"""服务层编排、限流退避与会话重引导的离线测试。"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.config import AppConfig, Settings, Target     # noqa: E402
from ytmon.errors import SessionExpired                  # noqa: E402
from ytmon.service import (STATUS_ERROR, STATUS_SAME,            # noqa: E402
                           MonitorService, TargetOutcome)


class StubService(MonitorService):
    """把网络相关的两个方法换掉，只测编排决策。"""

    def __init__(self, cfg=None, target_errors=None):
        cfg = cfg or AppConfig(
            targets=[Target("ship", "MSC IRINA")],
            settings=Settings(state_file=str(pathlib.Path(tempfile.mkdtemp()) / "s.json")),
        )
        super().__init__(cfg)
        self.ran_targets = 0
        self._target_errors = target_errors or {}

    def _run_target(self, t, etb, store) -> TargetOutcome:
        idx = self.ran_targets
        self.ran_targets += 1
        exc = self._target_errors.get(idx)
        if exc is not None:
            from ytmon.http_client import is_fatal_error
            return TargetOutcome(t, STATUS_ERROR,
                                 error=f"{type(exc).__name__}: {exc}",
                                 fatal=is_fatal_error(exc))
        return TargetOutcome(t, STATUS_SAME)


class TestFirstTargetFailFast(unittest.TestCase):
    """第一个目标就撞上致命错误 → 不再打后面的目标。

    这与被删掉的 GET 预检查是同一个目的，但不额外发请求。
    在站点限流/异常时，这直接决定我们是"少打几下"还是"继续加压"。
    """

    def _svc(self, errors, n_targets=3):
        cfg = AppConfig(
            targets=[Target("ship", f"船{i}") for i in range(n_targets)],
            settings=Settings(
                state_file=str(pathlib.Path(tempfile.mkdtemp()) / "s.json")),
        )
        return StubService(cfg=cfg,
                           target_errors=errors)

    def test_rate_limit_stops_the_rest(self):
        from ytmon.errors import RateLimited
        svc = self._svc({0: RateLimited("站点限流（HTTP 567）")})
        rep = svc.run_cycle()
        self.assertEqual(svc.ran_targets, 1,
                         "被限流时不该继续查剩余目标")
        self.assertEqual(rep.counts["error"], 1)
        self.assertTrue(rep.outcomes[0].fatal)

    def test_network_error_stops_the_rest(self):
        import requests
        svc = self._svc({0: requests.ConnectionError("网络不通")})
        svc.run_cycle()
        self.assertEqual(svc.ran_targets, 1)

    def test_waf_stops_the_rest(self):
        svc = self._svc({0: SessionExpired("EdgeOne 挑战拦截")})
        svc.run_cycle()
        self.assertEqual(svc.ran_targets, 1)

    def test_non_fatal_error_does_not_stop_the_rest(self):
        """普通错误不该连坐 —— 换一个目标可能就好了。"""
        svc = self._svc({0: ValueError("解析不出来")})
        rep = svc.run_cycle()
        self.assertEqual(svc.ran_targets, 3, "非致命错误应继续查后面的目标")
        self.assertFalse(rep.outcomes[0].fatal,
                         "普通错误不该被标成致命")

    def test_single_target_still_reports(self):
        """只有一个目标时，"跳过剩余"没有意义，但结果要照常给出。"""
        from ytmon.errors import RateLimited
        svc = self._svc({0: RateLimited("限流")}, n_targets=1)
        rep = svc.run_cycle()
        self.assertEqual(len(rep.outcomes), 1)
        self.assertEqual(rep.counts["error"], 1)


class TestRateLimitBackoff(unittest.TestCase):
    """被限流时**退避**，而不是重新引导 cookie。

    这是实测逼出来的区分。限流的响应体同样"不是结果页"，
    如果按会话失效处理就会去起一次浏览器 —— 那是在被限流的时候
    又加一串请求，只会把自己推得更深。两类的正确反应是相反的。
    """

    def setUp(self):
        from unittest.mock import patch
        self._p = patch("ytmon.service.bootstrap_cookies",
                        return_value={"EO-Bot-Js-Token": "x"})
        self._bootstrap = self._p.start()

    def tearDown(self):
        self._p.stop()

    def _svc(self, errors, retries=2):
        cfg = AppConfig(
            targets=[Target("ship", "MSC IRINA")],
            settings=Settings(
                state_file=str(pathlib.Path(tempfile.mkdtemp()) / "s.json"),
                retry_attempts=retries, retry_backoff_seconds=0.01),
        )
        svc = SelfHealService(fail_times=0)
        svc.cfg = cfg
        svc._retries = retries
        # 把 sleep 换掉，测试不该真的等
        self._sleeps: list[float] = []
        return svc

    def test_backoff_then_success(self):
        from unittest.mock import patch
        from ytmon.errors import RateLimited

        svc = self._svc([])
        calls = {"n": 0}

        def fn(client, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RateLimited("站点限流（HTTP 567）")
            return "ok"

        with patch("ytmon.service.time.sleep") as slept:
            self.assertEqual(svc._client_call(fn), "ok")
        self.assertEqual(calls["n"], 2, "退避后应重试一次")
        self.assertEqual(slept.call_count, 1)
        self.assertEqual(self._bootstrap.call_count, 0)

    def test_no_rebootstrap_on_rate_limit(self):
        """关键断言：限流路径绝不能去起浏览器。"""
        from unittest.mock import patch
        from ytmon.errors import RateLimited

        svc = self._svc([])
        self._bootstrap.reset_mock()

        def fn(client, **kw):
            raise RateLimited("站点限流（HTTP 567）")

        with patch("ytmon.service.time.sleep"):
            with self.assertRaises(RateLimited):
                svc._client_call(fn)

        self.assertEqual(self._bootstrap.call_count, 0,
                         "被限流时重新引导 cookie 只会雪上加霜")

    def test_gives_up_after_retries(self):
        from unittest.mock import patch
        from ytmon.errors import RateLimited

        svc = self._svc([], retries=2)
        calls = {"n": 0}

        def fn(client, **kw):
            calls["n"] += 1
            raise RateLimited("限流")

        with patch("ytmon.service.time.sleep"):
            with self.assertRaises(RateLimited):
                svc._client_call(fn)
        self.assertEqual(calls["n"], 3, "1 次原始 + 2 次重试")

    def test_zero_retries_means_no_extra_request(self):
        from unittest.mock import patch
        from ytmon.errors import RateLimited

        svc = self._svc([], retries=0)
        calls = {"n": 0}

        def fn(client, **kw):
            calls["n"] += 1
            raise RateLimited("限流")

        with patch("ytmon.service.time.sleep"):
            with self.assertRaises(RateLimited):
                svc._client_call(fn)
        self.assertEqual(calls["n"], 1, "关掉重试就应该只打一次")

    def test_session_expired_still_rebootstraps(self):
        """别把会话失效也一起改成退避 —— 那条路径重引导是对的。"""
        from unittest.mock import patch
        svc = SelfHealService(fail_times=1)
        self._bootstrap.reset_mock()
        with patch("ytmon.service.time.sleep"):
            self.assertEqual(svc._client_call(svc._fn), "ok-after-2")
        self.assertEqual(self._bootstrap.call_count, 1)

    def test_backoff_grows_and_is_jittered(self):
        """退避要指数增长，且带抖动（固定节奏很容易和限流窗口对齐）。"""
        svc = SelfHealService(fail_times=0)
        svc.cfg.settings.retry_backoff_seconds = 10.0
        a = svc._backoff_seconds(0)
        b = svc._backoff_seconds(1)
        self.assertGreaterEqual(a, 10.0)
        self.assertLessEqual(a, 15.0)
        self.assertGreaterEqual(b, 20.0, "指数增长")
        # 多次取值不应完全相同
        vals = {svc._backoff_seconds(0) for _ in range(20)}
        self.assertGreater(len(vals), 1, "退避必须带抖动")


class TestIsFatalError(unittest.TestCase):
    """哪些错误意味着"换下一个目标也白搭" —— 决定要不要跳过剩余目标。"""

    def test_rate_limit_is_fatal(self):
        from ytmon.errors import RateLimited
        from ytmon.http_client import is_fatal_error
        self.assertTrue(is_fatal_error(RateLimited("限流")))

    def test_session_expired_is_fatal(self):
        from ytmon.http_client import is_fatal_error
        self.assertTrue(is_fatal_error(SessionExpired("EdgeOne 挑战拦截")))

    def test_network_error_is_fatal(self):
        import requests
        from ytmon.http_client import is_fatal_error
        self.assertTrue(is_fatal_error(requests.ConnectionError("不通")))

    def test_ordinary_error_is_not_fatal(self):
        from ytmon.http_client import is_fatal_error
        for exc in (ValueError("解析不出来"), KeyError("字段变了"),
                    RuntimeError("别的问题")):
            with self.subTest(exc):
                self.assertFalse(is_fatal_error(exc))


class _DummyClient:
    def __init__(self):
        self.cookies = None

    def set_cookies(self, c):
        self.cookies = c


class SelfHealService(MonitorService):
    def __init__(self, fail_times):
        cfg = AppConfig(
            targets=[Target("ship", "MSC IRINA")],
            settings=Settings(state_file=str(pathlib.Path(tempfile.mkdtemp()) / "s.json")),
        )
        super().__init__(cfg)
        self.fail_times = fail_times
        self.calls = 0

    def ensure_client(self):
        return _DummyClient()

    def _fn(self, client, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise SessionExpired("会话失效")
        return f"ok-after-{self.calls}"


class TestSelfHeal(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self._patch = patch("ytmon.service.bootstrap_cookies",
                            return_value={"EO-Bot-Js-Token": "x"})
        self.bootstrap = self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_success_needs_no_healing(self):
        service = SelfHealService(0)
        self.assertEqual(service._client_call(service._fn), "ok-after-1")
        self.bootstrap.assert_not_called()

    def test_one_rebootstrap_heals(self):
        service = SelfHealService(1)
        self.assertEqual(service._client_call(service._fn), "ok-after-2")
        self.bootstrap.assert_called_once()

    def test_second_session_failure_propagates(self):
        service = SelfHealService(2)
        with self.assertRaises(SessionExpired):
            service._client_call(service._fn)
        self.assertEqual(service.calls, 2)
        self.bootstrap.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
