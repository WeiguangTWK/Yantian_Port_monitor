"""同步 HTTP 查询及浏览器 Cookie 引导。公众查询无需账号或查询令牌。"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
import time
from dataclasses import dataclass, field

import requests

from .errors import QueryError, RateLimited, SessionExpired
from .parse import Result, is_result_page, parse_result

BASE = "https://www.156yt.cn/pqs_revision/pages/jsp/"
VOY_PAGE = BASE + "voyQuery.jsp"
VOY_POST = BASE + "voyQuery.jsp?modify=query"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

WANTED_COOKIES = ("EO-Bot-Js-Token", "JSESSIONID")

# EdgeOne 挑战页的特征串（混淆 JS 里的变量名）
_WAF_MARKERS = ("Qua7lMrVs39mmYCjI2s", "_aMYJPelgGNdHBCHbZMSUTNCYVFeXkCWA")

DEFAULT_COOKIE_CACHE = ".cache/cookies.json"
# Cookie 缓存超过此时长后重新引导
COOKIE_MAX_AGE_SECONDS = 20 * 60

# 站点限流时返回的**非标准**状态码（实测）。标准的是 429，也一并认。
RATE_LIMIT_STATUS = 567


@dataclass
class QueryStatus:
    """一次 POST 查询探针的结果。"""

    ok: bool
    reason: str                 # ok | expired | waf | ratelimited | query
    detail: str = ""
    status_code: int = 0
    elapsed_ms: int = 0
    checked_at: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    def describe(self) -> str:
        tag = "查询正常" if self.ok else f"查询失败({self.reason})"
        return f"{tag} {self.detail} [{self.status_code} {self.elapsed_ms}ms]"


def _is_waf(text: str) -> bool:
    return any(m in text for m in _WAF_MARKERS)


# ------------------------------------------------------------------ cookie 引导


async def _bootstrap_async(profile_dir: str, edge_path: str | None,
                           headless: bool) -> dict[str, str]:
    from .cdp import Browser

    async with Browser(edge_path, profile_dir, headless=headless) as b:
        assert b.cdp
        await b.cdp.goto(VOY_PAGE, timeout=60)
        cookies = await b.cdp.send("Network.getAllCookies")
        out: dict[str, str] = {}
        for c in cookies.get("cookies", []):
            if "156yt" in c["domain"] and c["name"] in WANTED_COOKIES:
                out[c["name"]] = c["value"]
        if "EO-Bot-Js-Token" not in out:
            raise RuntimeError("引导失败：没拿到 EO-Bot-Js-Token（EdgeOne 挑战可能变了）")
        return out


def _cache_path(cache_file: str) -> pathlib.Path:
    return pathlib.Path(cache_file)


def load_cached_cookies(cache_file: str = DEFAULT_COOKIE_CACHE,
                        max_age: int = COOKIE_MAX_AGE_SECONDS) -> dict[str, str] | None:
    p = _cache_path(cache_file)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    age = time.time() - float(data.get("saved_at", 0))
    if age > max_age:
        return None
    cookies = data.get("cookies") or {}
    return cookies if "EO-Bot-Js-Token" in cookies else None


def save_cookies(cookies: dict[str, str], cache_file: str = DEFAULT_COOKIE_CACHE) -> None:
    p = _cache_path(cache_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"saved_at": time.time(), "cookies": cookies},
                            ensure_ascii=False, indent=2), "utf-8")


def bootstrap_cookies(*, profile_dir: str = ".browser_profile",
                      edge_path: str | None = None, headless: bool = True,
                      cache_file: str = DEFAULT_COOKIE_CACHE) -> dict[str, str]:
    """启动浏览器获取查询 Cookie，并写入缓存。"""
    cookies = asyncio.run(_bootstrap_async(profile_dir, edge_path, headless))
    save_cookies(cookies, cache_file)
    return cookies


# ------------------------------------------------------------------ HTTP 客户端


class HttpClient:
    """带自动重新引导能力的同步查询客户端。"""

    def __init__(self, cookies: dict[str, str] | None = None, *,
                 timeout: float = 25.0, cache_file: str = DEFAULT_COOKIE_CACHE,
                 boot: dict | None = None):
        self.timeout = timeout
        self.cache_file = cache_file
        self.boot = boot or {}          # 重新引导所需的参数
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        self.set_cookies(cookies or {})

    # ---------------------------------------------------------- cookie

    def set_cookies(self, cookies: dict[str, str]) -> None:
        self.session.cookies.clear()
        for k, v in cookies.items():
            self.session.cookies.set(k, v, domain=".156yt.cn")

    def rebootstrap(self) -> dict[str, str]:
        if not self.boot:
            raise RuntimeError("没有引导参数，无法重新获取 cookie")
        cookies = bootstrap_cookies(**self.boot)
        self.set_cookies(cookies)
        return cookies

    @classmethod
    def create(cls, *, prefer_cache: bool = True, **kwargs) -> "HttpClient":
        boot = {
            "profile_dir": kwargs.pop("profile_dir", ".browser_profile"),
            "edge_path": kwargs.pop("edge_path", None),
            "headless": kwargs.pop("headless", True),
            "cache_file": kwargs.pop("cache_file", DEFAULT_COOKIE_CACHE),
        }
        cookies = load_cached_cookies(boot["cache_file"]) if prefer_cache else None
        client = cls(cookies or {}, cache_file=boot["cache_file"], boot=boot, **kwargs)
        if not cookies:
            client.rebootstrap()
        return client

    # ---------------------------------------------------------- 体检

    def check_query(self, probe_etb: str | None = None,
                     probe_ship: str = "MSC") -> QueryStatus:
        """通过实际 POST 查询检查链路，不使用 GET 预检查。"""
        etb = probe_etb or (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
        t0 = time.monotonic()
        try:
            result = self.query_page(etb_time=etb, ship_name=probe_ship)
        except SessionExpired as e:
            msg = str(e)
            reason = "waf" if "EdgeOne" in msg else "expired"
            return QueryStatus(False, reason, f"查询被拒：{e}",
                               elapsed_ms=int((time.monotonic() - t0) * 1000))
        except RateLimited as e:
            return QueryStatus(False, "ratelimited", f"被限流：{e}",
                               elapsed_ms=int((time.monotonic() - t0) * 1000))
        except QueryError as e:
            return QueryStatus(False, "query", f"查询失败：{e}",
                               elapsed_ms=int((time.monotonic() - t0) * 1000))

        ms = int((time.monotonic() - t0) * 1000)
        return QueryStatus(True, "ok",
                           f"查询链路可用（探测「{probe_ship}」命中 {result.total} 条）",
                           200, ms)

    # ---------------------------------------------------------- 查询

    def query_page(self, *, etb_time: str, ship_name: str = "",
                   voyage_code: str = "", page: int = 1) -> Result:
        fields = {
            "pageCurrent": str(page), "etb_time": etb_time,
            "ship_name": ship_name, "voyage_code": voyage_code, "Submit1": "查询",
        }
        r = self.session.post(VOY_POST, data=fields, timeout=self.timeout,
                              headers={"Referer": VOY_PAGE})

        # 先看状态码再解析。限流必须在这里挑出来 —— 它的响应体不是结果页，
        # 如果混在下面按"会话失效"处理，就会去重新引导 cookie（起一次浏览器），
        # 那是在被限流的时候又加一串请求，只会更糟。
        if r.status_code == RATE_LIMIT_STATUS or r.status_code == 429:
            raise RateLimited(f"站点限流（HTTP {r.status_code}），本轮应退避等待")
        if r.status_code == 503:
            raise RateLimited(f"站点暂不可用（HTTP 503），本轮应退避等待")
        if r.status_code != 200:
            raise QueryError(f"查询返回意外状态码 HTTP {r.status_code}")

        text = r.content.decode("gb18030", "replace")
        if _is_waf(text):
            raise SessionExpired("EdgeOne 挑战拦截，需要重新引导 cookie")
        if not is_result_page(text):
            raise SessionExpired("返回内容不是船期结果页，会话可能已失效")
        return parse_result(text)

    def query_all(self, *, etb_time: str, ship_name: str = "",
                  voyage_code: str = "", max_pages: int = 5) -> Result:
        first = self.query_page(etb_time=etb_time, ship_name=ship_name,
                                voyage_code=voyage_code, page=1)
        rows = list(first.rows)
        pages = min(first.pages or 1, max_pages)
        for p in range(2, pages + 1):
            more = self.query_page(etb_time=etb_time, ship_name=ship_name,
                                   voyage_code=voyage_code, page=p)
            if not more.rows:
                break
            rows.extend(more.rows)
        return Result(rows=rows, total=first.total, page=1, pages=first.pages, raw=first.raw)


def is_fatal_error(exc: BaseException) -> bool:
    """这个异常是否意味着"换下一个查询目标也一定失败"。

    用来在一轮里跳过剩余目标 —— 站点异常或限流时，继续查下去
    只是在给自己加压。放在这里是为了把 requests 的依赖收在一个模块里。
    """
    if isinstance(exc, (RateLimited, SessionExpired)):
        # 限流：越查越糟。会话坏了：下一个目标用同一个会话，一样过不去，
        # 而且会再触发一次浏览器引导（很贵）。
        return True
    return isinstance(exc, requests.RequestException)


__all__ = [
    "HttpClient", "QueryStatus", "SessionExpired", "QueryError",
    "RateLimited", "RATE_LIMIT_STATUS", "is_fatal_error",
    "bootstrap_cookies", "load_cached_cookies", "save_cookies",
    "VOY_PAGE", "VOY_POST", "BASE", "UA",
]
