"""统一异常类型。

放在单独模块，避免 cli / service / http_client 之间循环 import。
"""

from __future__ import annotations


class YtmonError(RuntimeError):
    """本项目所有异常的基类。"""


class TokenError(YtmonError):
    """token 缺失、失效或被拒绝。"""


class SessionExpired(TokenError):
    """cookie/会话失效，需要重新引导。"""


class QueryError(YtmonError):
    """查询本身失败（网络、状态码、返回内容异常）。"""


class RateLimited(QueryError):
    """站点限流（实测返回非标准状态码 567，也可能是 429/503）。

    **必须和"会话失效"分开。** 两者的正确反应完全相反：

        会话失效 → 重新引导 cookie（起一次浏览器）是对的
        被限流   → 重新引导毫无用处，反而**雪上加霜**：
                   起浏览器本身又是一串请求，只会把自己推得更深

    所以限流要单独成一类，走"退避等待"而不是"自愈重试"。
    """


class AuthError(YtmonError):
    """登录或 token 续期失败。"""
