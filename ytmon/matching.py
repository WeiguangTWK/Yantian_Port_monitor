"""监控目标与查询结果的匹配规则。

单独成模块，是因为它是"业务正确性"的核心：
站点 `ship_name` 是**子串匹配**，查 `EVER` 会连 `LEVERKUSEN EXPRESS` 一起返回。
所以必须区分"精确命中"与"模糊命中"，并把模糊命中明确暴露给用户，
否则一个不够具体的船名会在监控里悄悄变成一堆无关的船。
"""

from __future__ import annotations

from .parse import Voyage, norm

MATCH_EXACT = "exact"
MATCH_FUZZY = "fuzzy"
MATCH_NONE = "none"


def match_rows(rows: list[Voyage], ttype: str, value: str) -> tuple[list[Voyage], str]:
    """返回 (命中的行, 匹配方式)。"""
    want = norm(value)
    if not want:
        return [], MATCH_NONE

    if ttype == "voyage":
        exact = [r for r in rows if norm(r.voyage_code) == want]
        return (exact, MATCH_EXACT) if exact else ([], MATCH_NONE)

    exact = [r for r in rows if norm(r.ship_name) == want]
    if exact:
        return exact, MATCH_EXACT
    fuzzy = [r for r in rows if want in norm(r.ship_name)]
    return (fuzzy, MATCH_FUZZY) if fuzzy else ([], MATCH_NONE)
