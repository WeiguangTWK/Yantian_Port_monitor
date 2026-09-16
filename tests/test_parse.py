"""离线回归测试（不需要网络、不需要浏览器）。

    python -m unittest discover -s tests -v

fixtures/ 里的 HTML 是 2026-09-14 从站点抓的真实响应（token 已脱敏）。
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.matching import match_rows                  # noqa: E402
from ytmon.parse import (Voyage, is_query_page,        # noqa: E402
                         norm, parse_dt, parse_result)
from ytmon.store import StateStore, compare, target_key  # noqa: E402

FIX = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIX / name).read_text("utf-8")


class TestParse(unittest.TestCase):
    def test_normal_result(self):
        r = parse_result(load("voy_result_ever.html"))
        self.assertEqual(r.total, 38)
        self.assertEqual(r.pages, 1)
        self.assertEqual(len(r.rows), 38)
        first = r.rows[0]
        self.assertEqual(first.ship_name, "EVER GIFTED")
        self.assertEqual(first.voyage_code, "030W")
        self.assertEqual(first.gate, "B")
        self.assertEqual(first.agent, "联代")
        self.assertIsNotNone(first.etb)
        self.assertIsNotNone(first.etd)

    def test_empty_result(self):
        r = parse_result(load("voy_result_empty.html"))
        self.assertEqual(r.total, 0)
        self.assertEqual(r.rows, [])
        self.assertTrue(r.empty)

    def test_all_rows_have_six_fields(self):
        r = parse_result(load("voy_result_ever.html"))
        for v in r.rows:
            self.assertTrue(v.voyage_code, f"空航次：{v}")
            self.assertTrue(v.ship_name, f"空船名：{v}")
            # 闸口可能为空，但 ETB 必须有（站点保证）
            self.assertTrue(v.etb_raw, f"空 ETB：{v}")

    def test_date_formats(self):
        self.assertEqual(parse_dt("2026-09-14 21:00").hour, 21)
        self.assertEqual(parse_dt("2026-09-18").hour, 0)
        self.assertIsNone(parse_dt(""))
        self.assertIsNone(parse_dt("N/A"))

    def test_norm(self):
        self.assertEqual(norm(" msc  irina "), "MSCIRINA")
        self.assertEqual(norm("GJ634W"), "GJ634W")


class TestQueryPageContract(unittest.TestCase):
    """页面改版守卫：我们依赖的表单字段一旦消失，这里立刻失败。"""

    def test_form_contract(self):
        html = load("voy_query_page.html")
        for field in ["etb_time", "ship_name", "voyage_code", "pageCurrent"]:
            self.assertIn(f'name="{field}"', html,
                          f"查询页缺少字段 {field}，站点可能已改版")
        self.assertIn("voyQuery.jsp?modify=query", html,
                      "提交目标变了，站点可能已改版")
        self.assertIn("船期公众查询", html)

    def test_result_page_markers(self):
        html = load("voy_result_ever.html")
        for marker in ["码头航次", "船名", "闸口", "预计停靠", "预计离港", "船代",
                       "总记录数", "总页数"]:
            self.assertIn(marker, html, f"结果页缺少标记 {marker}")

    def test_is_query_page_accepts_real_query_page(self):
        self.assertTrue(is_query_page(load("voy_query_page.html")))

    def test_is_query_page_rejects_index_page(self):
        """回归守卫：这是实测踩过的坑。

        「公共信息服务」首页里有一条指向船期查询的链接，**链接文本恰好就是
        「船期公众查询」**。所以用标题文字判断会把"被弹回首页"误判成
        "还在查询页" —— `check_token` 于是永远报"有效"。
        """
        index = load("public_info_page.html")
        self.assertIn("船期公众查询", index,
                      "前提：首页确实含这几个字，否则本测试失去意义")
        self.assertFalse(is_query_page(index), "首页必须被判为【不是】查询页")

    def test_result_page_counts_as_query_page(self):
        """结果页是同一个页面的带结果版本，仍保留查询表单 —— 判为查询页是对的。

        这个断言的用途是"区分【查询页系】与【被弹回公共信息服务首页】"，
        而不是"区分有无结果"。
        """
        self.assertTrue(is_query_page(load("voy_result_ever.html")))


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.rows = parse_result(load("voy_result_ever.html")).rows

    def test_exact_ship(self):
        got, mode = match_rows(self.rows, "ship", "EVER GIFTED")
        self.assertEqual(mode, "exact")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].voyage_code, "030W")

    def test_ship_is_case_insensitive(self):
        got, mode = match_rows(self.rows, "ship", "ever gifted")
        self.assertEqual(mode, "exact")
        self.assertEqual(len(got), 1)

    def test_fuzzy_ship_warns(self):
        # 站点是子串匹配，'EVER' 会命中多条 => 必须标为 fuzzy
        got, mode = match_rows(self.rows, "ship", "EVER")
        self.assertEqual(mode, "fuzzy")
        self.assertGreater(len(got), 1)

    def test_voyage_exact(self):
        got, mode = match_rows(self.rows, "voyage", "030W")
        self.assertEqual(mode, "exact")
        self.assertEqual(got[0].ship_name, "EVER GIFTED")

    def test_no_match(self):
        got, mode = match_rows(self.rows, "ship", "NOT A REAL SHIP")
        self.assertEqual((got, mode), ([], "none"))


class TestCompare(unittest.TestCase):
    def _v(self, etb="2026-09-14 21:00", etd="2026-09-16 04:00", code="KN637A"):
        return Voyage(code, "MSC SOMYA III", "A", etb, etd, "外运")

    def test_first_time_has_no_changes(self):
        self.assertEqual(compare(None, self._v()), [])

    def test_no_change(self):
        prev = self._v().to_dict()
        self.assertEqual(compare(prev, self._v()), [])

    def test_etb_delay_reports_delta(self):
        prev = self._v().to_dict()
        changes = compare(prev, self._v(etb="2026-09-15 03:00"))
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].field, "etb_raw")
        self.assertAlmostEqual(changes[0].delta_hours, 6.0, places=3)
        self.assertIn("+6.0 小时", changes[0].describe())

    def test_etd_advance_is_negative(self):
        prev = self._v().to_dict()
        changes = compare(prev, self._v(etd="2026-09-15 04:00"))
        self.assertAlmostEqual(changes[0].delta_hours, -24.0, places=3)
        self.assertIn("−24.0", changes[0].describe())

    def test_date_only_vs_datetime_is_not_a_change(self):
        # 站点会在"仅日期"与"日期 00:00"之间来回变，指同一时刻 => 不该告警
        prev = self._v(etb="2026-09-18").to_dict()
        self.assertEqual(compare(prev, self._v(etb="2026-09-18 00:00")), [])

    def test_date_only_gaining_a_time_is_a_change(self):
        # 从"仅日期"变成带具体时刻 => 是真实变动，且不编造 delta
        prev = self._v(etb="2026-09-18").to_dict()
        changes = compare(prev, self._v(etb="2026-09-18 14:00"))
        self.assertEqual(len(changes), 1)
        self.assertAlmostEqual(changes[0].delta_hours, 14.0, places=3)

    def test_voyage_change_detected(self):
        prev = self._v().to_dict()
        changes = compare(prev, self._v(code="KN638A"))
        self.assertEqual([c.field for c in changes], ["voyage_code"])


class TestStateStore(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "s.json"
            st = StateStore(path).load()
            key = target_key("ship", "msc irina")
            self.assertEqual(key, "ship:MSC IRINA")
            self.assertIsNone(st.get(key))

            st.record(key, "ship", "MSC IRINA",
                      Voyage("KN637A", "MSC IRINA", "A", "2026-09-14 21:00",
                             "2026-09-16 04:00", "外运"))
            st.save()

            again = StateStore(path).load()
            prev = again.get(key)
            self.assertIsNotNone(prev)
            self.assertEqual(prev["record"]["voyage_code"], "KN637A")
            self.assertEqual(len(prev["history"]), 1)

    def test_broken_state_is_backed_up_not_swallowed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "s.json"
            path.write_text("{ this is not json", "utf-8")
            st = StateStore(path).load()
            self.assertEqual(st.data["targets"], {})
            self.assertTrue(path.with_suffix(".json.broken").exists(),
                            "损坏的状态文件应被备份，而不是直接覆盖")


if __name__ == "__main__":
    unittest.main(verbosity=2)
