"""登录与 token 自动续期。

## 为什么是"两个会话"

侦察时实测到一个反直觉现象：
  * **干净会话** + token  → 正常进入「船期公众查询」           ✅
  * **已登录会话** + token → 被弹回「公共信息服务」首页        ❌

所以续期不能在一个会话里做完，必须拆成两步：

    会话 A：EO cookie + 账号登录  → 打开公共信息服务页 → 抠出新的 loginVerifyCode
    会话 B：只要 EO cookie，不带任何登录态 → 用新 token 查询

本模块负责会话 A，产出一个新 token；会话 B 由 http_client 负责。

## 凭证来源

优先环境变量 `YT_USER` / `YT_PASS`；也支持 Windows 凭据管理器（可选，供 GUI 用）。
密码绝不写日志、绝不落盘明文。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import requests

from .errors import AuthError
from .http_client import UA, _is_waf, _title

LOGIN_URL = "https://www.156yt.cn/passport/login!verify"
PUBLIC_INFO_URL = "https://www.156yt.cn/publicInfoService/index.action"
MEMBER_URL = "https://www.156yt.cn/member/index.action"

TOKEN_RE = re.compile(r"loginVerifyCode=([^\"'&\s<>]{6,300})")

CRED_TARGET = "ytmon:156yt.cn"


def extract_token_from_url(url: str) -> str | None:
    """从 redirectUrl 里取出 loginVerifyCode。

    实测登录成功响应形如：
        {"loginMessege":null,
         "redirectUrl":"https://www.156yt.cn/member/index.action?loginVerifyCode=xxx==",
         "result":1}
    也就是说 **token 直接就在登录响应里**，不必再去爬页面抠。
    """
    if not url:
        return None
    m = re.search(r"[?&]loginVerifyCode=([^&\s]+)", url)
    if not m:
        return None
    from urllib.parse import unquote
    return unquote(m.group(1)) or None


def mask(user: str) -> str:
    """用户名脱敏，用于日志。"""
    if not user:
        return "(空)"
    if len(user) <= 3:
        return user[0] + "*" * (len(user) - 1)
    return user[:2] + "*" * (len(user) - 3) + user[-1]


# ------------------------------------------------------------------ 凭证来源


def load_credentials() -> tuple[str, str] | None:
    """依次尝试：环境变量 -> Windows 凭据管理器。"""
    u, p = os.environ.get("YT_USER"), os.environ.get("YT_PASS")
    if u and p:
        return u.strip(), p
    return _load_from_credential_manager()


def _load_from_credential_manager() -> tuple[str, str] | None:
    """从 Windows 凭据管理器读（pywin32 已装；读不到就返回 None）。"""
    try:
        import win32cred
        cred = win32cred.CredRead(CRED_TARGET, win32cred.CRED_TYPE_GENERIC)
        blob = cred.get("CredentialBlob", b"")
        if isinstance(blob, bytes):
            blob = blob.decode("utf-16-le", "ignore").rstrip("\x00")
        data = json.loads(blob)
        return data["username"], data["password"]
    except Exception:                       # noqa: BLE001  任何异常都视为"没有"
        return None


def delete_credentials() -> bool:
    """从 Windows 凭据管理器删除本项目保存的凭证。

    实测证明公众船期查询**不需要账号**，因此这些凭证通常根本不必存在。
    不需要时就该删掉 —— 少一份凭证就少一份风险。
    """
    try:
        import win32cred
        win32cred.CredDelete(CRED_TARGET, win32cred.CRED_TYPE_GENERIC, 0)
        return True
    except Exception:                       # noqa: BLE001  包括"本来就不存在"
        return False


def save_credentials(username: str, password: str,
                     use_credential_manager: bool = True) -> bool:
    """保存凭证到 Windows 凭据管理器。成功返回 True。

    注意：pywin32 的 CredWrite 要求 CredentialBlob 是 **str**
    （它内部自己转 UTF-16）；传 bytes 会报
    "Objects of type 'bytes' can not be converted to Unicode"。
    """
    if not use_credential_manager:
        return False
    try:
        import win32cred
        win32cred.CredWrite({
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": CRED_TARGET,
            "UserName": username,
            "CredentialBlob": json.dumps(
                {"username": username, "password": password}, ensure_ascii=False),
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            "Comment": "盐田船期监控登录凭证",
        }, 0)
        return True
    except Exception:                       # noqa: BLE001
        return False


# ------------------------------------------------------------------ 结果


@dataclass
class LoginResult:
    ok: bool
    detail: str
    http_status: int = 0
    raw_snippet: str = ""
    redirect_to: str = ""
    token: str | None = None          # 登录响应里直接带回的新 token

    def describe(self) -> str:
        return f"{'成功' if self.ok else '失败'}：{self.detail} [HTTP {self.http_status}]"


@dataclass
class RenewResult:
    ok: bool
    token: str | None
    detail: str
    login: LoginResult | None = None


# ------------------------------------------------------------------ 认证器


class Authenticator:
    """用已有 cookie 的会话做登录与 token 抠取。"""

    def __init__(self, session: requests.Session, timeout: float = 30.0):
        self.session = session
        self.timeout = timeout

    @classmethod
    def from_cookies(cls, cookies: dict[str, str], timeout: float = 30.0) -> "Authenticator":
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.156yt.cn/passport/",
        })
        for k, v in cookies.items():
            s.cookies.set(k, v, domain=".156yt.cn")
        return cls(s, timeout)

    # -------------------------------------------------------------- 登录

    def login(self, username: str, password: str) -> LoginResult:
        """POST /passport/login!verify。兼容 JSON 与 HTML 两种响应。"""
        if not username or not password:
            return LoginResult(False, "用户名或密码为空")

        payload = {
            "j_username": username.strip().lower(),   # 页面 JS 会把用户名转小写
            "j_password": password,
            "_by_ajax": "",
        }
        try:
            r = self.session.post(
                LOGIN_URL, data=payload, timeout=self.timeout,
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                         "Referer": "https://www.156yt.cn/passport/"},
            )
        except requests.RequestException as e:
            return LoginResult(False, f"网络异常：{type(e).__name__}: {e}")

        text = r.text
        snippet = re.sub(r"\s+", " ", text)[:300]

        if _is_waf(text):
            return LoginResult(False, "被 EdgeOne 挑战拦截，需先重新引导 cookie",
                               r.status_code, snippet)

        # 1) JSON：站点扫码登录用 d.result==1，普通登录大概率同构
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            result = data.get("result", data.get("success", data.get("code")))
            msg = data.get("message") or data.get("msg") or data.get("loginMessege")
            redirect = data.get("redirectUrl") or data.get("redirectURL") or ""
            token = extract_token_from_url(redirect)

            if result in (1, "1", True, "true", "success"):
                detail = "登录成功"
                if token:
                    detail += f"，响应中直接带回新 token（长度 {len(token)}）"
                else:
                    detail += "，但响应里没有 redirectUrl/token，将退回页面抠取"
                return LoginResult(True, detail, r.status_code, snippet,
                                   redirect_to=redirect, token=token)
            if result in (0, "0"):
                return LoginResult(True, f"登录接口返回 code=0（{msg}）", r.status_code,
                                   snippet, redirect_to=redirect, token=token)
            return LoginResult(False, f"登录被拒：{msg or result}", r.status_code, snippet,
                               redirect_to=redirect)

        # 2) HTML/重定向：看页面登录态
        state = self.check_logged_in()
        if state:
            return LoginResult(True, "登录后页面显示已登录", r.status_code, snippet)
        return LoginResult(False, f"无法识别登录结果（title={_title(text)!r}）",
                           r.status_code, snippet)

    # ---------------------------------------------------------- 登录态判定

    def check_logged_in(self, url: str = PUBLIC_INFO_URL) -> bool:
        try:
            r = self.session.get(url, timeout=self.timeout)
        except requests.RequestException:
            return False
        return _is_logged_in_html(r.text)

    # ---------------------------------------------------------- 抠 token

    def harvest_token(self, url: str = PUBLIC_INFO_URL) -> str | None:
        """从页面里抠出服务端新签发的 loginVerifyCode。"""
        try:
            r = self.session.get(url, timeout=self.timeout)
        except requests.RequestException:
            return None
        tokens = [t for t in TOKEN_RE.findall(r.text) if t.strip()]
        if not tokens:
            return None
        # 同一页面里所有链接应带同一个 token；取出现次数最多的
        return max(set(tokens), key=tokens.count)

    # ---------------------------------------------------------- 一步到位

    def renew(self, username: str, password: str) -> RenewResult:
        """登录并取得新 token。

        首选：登录响应里的 redirectUrl 直接带回 token（实测如此，一步到位）。
        兜底：登录后打开「公共信息服务」页面，从链接里抠 loginVerifyCode。
        """
        lr = self.login(username, password)
        if not lr.ok:
            return RenewResult(False, None, lr.detail, lr)

        if lr.token:
            return RenewResult(True, lr.token,
                               f"续期成功（登录响应直出），token 长度 {len(lr.token)}，"
                               f"尾部 ...{lr.token[-16:]}", lr)

        # 兜底路径
        token = self.harvest_token()
        if not token:
            return RenewResult(False, None,
                               "登录成功但既没有 redirectUrl，页面上也没找到 loginVerifyCode"
                               "（站点可能改了协议）", lr)
        return RenewResult(True, token,
                           f"续期成功（页面抠取），token 长度 {len(token)}，"
                           f"尾部 ...{token[-16:]}", lr)


def _is_logged_in_html(text: str) -> bool:
    """页面里 isLogin 的值是 true 即为已登录。"""
    m = re.search(r'value="(true|false)"\s+id="isLogin"', text)
    if m:
        return m.group(1) == "true"
    return "退出" in text and "欢迎您" in text
