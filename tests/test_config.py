"""配置与 HTTP 判定逻辑的离线测试（不联网、不起浏览器）。"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.config import AppConfig, Settings, Target  # noqa: E402
from ytmon.http_client import (HttpClient,  # noqa: E402
                               load_cached_cookies, save_cookies)

FIX = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- 假的 HTTP 响应

class FakeResponse:
    def __init__(self, text: str, status: int = 200, url: str = ""):
        self.text = text
        self.status_code = status
        self.url = url or "https://www.156yt.cn/pqs_revision/pages/jsp/voyQuery.jsp"
        self.content = text.encode("gb18030", "replace")


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.cookies = _FakeJar()
        self.headers = {}
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self._response

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self._response

    def clear(self):
        pass


class _FakeJar:
    def clear(self):
        pass

    def set(self, *a, **k):
        pass


WAF_HTML = ("<html><body><script>window._aMYJPelgGNdHBCHbZMSUTNCYVFeXkCWA = "
            "function(){return new Date()};var Qua7lMrVs39mmYCjI2s=function(){};"
            "</script></body></html>")

# 真实查询页的关键结构（表单字段 + 提交目标）
REAL_QUERY_HTML = (
    '<html><head><title>船期公众查询</title></head><body>'
    '<form name="form1" method="post" action="voyQuery.jsp?modify=query">'
    '<input type="text" id="etb_time" name="etb_time">'
    '<input type="text" id="voyage_code" name="voyage_code">'
    "</form></body></html>"
)

# 首页的查询链接不能视为查询结果。
PUBLIC_INFO_HTML = (
    '<html><head><title>公共信息服务</title></head><body>'
    '<a href="/pqs_revision/pages/jsp/voyQuery.jsp?loginVerifyCode=">'
    "船期公众查询(船名航次)</a></body></html>"
)


class TestCheckQuery(unittest.TestCase):
    def _client(self, response):
        client = HttpClient({"EO-Bot-Js-Token": "x"})
        client.session = FakeSession(response)
        return client

    def test_valid_uses_post(self):
        html = (FIX / "voy_result_ever.html").read_text("utf-8")
        client = self._client(FakeResponse(html))
        self.assertTrue(client.check_query().ok)
        self.assertEqual([method for method, _ in client.session.calls], ["POST"])

    def test_home_is_not_a_result(self):
        status = self._client(FakeResponse(PUBLIC_INFO_HTML)).check_query()
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, "expired")

    def test_waf_is_distinguished(self):
        status = self._client(FakeResponse(WAF_HTML)).check_query()
        self.assertEqual(status.reason, "waf")

    def test_rate_limit_is_distinguished(self):
        for code in (429, 503, 567):
            with self.subTest(code=code):
                status = self._client(FakeResponse("", code)).check_query()
                self.assertEqual(status.reason, "ratelimited")

    def test_http_error_is_query_failure(self):
        status = self._client(FakeResponse("", 500)).check_query()
        self.assertEqual(status.reason, "query")

    def test_network_error_propagates(self):
        import requests
        class Boom(FakeSession):
            def post(self, url, **kw):
                raise requests.ConnectionError("boom")
        client = self._client(FakeResponse(""))
        client.session = Boom(None)
        with self.assertRaises(requests.ConnectionError):
            client.check_query()


class TestConfig(unittest.TestCase):
    def test_load_normalises_short_form_targets(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "w.json"
            p.write_text(json.dumps({"targets": ["MSC IRINA"]}), "utf-8")
            cfg = AppConfig.load(p)
            self.assertEqual(len(cfg.targets), 1)
            self.assertEqual(cfg.targets[0].type, "ship")
            self.assertEqual(cfg.targets[0].value, "MSC IRINA")

    def test_roundtrip_preserves_targets(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "w.json"
            p.write_text(json.dumps({
                "targets": [
                    {"type": "ship", "value": "MSC IRINA", "label": "MSC IRINA"},
                    {"type": "voyage", "value": "GJ634W"},
                ],
                "settings": {"etb_back_days": 3},
            }), "utf-8")
            cfg = AppConfig.load(p)
            self.assertEqual(cfg.settings.etb_back_days, 3)
            cfg.save()
            again = AppConfig.load(p)
            self.assertEqual(len(again.targets), 2)
            self.assertEqual(again.targets[1].value, "GJ634W")
            self.assertEqual(again.settings.etb_back_days, 3)

    def test_unknown_settings_keys_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "w.json"
            p.write_text(json.dumps({"settings": {"future_option": 1}}), "utf-8")
            cfg = AppConfig.load(p)                # 不该抛
            self.assertFalse(hasattr(cfg.settings, "future_option"))

    def test_validate_catches_problems(self):
        cfg = AppConfig(targets=[
            Target("ship", "A"),                   # 太短
            Target("ship", "MSC IRINA"),
            Target("ship", "msc irina"),           # 与上一条重复（忽略大小写）
            Target("badtype", "XYZ"),
        ])
        errs = cfg.validate()
        joined = " | ".join(errs)
        self.assertIn("至少 2 个字符", joined)
        self.assertIn("重复", joined)
        self.assertIn("类型", joined)

    def test_valid_config_has_no_errors(self):
        cfg = AppConfig(targets=[Target("ship", "MSC IRINA"),
                                 Target("voyage", "GJ634W")])
        self.assertEqual(cfg.validate(), [])

    def test_enabled_targets_filters_blank_and_disabled(self):
        cfg = AppConfig(targets=[Target("ship", "A SHIP"),
                                 Target("ship", "", ""),
                                 Target("ship", "B SHIP", enabled=False)])
        self.assertEqual([t.value for t in cfg.enabled_targets], ["A SHIP"])

    def test_legacy_login_fields_are_ignored_and_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "w.json"
            path.write_text(json.dumps({
                "token": "LEGACY",
                "settings": {"precheck": True},
                "targets": ["MSC IRINA"],
            }), "utf-8")
            config = AppConfig.load(path)
            self.assertFalse(hasattr(config, "token"))
            self.assertFalse(hasattr(config.settings, "precheck"))
            config.save()
            saved = json.loads(path.read_text("utf-8"))
            self.assertNotIn("token", saved)
            self.assertNotIn("precheck", saved["settings"])


class TestCookieCache(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            f = str(pathlib.Path(d) / "c.json")
            save_cookies({"EO-Bot-Js-Token": "abc"}, f)
            self.assertEqual(load_cached_cookies(f)["EO-Bot-Js-Token"], "abc")

    def test_missing_eo_token_is_treated_as_no_cache(self):
        with tempfile.TemporaryDirectory() as d:
            f = str(pathlib.Path(d) / "c.json")
            save_cookies({"JSESSIONID": "only-this"}, f)
            self.assertIsNone(load_cached_cookies(f),
                              "没有 EO cookie 的缓存不可用")

    def test_expired_cache_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            f = str(pathlib.Path(d) / "c.json")
            save_cookies({"EO-Bot-Js-Token": "abc"}, f)
            self.assertIsNone(load_cached_cookies(f, max_age=-1))

    def test_corrupt_cache_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "c.json"
            f.write_text("{not json", "utf-8")
            self.assertIsNone(load_cached_cookies(str(f)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
