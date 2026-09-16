"""配置与 HTTP 判定逻辑的离线测试（不联网、不起浏览器）。"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.auth import _is_logged_in_html, mask          # noqa: E402
from ytmon.config import AppConfig, Settings, Target, resolve_token  # noqa: E402
from ytmon.errors import TokenError                      # noqa: E402
from ytmon.http_client import (HttpClient, TokenStatus,  # noqa: E402
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

# 「公共信息服务」首页 —— 注意它**也含有**「船期公众查询」这几个字（作为链接文本），
# 这正是 check_token 原先误判的根源。
PUBLIC_INFO_HTML = (
    '<html><head><title>公共信息服务</title></head><body>'
    '<a href="/pqs_revision/pages/jsp/voyQuery.jsp?loginVerifyCode=">'
    "船期公众查询(船名航次)</a></body></html>"
)


class TestCheckToken(unittest.TestCase):
    """check_token 的分类是整套监控的判据，必须有测试守住。"""

    def _client(self, response) -> HttpClient:
        c = HttpClient("dummy-token", {"EO-Bot-Js-Token": "x"})
        c.session = FakeSession(response)          # type: ignore[assignment]
        return c

    def test_valid(self):
        st = self._client(FakeResponse(REAL_QUERY_HTML)).check_token()
        self.assertTrue(st.ok)
        self.assertEqual(st.reason, "ok")
        self.assertFalse(st.expired)

    def test_index_page_is_not_mistaken_for_query_page(self):
        """回归守卫：首页里也有「船期公众查询」这个链接文本。

        旧实现用 `'船期公众查询' in text` 判断，于是**被弹回首页也会报"有效"**。
        现在改用查询表单的特有字段，必须判为 expired。
        """
        st = self._client(FakeResponse(
            PUBLIC_INFO_HTML,
            url="https://www.156yt.cn/pqs_revision/pages/jsp/voyQuery.jsp")).check_token()
        self.assertFalse(st.ok, "首页含'船期公众查询'字样，但绝不能判为有效")
        self.assertEqual(st.reason, "expired")

    def test_waf_is_not_token_expiry(self):
        # 关键：cookie 过期导致的 WAF 拦截 ≠ token 过期。
        # 若混为一谈，就会把"重新引导一下就好"误报成"token 死了"。
        st = self._client(FakeResponse(WAF_HTML)).check_token()
        self.assertFalse(st.ok)
        self.assertEqual(st.reason, "waf")
        self.assertNotEqual(st.reason, "expired")

    def test_bounced_to_home_is_expired(self):
        st = self._client(FakeResponse(
            PUBLIC_INFO_HTML,
            url="https://www.156yt.cn/publicInfoService/index.action")).check_token()
        self.assertFalse(st.ok)
        self.assertEqual(st.reason, "expired")

    def test_network_error_is_not_expiry(self):
        class Boom(FakeSession):
            def get(self, url, **kw):
                import requests
                raise requests.ConnectionError("boom")

        c = HttpClient("dummy", {"EO-Bot-Js-Token": "x"})
        c.session = Boom(None)                     # type: ignore[assignment]
        st = c.check_token()
        self.assertFalse(st.ok)
        self.assertEqual(st.reason, "network")
        self.assertFalse(st.expired, "网络错误绝不能被当成 token 过期")

    def test_describe_never_leaks_token(self):
        c = self._client(FakeResponse(REAL_QUERY_HTML))
        text = c.check_token().describe()
        self.assertNotIn("dummy-token", text)


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

    def test_resolve_token_priority(self):
        cfg = AppConfig(token="from-config")
        old = os.environ.pop("YT_TOKEN", None)
        try:
            self.assertEqual(resolve_token(cfg, "from-cli"), "from-cli")
            self.assertEqual(resolve_token(cfg, None), "from-config")
            os.environ["YT_TOKEN"] = "from-env"
            self.assertEqual(resolve_token(cfg, None), "from-env")
            os.environ["YT_TOKEN"] = "   "
            self.assertEqual(resolve_token(cfg, None), "from-config")
        finally:
            os.environ.pop("YT_TOKEN", None)
            if old is not None:
                os.environ["YT_TOKEN"] = old

    def test_resolve_token_optional_by_default(self):
        """token 现在是可选的 —— 实测公众查询不需要它（连参数都不带也能查）。"""
        old = os.environ.pop("YT_TOKEN", None)
        try:
            self.assertEqual(resolve_token(AppConfig(), None), "")
        finally:
            if old is not None:
                os.environ["YT_TOKEN"] = old

    def test_resolve_token_raises_only_when_required(self):
        old = os.environ.pop("YT_TOKEN", None)
        try:
            with self.assertRaises(TokenError):
                resolve_token(AppConfig(), None, required=True)
        finally:
            if old is not None:
                os.environ["YT_TOKEN"] = old


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


class TestAuthHelpers(unittest.TestCase):
    def test_detect_logged_in(self):
        self.assertTrue(_is_logged_in_html('<input value="true" id="isLogin">'))
        self.assertFalse(_is_logged_in_html('<input value="false" id="isLogin">'))
        self.assertFalse(_is_logged_in_html("<html>nothing</html>"))

    def test_mask_hides_username(self):
        self.assertNotIn("secretuser", mask("secretuser"))
        self.assertEqual(mask(""), "(空)")
        self.assertTrue(mask("ab").startswith("a"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
