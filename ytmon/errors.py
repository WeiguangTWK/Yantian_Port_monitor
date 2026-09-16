"""统一异常类型。

放在单独模块，避免 cli / service / http_client 之间循环 import。
"""

from __future__ import annotations


class YtmonError(RuntimeError):
    """本项目所有异常的基类。"""


class SessionExpired(YtmonError):
    """cookie/会话失效，需要重新引导。"""


class QueryError(YtmonError):
    """查询本身失败（网络、状态码、返回内容异常）。"""


class RateLimited(QueryError):
    """站点限流或暂不可用，走退避重试，不重新引导浏览器。"""
