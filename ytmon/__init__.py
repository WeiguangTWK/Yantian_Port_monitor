"""盐田船期监控。惰性导入允许无第三方依赖的机器使用浏览器定位与基础自检。"""

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
    # errors
    "YtmonError": (".errors", "YtmonError"),
    "SessionExpired": (".errors", "SessionExpired"),
    "QueryError": (".errors", "QueryError"),
    # http / auth
    "HttpClient": (".http_client", "HttpClient"),
    "QueryStatus": (".http_client", "QueryStatus"),
    "bootstrap_cookies": (".http_client", "bootstrap_cookies"),
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
