"""盐田码头（156yt）船期监控。

针对特定船名或码头航次定时查询，比对 ETB/ETD 变化并告警。

分层：
    browser_find.py 浏览器定位（**纯标准库**，裸机器上也能用）
    cdp.py          异步 CDP 原语（驱动 Chromium 系浏览器过 EdgeOne 挑战）
    http_client.py  同步 HTTP：cookie 引导 + token 体检 + 查询（日常走这条）
    auth.py         登录与 token 自动续期
    parse.py        结果页解析
    matching.py     船名/航次匹配规则
    store.py        状态存储与变化比对
    config.py       配置模型（GUI 可直接绑定）
    notify.py       告警出口（webhook / 钉钉 / 企微 / 飞书 / 邮件，均无新依赖）
    service.py      UI 无关的编排层 —— GUI 基座
    cli.py          命令行呈现层
    errors.py       统一异常

## 为什么这里的导入是"惰性"的

本包必须能在**还没装任何第三方依赖**的机器上被部分使用：
Win7 机器上第一次自检时，用户往往只想问"这台机器有没有可用浏览器"，
而此时 requests / aiohttp 都还没装。

如果本文件在顶部 eager import 各子模块，那么连
`from ytmon.browser_find import find_browser` 都会连带拉进 requests，
直接报 ModuleNotFoundError —— 这与 browser_find 的设计意图完全矛盾。

所以这里只做惰性转发（PEP 562），真正的导入推迟到属性被访问时。
"""

from __future__ import annotations

import importlib

__version__ = "0.2.0"

# 公开名字 -> (子模块, 模块内属性名)
_LAZY: dict[str, tuple[str, str]] = {
    # config
    "AppConfig": (".config", "AppConfig"),
    "Settings": (".config", "Settings"),
    "Target": (".config", "Target"),
    "NotifyChannel": (".config", "NotifyChannel"),
    "resolve_token": (".config", "resolve_token"),
    # errors
    "YtmonError": (".errors", "YtmonError"),
    "TokenError": (".errors", "TokenError"),
    "SessionExpired": (".errors", "SessionExpired"),
    "QueryError": (".errors", "QueryError"),
    "AuthError": (".errors", "AuthError"),
    # http / auth
    "HttpClient": (".http_client", "HttpClient"),
    "TokenStatus": (".http_client", "TokenStatus"),
    "bootstrap_cookies": (".http_client", "bootstrap_cookies"),
    "Authenticator": (".auth", "Authenticator"),
    # parse / matching
    "Voyage": (".parse", "Voyage"),
    "parse_result": (".parse", "parse_result"),
    "match_rows": (".matching", "match_rows"),
    # store
    "StateStore": (".store", "StateStore"),
    "compare": (".store", "compare"),
    # service
    "MonitorService": (".service", "MonitorService"),
    "CycleReport": (".service", "CycleReport"),
    "TargetOutcome": (".service", "TargetOutcome"),
    # notify
    "Notifier": (".notify", "Notifier"),
    "AlertMessage": (".notify", "AlertMessage"),
    "build_alert": (".notify", "build_alert"),
    # browser（纯标准库，永远可用）
    "find_browser": (".browser_find", "find_browser"),
}

__all__ = sorted(_LAZY)


def __getattr__(name: str):
    """按需导入 —— 只有真正用到某个名字时才加载对应子模块。"""
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    module = importlib.import_module(module_name, __name__)
    value = getattr(module, attr)
    globals()[name] = value          # 缓存，后续访问不再走 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(list(globals()) + __all__))
