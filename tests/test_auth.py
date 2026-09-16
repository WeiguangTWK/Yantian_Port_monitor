"""登录/续期的离线测试（不联网）。

这里的用例直接来自 2026-09-14 的真实登录响应，
所以它们同时是"站点协议改版"的守卫。
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest
from urllib.parse import quote

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.auth import Authenticator, extract_token_from_url   # noqa: E402
from ytmon.http_client import token_url                        # noqa: E402

# 结构完全仿照实测响应（长度 76、含 '/'、以 '==' 结尾、尾部是 base64 会员 ID），
# 但**内容全部是假的**。真实的 loginVerifyCode 是有效凭证，绝不能进版本库。
FAKE_TOKEN = ("FAKE0TOKEN1abcdefghijklmnopqrstuvwxyzABC/EFGHIJKLMNOPQRSTU0123=="
              "OTk5OTk5OQ==")
FAKE_ID_PART = "OTk5OTk5OQ=="
REAL_REDIRECT = ("https://www.156yt.cn/member/index.action?"
                 "loginVerifyCode=" + FAKE_TOKEN)
REAL_TOKEN = FAKE_TOKEN

LOGIN_JSON = json.dumps({
    "loginMessege": None,
    "redirectUrl": REAL_REDIRECT,
    "result": 1,
}, ensure_ascii=False)

BAD_LOGIN_JSON = json.dumps({
    "loginMessege": "用户名或密码错误",
    "redirectUrl": None,
    "result": -1,
}, ensure_ascii=False)


class FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status
        self.content = text.encode("utf-8")
        self.url = "https://www.156yt.cn/passport/login!verify"


class FakeSession:
    def __init__(self, responses):
        # responses: list，按 POST 调用次序取；用完后复用最后一个
        self.responses = list(responses)
        self.headers = {}
        self.posts: list[dict] = []

    def post(self, url, data=None, **kw):
        self.posts.append({"url": url, "data": data or {}})
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def get(self, url, **kw):
        return FakeResponse("<html></html>")


class TestExtractToken(unittest.TestCase):
    def test_real_redirect_url(self):
        self.assertEqual(extract_token_from_url(REAL_REDIRECT), REAL_TOKEN)

    def test_token_contains_slash(self):
        # 这是关键：token 是 base64，含 '/' 甚至可能含 '+'
        self.assertIn("/", REAL_TOKEN)
        self.assertTrue(REAL_TOKEN.endswith(FAKE_ID_PART))
        self.assertEqual(len(REAL_TOKEN), 76, "真实 token 长度为 76，测试样本应保持一致")

    def test_url_encoded_token_is_decoded(self):
        url = "https://x/y?loginVerifyCode=" + quote(REAL_TOKEN, safe="")
        self.assertEqual(extract_token_from_url(url), REAL_TOKEN)

    def test_absent_or_empty(self):
        self.assertIsNone(extract_token_from_url(""))
        self.assertIsNone(extract_token_from_url("https://x/y?other=1"))
        self.assertIsNone(extract_token_from_url(None))       # type: ignore[arg-type]


class TestTokenUrl(unittest.TestCase):
    def test_plus_is_encoded(self):
        # '+' 在 query string 里会被服务端当成空格 —— 必须编码，否则 token 静默失效
        url = token_url("a+b/c==")
        self.assertIn("%2B", url)
        self.assertIn("%2F", url)
        self.assertNotIn("a+b", url)

    def test_plain_token_still_fine(self):
        self.assertIn("loginVerifyCode=ABC", token_url("ABC"))


class TestLogin(unittest.TestCase):
    def test_success_returns_token_directly(self):
        """实测：登录响应里直接带 token，不需要再爬页面。"""
        auth = Authenticator(FakeSession([FakeResponse(LOGIN_JSON)]))
        r = auth.login("someuser", "somepass")
        self.assertTrue(r.ok)
        self.assertEqual(r.token, REAL_TOKEN)
        self.assertIn("token", r.detail)

    def test_password_is_lowercased_username_and_sent(self):
        s = FakeSession([FakeResponse(LOGIN_JSON)])
        Authenticator(s).login("MixedCase", "pw")
        data = s.posts[0]["data"]
        self.assertEqual(data["j_username"], "mixedcase", "页面 JS 会转小写，必须一致")
        self.assertEqual(data["j_password"], "pw")

    def test_rejected_login_has_no_token(self):
        auth = Authenticator(FakeSession([FakeResponse(BAD_LOGIN_JSON)]))
        r = auth.login("u", "p")
        self.assertFalse(r.ok)
        self.assertIsNone(r.token)

    def test_waf_challenge_is_reported(self):
        auth = Authenticator(FakeSession([FakeResponse(
            "<script>window._aMYJPelgGNdHBCHbZMSUTNCYVFeXkCWA=1;"
            "var Qua7lMrVs39mmYCjI2s=1;</script>")]))
        r = auth.login("u", "p")
        self.assertFalse(r.ok)
        self.assertIn("EdgeOne", r.detail)

    def test_network_error_does_not_raise(self):
        import requests

        class Boom(FakeSession):
            def post(self, url, data=None, **kw):
                raise requests.ConnectionError("down")

        r = Authenticator(Boom([])).login("u", "p")
        self.assertFalse(r.ok)
        self.assertIn("网络异常", r.detail)

    def test_empty_credentials_rejected_without_request(self):
        s = FakeSession([FakeResponse(LOGIN_JSON)])
        r = Authenticator(s).login("", "")
        self.assertFalse(r.ok)
        self.assertEqual(s.posts, [], "不该为空凭证发请求")

    def test_snippet_never_contains_password(self):
        auth = Authenticator(FakeSession([FakeResponse(LOGIN_JSON)]))
        r = auth.login("u", "SuperSecret123")
        self.assertNotIn("SuperSecret123", r.raw_snippet)
        self.assertNotIn("SuperSecret123", r.describe())


class TestRenew(unittest.TestCase):
    def test_renew_uses_token_from_login_response(self):
        """首选路径：不应再去 GET 页面抠 token。"""
        s = FakeSession([FakeResponse(LOGIN_JSON)])
        result = Authenticator(s).renew("u", "p")
        self.assertTrue(result.ok)
        self.assertEqual(result.token, REAL_TOKEN)
        self.assertIn("直出", result.detail)

    def test_renew_falls_back_to_page_scrape(self):
        """站点若哪天不再返回 redirectUrl，应退回页面抠取而不是直接失败。"""
        html = ('<a href="/pqs_revision/pages/jsp/voyQuery.jsp?'
                'loginVerifyCode=FALLBACKTOKEN123456">船期公众查询</a>')

        class S(FakeSession):
            def get(self, url, **kw):
                return FakeResponse(html)

        result = Authenticator(S([FakeResponse('{"result":1}')])).renew("u", "p")
        self.assertTrue(result.ok)
        self.assertEqual(result.token, "FALLBACKTOKEN123456")

    def test_renew_reports_failure_clearly(self):
        result = Authenticator(FakeSession([FakeResponse(BAD_LOGIN_JSON)])).renew("u", "p")
        self.assertFalse(result.ok)
        self.assertIsNone(result.token)
        self.assertIn("登录被拒", result.detail)
        self.assertIn("用户名或密码错误", result.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
