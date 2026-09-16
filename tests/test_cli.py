"""CLI 日志级别、告警输出与参数契约测试。"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytmon.cli import (EXIT_ERROR, EXIT_OK, build_parser,     # noqa: E402
                       make_event_handler, run_test_alert)
from ytmon.config import AppConfig, NotifyChannel, Target     # noqa: E402


def render(handler, *events) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for kind, payload in events:
            handler(kind, payload)
    return buf.getvalue()


LOG_DEBUG = ("log", {"level": "debug", "message": "查询响应已解析"})
LOG_INFO = ("log", {"level": "info", "message": "正在引导 cookie"})
LOG_WARN = ("log", {"level": "warn", "message": "被 EdgeOne 拦截"})
LOG_ERROR = ("log", {"level": "error", "message": "查询失败"})


class TestLogLevels(unittest.TestCase):

    def test_debug_hidden_by_default(self):
        out = render(make_event_handler(quiet=False), LOG_DEBUG)
        self.assertNotIn("查询响应已解析", out,
                         "恒定无信息的预检查提示不该默认刷屏")

    def test_debug_shown_with_verbose(self):
        out = render(make_event_handler(quiet=False, verbose=True), LOG_DEBUG)
        self.assertIn("查询响应已解析", out, "--verbose 要能把它找回来")

    def test_info_shown_by_default(self):
        self.assertIn("引导", render(make_event_handler(quiet=False), LOG_INFO))

    def test_info_hidden_when_quiet(self):
        self.assertNotIn("引导", render(make_event_handler(quiet=True), LOG_INFO))

    def test_warn_and_error_always_shown(self):
        for quiet in (False, True):
            for verbose in (False, True):
                with self.subTest(quiet=quiet, verbose=verbose):
                    h = make_event_handler(quiet=quiet, verbose=verbose)
                    self.assertIn("拦截", render(h, LOG_WARN))
                    self.assertIn("失败", render(h, LOG_ERROR))

    def test_verbose_does_not_override_quiet_for_debug(self):
        # --quiet --verbose 同时给：debug 该出来（verbose 是更明确的要求）
        out = render(make_event_handler(quiet=True, verbose=True), LOG_DEBUG)
        self.assertIn("查询响应已解析", out)


class TestAlertEvents(unittest.TestCase):

    def test_success_and_failure_are_rendered(self):
        h = make_event_handler(quiet=False)
        self.assertIn("已发出",
                      render(h, ("alert_sent", {"ok": True, "channel": "钉钉"})))
        self.assertIn("连接被拒绝",
                      render(h, ("alert_sent", {"ok": False,
                                                "message": "钉钉 发送失败：连接被拒绝"})))

    def test_unknown_event_is_ignored(self):
        # 将来 service 加新事件类型时，旧 CLI 不该崩
        self.assertEqual(render(make_event_handler(quiet=False),
                                ("future_event", {"x": 1})), "")


class TestTestAlertCommand(unittest.TestCase):

    def _cfg(self, notify) -> AppConfig:
        return AppConfig(targets=[Target("ship", "MSC IRINA")], notify=notify)

    def _args(self, **kw):
        ns = build_parser().parse_args([])
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_no_channels_explains_what_to_do(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_test_alert(self._cfg([]), self._args())
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("notify", buf.getvalue(), "要告诉用户去哪儿找写法")

    def test_success_returns_zero(self):
        # 把真正的 HTTP 出口换掉，别让测试依赖外网
        with unittest.mock.patch("ytmon.notify._post_json",
                                 return_value='{"errcode":0,"errmsg":"ok"}'):
            code = self._run([NotifyChannel(kind="webhook", url="https://ok")])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("成功 1 / 1", self._out)

    def test_partial_failure_returns_error(self):
        def boom(url, payload, timeout=None):
            raise ConnectionError("拒绝连接")

        with unittest.mock.patch("ytmon.notify._post_json", side_effect=boom):
            code = self._run([NotifyChannel(kind="webhook", url="https://bad",
                                            label="坏通道")])
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("坏通道", self._out)
        self.assertIn("拒绝连接", self._out)

    def test_token_is_masked_in_the_listing(self):
        """自检会把地址打出来，不能让 access_token 明文出现在屏幕/截图里。"""
        secret = "S" * 30
        with unittest.mock.patch("ytmon.notify._post_json", return_value=""):
            self._run([NotifyChannel(
                kind="dingtalk",
                url="https://oapi.dingtalk.com/robot/send?access_token=" + secret)])
        self.assertNotIn(secret, self._out,
                         "access_token 不能在自检输出里明文出现")

    # ---- 小工具 ----

    def _run(self, notify) -> int:
        cfg = self._cfg(notify)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run_test_alert(cfg, self._args())
        self._out = buf.getvalue()
        return code

    def test_channel_errors_do_not_need_valid_targets(self):
        """还没配好监控目标时也该能先测通道。"""
        cfg = AppConfig(targets=[Target("ship", "")],
                        notify=[NotifyChannel(kind="webhook", url="https://ok")])
        self.assertEqual(cfg.validate_channels(), [])
        self.assertTrue(cfg.validate(), "完整校验仍应报出目标的问题")


class TestParserContract(unittest.TestCase):

    def test_new_flags_are_accepted(self):
        for flag in ("--test-alert", "--no-notify", "--verbose", "--quiet"):
            with self.subTest(flag):
                args = build_parser().parse_args([flag])   # 不该抛
                self.assertTrue(getattr(args, flag[2:].replace("-", "_")))

    def test_removed_login_flags_are_rejected(self):
        for flags in (["--token", "LEGACY"], ["--no-auto-renew"]):
            with self.subTest(flags=flags):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        build_parser().parse_args(flags)

    def test_defaults(self):
        args = build_parser().parse_args([])
        self.assertFalse(args.test_alert)
        self.assertFalse(args.no_notify)
        self.assertFalse(args.verbose)
        self.assertFalse(args.quiet)
        self.assertEqual(args.watch, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
