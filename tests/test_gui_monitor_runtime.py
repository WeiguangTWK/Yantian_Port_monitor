"""倒计时与状态映射离线测试，不导入 Qt、不执行真实查询。"""

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gui.monitor_runtime import Countdown, ManualCheckCooldown, row_status


class TestCountdown(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.countdown = Countdown(clock=lambda: self.now, uniform=lambda low, high: high)

    def test_initially_unscheduled(self):
        self.assertFalse(self.countdown.due)
        self.assertEqual(self.countdown.bar_value, 0)

    def test_bar_decreases_with_remaining_time(self):
        self.countdown.reset(600)
        self.assertEqual(self.countdown.bar_value, 1000)
        self.now += 300
        self.assertEqual(self.countdown.remaining, 300)
        self.assertEqual(self.countdown.bar_value, 500)

    def test_jitter_is_included_in_deadline(self):
        self.countdown.reset(600, 30)
        self.assertEqual(self.countdown.remaining, 630)

    def test_elapsed_deadline_is_due_without_negative_progress(self):
        self.countdown.reset(60)
        self.now += 100
        self.assertTrue(self.countdown.due)
        self.assertEqual(self.countdown.remaining, 0)
        self.assertEqual(self.countdown.bar_value, 0)

    def test_stop_removes_deadline(self):
        self.countdown.reset(60)
        self.countdown.clear()
        self.now += 10
        self.assertFalse(self.countdown.due)

    def test_reset_uses_current_time_and_new_settings(self):
        self.countdown.reset(600)
        self.now += 200
        self.countdown.reset(1800)
        self.assertEqual(self.countdown.remaining, 1800)

    def test_invalid_intervals_are_rejected(self):
        for interval, jitter in [(0, 0), (5, 0), (59, 0), (600, -1), (True, 0),
                                 (float('nan'), 0), (600, float('inf'))]:
            with self.subTest(interval=interval, jitter=jitter):
                with self.assertRaises(ValueError):
                    self.countdown.reset(interval, jitter)

    def test_minimum_interval_is_accepted(self):
        self.countdown.reset(60)
        self.assertEqual(self.countdown.remaining, 60)


class TestManualCheckCooldown(unittest.TestCase):
    def test_starts_after_completion_and_expires_after_ten_seconds(self):
        now = [100.0]
        cooldown = ManualCheckCooldown(clock=lambda: now[0])
        self.assertTrue(cooldown.ready)
        cooldown.start()
        self.assertEqual(cooldown.remaining, 10)
        now[0] += 9.9
        self.assertFalse(cooldown.ready)
        now[0] += 0.1
        self.assertTrue(cooldown.ready)


class TestRowStatus(unittest.TestCase):
    def test_requested_statuses(self):
        for status, expected in [('updating', '正在更新'), ('error', '查询失败'),
                                 ('missing', '不存在'), ('same', '上次核对: 2026-09-16 12:00:00')]:
            self.assertEqual(row_status(True, status, '2026-09-16 12:00:00'), expected)

    def test_disabled_and_not_yet_checked(self):
        self.assertEqual(row_status(False, 'error'), '已停用')
        self.assertEqual(row_status(True), '上次核对: 尚未查询')


class TestTimerWiring(unittest.TestCase):
    def test_query_events_no_longer_drive_progress(self):
        tree = ast.parse((ROOT / 'gui/app.py').read_text('utf-8'))
        page = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MonitorPage')
        event = next(node for node in page.body if isinstance(node, ast.FunctionDef) and node.name == '_on_event')
        calls = [node for node in ast.walk(event) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertFalse(any(call.func.attr in ('setValue', 'setMaximum') for call in calls))
