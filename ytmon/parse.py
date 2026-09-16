"""解析「船期公众查询」结果页。

页面实测特征（2026-09-14）：
  * Content-Type: text/html;charset=gb2312  → 一律按 gb18030 解码
  * 6 列：码头航次 / 船名 / 闸口 / 预计停靠（ETB）/ 预计离港（ETD）/ 船代
  * 日期两种格式：'YYYY-MM-DD' 与 'YYYY-MM-DD HH:MM'
  * 分页元数据在 <div class="record"><div class="total"><span>总记录数：N</span>...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime

ENCODING = "gb18030"

EXPECTED_TITLE = "船期公众查询"

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _text(cell_html: str) -> str:
    return _WS.sub(" ", _TAG.sub("", cell_html)).strip()


def parse_dt(value: str) -> datetime | None:
    """解析页面上的日期。两种格式都支持；解析不出来返回 None。"""
    v = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class Voyage:
    voyage_code: str          # 码头航次
    ship_name: str            # 船名
    gate: str                 # 闸口
    etb_raw: str              # 预计停靠（ETB）原文
    etd_raw: str              # 预计离港（ETD）原文
    agent: str                # 船代

    @property
    def etb(self) -> datetime | None:
        return parse_dt(self.etb_raw)

    @property
    def etd(self) -> datetime | None:
        return parse_dt(self.etd_raw)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["etb_iso"] = self.etb.isoformat(sep=" ") if self.etb else None
        d["etd_iso"] = self.etd.isoformat(sep=" ") if self.etd else None
        return d

    def one_line(self) -> str:
        return (f"航次 {self.voyage_code} | {self.ship_name} | 闸口 {self.gate} | "
                f"ETB {self.etb_raw} | ETD {self.etd_raw} | 船代 {self.agent}")


@dataclass
class Result:
    rows: list[Voyage]
    total: int | None         # 总记录数
    page: int | None          # 当前页
    pages: int | None         # 总页数
    raw: str = ""             # 原始 HTML（留档用）

    @property
    def empty(self) -> bool:
        return not self.rows


def decode(raw: bytes) -> str:
    """结果页是 gb2312；用 gb18030 超集解码，容错不抛。"""
    try:
        return raw.decode(ENCODING)
    except UnicodeDecodeError:
        return raw.decode(ENCODING, "replace")


def _meta(html: str, label: str) -> int | None:
    m = re.search(label + r"[：:]\s*(\d+)", html)
    return int(m.group(1)) if m else None


def parse_result(raw: bytes | str) -> Result:
    html = decode(raw) if isinstance(raw, bytes) else raw

    rows: list[Voyage] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        if len(tds) < 6:
            continue
        cells = [_text(td) for td in tds[:6]]
        rows.append(Voyage(*cells))

    return Result(
        rows=rows,
        total=_meta(html, "总记录数"),
        page=_meta(html, "当前页"),
        pages=_meta(html, "总页数"),
        raw=html,
    )


def is_result_page(html: str) -> bool:
    return "总记录数" in html and "码头航次" in html


def is_query_page(html: str) -> bool:
    """判断是不是「船期公众查询」页（带查询表单的那一页）。

    ⚠️ 不能用标题文字 `船期公众查询` 来判断 —— **公共信息服务首页里也有一条
    指向它的链接**，链接文本同样含这几个字，会把"被弹回首页"误判成"还在查询页"。
    （实测踩到的坑：GET 回来的 title 明明是「公共信息服务」，却因为正文里有
    这个字符串而被判为查询页。）

    改用查询表单特有的字段名 —— 首页上不存在这些。
    """
    return ('name="etb_time"' in html
            and 'name="voyage_code"' in html
            and "modify=query" in html)


def norm(s: str) -> str:
    """归一化：大写、压缩空白。用于比对船名/航次。"""
    return _WS.sub("", (s or "")).upper()
