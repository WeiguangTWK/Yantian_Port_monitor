"""守住 `ytmon/fatal.py`：windowed 产物唯一的错误出口。

为什么值得测：这个模块只在**别的机器上、出错的时候**才跑。
它自己坏掉的话，症状和"程序打不开"完全一样 —— 现场还是拿不到任何线索。
所以：**落盘必须成功、弹窗必须可选、而且两者都绝不能抛异常**。
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytmon import fatal                                          # noqa: E402


class TestAppendLog(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ytmon-fatal-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))

    def test_writes_file_and_returns_its_path(self):
        path = fatal.append_log("第一行\n第二行", base=self._tmp)
        self.assertEqual(path, os.path.join(self._tmp, fatal.LOG_NAME))
        self.assertTrue(os.path.isfile(path))

    def test_content_keeps_the_text_and_a_timestamp(self):
        text = "找不到配置文件 watchlist.json"
        path = fatal.append_log(text, base=self._tmp)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(text, body)
        self.assertIn(text[:6], body)
        # 时间戳形如 [2026-09-16 13:20:01]
        self.assertRegex(body, r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")

    def test_appends_rather_than_overwrites(self):
        fatal.append_log("第一次", base=self._tmp)
        fatal.append_log("第二次", base=self._tmp)
        with open(fatal.log_path(self._tmp), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("第一次", body)
        self.assertIn("第二次", body)

    def test_chinese_survives_utf8(self):
        """日志里一定有中文；按 GBK 写会崩在编码上（dead-ends B7）。"""
        path = fatal.append_log("警告：程序所在目录不可写 ✓", base=self._tmp)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("不可写", fh.read())

    def test_unwritable_location_does_not_raise(self):
        """它本身就是出错时才被调用的，再抛就把原始问题盖掉了。"""
        bogus = os.path.join(self._tmp, "no-such-dir", "deeper")
        path = fatal.append_log("x", base=bogus)          # 不抛就算过
        self.assertTrue(path.endswith(fatal.LOG_NAME))


class TestReportFatal(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ytmon-fatal-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))
        self.calls = []
        self._orig = fatal.show_dialog
        fatal.show_dialog = lambda title, text: self.calls.append((title, text)) or True
        self.addCleanup(lambda: setattr(fatal, "show_dialog", self._orig))

    def test_writes_log_and_returns_path(self):
        path = fatal.report_fatal("找不到配置文件", "路径：X", base=self._tmp)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("找不到配置文件", body)
        self.assertIn("路径：X", body)

    def test_dialog_is_off_by_default(self):
        """默认不弹 —— 否则测试或自动化运行会被一个模态框挂住。"""
        fatal.report_fatal("标题", "细节", base=self._tmp)
        self.assertEqual(self.calls, [])

    def test_dialog_shows_when_asked(self):
        fatal.report_fatal("标题", "细节", dialog=True, base=self._tmp)
        self.assertEqual(len(self.calls), 1)
        title, text = self.calls[0]
        self.assertEqual(title, "标题")
        self.assertIn("细节", text)
        self.assertIn(fatal.LOG_NAME, text, "弹窗里要告诉人日志写到哪了")

    def test_dialog_failure_does_not_break_reporting(self):
        def boom(title, text):
            raise RuntimeError("弹不出来")
        fatal.show_dialog = boom
        path = fatal.report_fatal("标题", "细节", dialog=True, base=self._tmp)
        self.assertTrue(os.path.isfile(path), "弹窗炸了也必须留下日志")

    def test_format_exception_has_type_and_traceback(self):
        try:
            raise ValueError("配置坏了")
        except ValueError as e:
            text = fatal.format_exception(e)
        self.assertIn("ValueError", text)
        self.assertIn("配置坏了", text)
        self.assertIn("Traceback", text)


class TestStderrDetection(unittest.TestCase):

    def test_returns_a_bool(self):
        self.assertIsInstance(fatal.stderr_is_lost(), bool)

    def test_none_stderr_means_lost(self):
        orig = sys.stderr
        try:
            sys.stderr = None
            self.assertTrue(fatal.stderr_is_lost())
        finally:
            sys.stderr = orig

    def test_real_stderr_is_not_lost(self):
        self.assertFalse(fatal.stderr_is_lost())


if __name__ == "__main__":
    unittest.main()
