"""心跳与停摆检测测试。

守的是一个**很容易被忽略、但会让整个告警系统白做**的空洞：

    如果监控本身死了，你会收到一片安静，
    而安静和"船期没变化"长得一模一样。

这里同时守住两种机制**能力不同**这个事实 ——
本地停摆检测能报"中途断过"，但报不了"再也没跑过"。
把这两者混为一谈，会让人以为配了心跳就万无一失。
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytmon.heartbeat import HeartbeatState, ping              # noqa: E402


class TestHeartbeatState(unittest.TestCase):

    def setUp(self):
        self.path = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-hb-")) / "hb.json"

    def test_fresh_state_has_no_history(self):
        st = HeartbeatState.load(self.path)
        self.assertEqual(st.last_success, "")
        self.assertIsNone(st.hours_since_success())

    def test_success_roundtrip(self):
        st = HeartbeatState.load(self.path)
        st.note_success()
        again = HeartbeatState.load(self.path)
        self.assertTrue(again.last_success)
        self.assertLess(again.hours_since_success(), 0.01)

    def test_gap_is_measured_correctly(self):
        st = HeartbeatState.load(self.path)
        now = dt.datetime(2026, 9, 14, 12, 0, 0)
        st.last_success = (now - dt.timedelta(hours=5)).isoformat(timespec="seconds")
        self.assertAlmostEqual(st.hours_since_success(now), 5.0, places=3)

    def test_stale_reason_fires_past_threshold(self):
        st = HeartbeatState.load(self.path)
        now = dt.datetime(2026, 9, 14, 12, 0, 0)
        st.last_success = (now - dt.timedelta(hours=5)).isoformat(timespec="seconds")
        self.assertIn("5.0 小时", st.stale_reason(3.0, now))
        self.assertEqual(st.stale_reason(8.0, now), "")

    def test_stale_check_disabled_when_zero(self):
        """0 = 关闭。不能因为"默认 0"就天天报停摆。"""
        st = HeartbeatState.load(self.path)
        now = dt.datetime(2026, 9, 14, 12, 0, 0)
        st.last_success = (now - dt.timedelta(days=400)).isoformat(timespec="seconds")
        self.assertEqual(st.stale_reason(0, now), "")

    def test_never_succeeded_does_not_report_stale(self):
        """从没成功过 = 还没建立基线，不该报"停摆"（那是首次运行）。"""
        st = HeartbeatState.load(self.path)
        self.assertEqual(st.stale_reason(1.0), "")

    def test_failures_are_counted_and_reset(self):
        st = HeartbeatState.load(self.path)
        self.assertEqual(st.note_failure(), 1)
        self.assertEqual(st.note_failure(), 2)
        st.note_success()
        self.assertEqual(HeartbeatState.load(self.path).consecutive_failures, 0)

    def test_corrupt_file_does_not_crash(self):
        self.path.write_text("{ 这不是 JSON", "utf-8")
        st = HeartbeatState.load(self.path)
        self.assertEqual(st.last_success, "")
        self.assertIsNone(st.hours_since_success())

    def test_bad_timestamp_does_not_crash(self):
        self.path.write_text(json.dumps({"last_success": "昨天"}), "utf-8")
        st = HeartbeatState.load(self.path)
        self.assertIsNone(st.hours_since_success())
        self.assertEqual(st.stale_reason(1.0), "")

    def test_unwritable_path_does_not_crash(self):
        """心跳是附属品 —— 存不下也绝不能把监控拖垮。"""
        st = HeartbeatState(path=pathlib.Path("Z:/不存在的盘/x.json"))
        st.note_success()                       # 不该抛
        st.note_failure()

    def test_heartbeat_due(self):
        st = HeartbeatState.load(self.path)
        now = dt.datetime(2026, 9, 14, 12, 0, 0)
        self.assertTrue(st.heartbeat_due(24, now), "从没发过 → 该发")
        self.assertFalse(st.heartbeat_due(0, now), "0 = 关闭")
        st.mark_heartbeat(now)
        self.assertFalse(st.heartbeat_due(24, now))
        self.assertTrue(st.heartbeat_due(24, now + dt.timedelta(hours=25)))


class TestPing(unittest.TestCase):

    def test_empty_url_is_not_an_error(self):
        ok, detail = ping("")
        self.assertFalse(ok)
        self.assertIn("未配置", detail)

    def test_success(self):
        class R:
            status_code = 200
        with patch("requests.get", return_value=R()):
            ok, detail = ping("https://hc-ping.com/abc")
        self.assertTrue(ok)
        self.assertIn("200", detail)

    def test_failure_is_reported_not_raised(self):
        """ping 只是旁路信号，失败绝不能影响监控。"""
        with patch("requests.get", side_effect=OSError("网络不通")):
            ok, detail = ping("https://hc-ping.com/abc")
        self.assertFalse(ok)
        self.assertIn("网络不通", detail)

    def test_non_2xx_is_reported(self):
        class R:
            status_code = 500
        with patch("requests.get", return_value=R()):
            ok, _ = ping("https://hc-ping.com/abc")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
