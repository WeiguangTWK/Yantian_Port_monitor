"""服务层编排逻辑的离线测试（不联网、不起浏览器）。

重点守两条业务判断：
  1. token **确认过期**（expired）才自动续期
  2. **WAF 拦截 / 网络错误不该触发续期** —— 那两类问题续期解决不了，
     白登录一次还会浪费一次账号密码校验（甚至触发风控）
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.auth import RenewResult                       # noqa: E402
from ytmon.config import AppConfig, Settings, Target     # noqa: E402
from ytmon.errors import SessionExpired                  # noqa: E402
from ytmon.http_client import TokenStatus                # noqa: E402
from ytmon.service import (STATUS_ERROR, STATUS_SAME,            # noqa: E402
                           MonitorService, TargetOutcome)


def ok_status() -> TokenStatus:
    return TokenStatus(True, "ok", "可进入船期查询页", 200, 100)


def bad(reason: str) -> TokenStatus:
    return TokenStatus(False, reason, f"stub-{reason}", 200, 100)


class StubService(MonitorService):
    """把网络相关的两个方法换掉，只测编排决策。"""

    def __init__(self, statuses, renew_ok=True, renew_token_value="NEWTOKEN",
                 precheck=True, target_errors=None, **kw):
        cfg = kw.pop("cfg", None) or AppConfig(
            targets=[Target("ship", "MSC IRINA")],
            settings=Settings(
                state_file=str(pathlib.Path(tempfile.mkdtemp()) / "s.json"),
                precheck=precheck),
        )
        super().__init__(cfg, **kw)
        self._statuses = list(statuses)
        self.renew_calls = 0
        self._renew_ok = renew_ok
        self._renew_token_value = renew_token_value
        self.ran_targets = 0
        # {目标序号(0起): 异常} —— 用来测"首个目标失败就跳过剩余目标"
        self._target_errors = target_errors or {}

    def check_token(self, rebootstrap_on_waf: bool = True) -> TokenStatus:
        if len(self._statuses) > 1:
            return self._statuses.pop(0)
        return self._statuses[0]

    def renew_token(self) -> RenewResult:
        self.renew_calls += 1
        if self._renew_ok:
            return RenewResult(True, self._renew_token_value, "stub 续期成功")
        return RenewResult(False, None, "stub 续期失败")

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


class TestPrecheckPolicy(unittest.TestCase):
    """预检查策略（`settings.precheck` 打开时）。

    实测教训：requests 的 GET 会**稳定落到公共信息服务首页**，而同一会话的
    POST 查询却完全正常。所以"页面对不对"根本不是可靠判据 ——
    预检查只应该在"必然失败"的两类问题上提前中止。

    注意：**预检查默认是关闭的**（它每轮多打一次站点，却恒报 expired）。
    这些测试显式把它打开，测的是打开时的行为。
    """

    def test_waf_aborts_early(self):
        """WAF 拦截必然导致后续查询失败，该提前中止。"""
        svc = StubService([bad("waf")])
        rep = svc.run_cycle()
        self.assertEqual(svc.ran_targets, 0, "waf 时不该继续跑目标")
        self.assertEqual(rep.outcomes, [])
        self.assertFalse(rep.token_status.ok)

    def test_network_aborts_early(self):
        svc = StubService([bad("network")])
        rep = svc.run_cycle()
        self.assertEqual(svc.ran_targets, 0)
        self.assertFalse(rep.token_status.ok)

    def test_ratelimited_aborts_early(self):
        """被限流也该早停 —— 越查越糟。"""
        svc = StubService([bad("ratelimited")])
        rep = svc.run_cycle()
        self.assertEqual(svc.ran_targets, 0)
        self.assertFalse(rep.token_status.ok)

    def test_expired_precheck_does_not_abort(self):
        """关键：预检查说 expired，但**不该中止** —— 它不是可靠判据。"""
        svc = StubService([bad("expired")])
        rep = svc.run_cycle()
        self.assertEqual(svc.ran_targets, 1, "应继续实际查询，由查询来判定")
        self.assertTrue(rep.token_status.ok,
                        "查询都成功了，报告不该再说 token 不可用")

    def test_precheck_never_renews_by_itself(self):
        """续期不再挂在预检查上 —— 它挂在真实查询失败上（见 TestSelfHeal）。"""
        for reason in ("waf", "network", "expired"):
            svc = StubService([bad(reason)])
            svc.run_cycle()
            self.assertEqual(svc.renew_calls, 0, f"{reason} 不该在预检查阶段触发续期")

    def test_valid_precheck_runs_targets(self):
        svc = StubService([ok_status()])
        rep = svc.run_cycle()
        self.assertEqual(svc.ran_targets, 1)
        self.assertTrue(rep.token_status.ok)


class TestPrecheckIsOffByDefault(unittest.TestCase):
    """预检查默认关闭，而且**一次请求都不该发**。

    它原本的"早停"作用已由"第一个目标失败就跳过剩余目标"覆盖 ——
    同样的效果，但零额外请求。而每轮少打一次站点，对限流是实打实的收益。
    """

    def test_no_precheck_request_is_made(self):
        calls = []

        class S(StubService):
            def check_token(self, rebootstrap_on_waf=True):
                calls.append(1)
                return ok_status()

        svc = S([ok_status()], precheck=False)
        svc.run_cycle()
        self.assertEqual(calls, [], "预检查关着的时候不该发那次 GET")
        self.assertEqual(svc.ran_targets, 1)

    def test_token_status_is_none_when_precheck_off(self):
        svc = StubService([ok_status()], precheck=False)
        rep = svc.run_cycle()
        self.assertIsNone(rep.token_status,
                          "没做预检查就不该编造一个 token_status")

    def test_default_setting_is_off(self):
        self.assertFalse(Settings().precheck,
                         "预检查默认必须是关的 —— 它恒报 expired 且每轮多打一次站点")


class TestFirstTargetFailFast(unittest.TestCase):
    """第一个目标就撞上致命错误 → 不再打后面的目标。

    这与被删掉的 GET 预检查是同一个目的，但不额外发请求。
    在站点限流/异常时，这直接决定我们是"少打几下"还是"继续加压"。
    """

    def _svc(self, errors, n_targets=3):
        cfg = AppConfig(
            targets=[Target("ship", f"船{i}") for i in range(n_targets)],
            settings=Settings(
                state_file=str(pathlib.Path(tempfile.mkdtemp()) / "s.json"),
                precheck=False),
        )
        return StubService([ok_status()], cfg=cfg, precheck=False,
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
        self.assertEqual(svc.renew_calls, 0, "限流**不该**触发续期")

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


class TestTokenPersistence(unittest.TestCase):
    def test_env_token_is_not_written_to_file(self):
        """配置里原本没 token（走环境变量）时，不该偷偷把凭证落进文件。"""
        with tempfile.TemporaryDirectory() as d:
            cfg_path = pathlib.Path(d) / "w.json"
            cfg_path.write_text('{"token": "", "targets": []}', "utf-8")
            cfg = AppConfig.load(cfg_path)
            svc = MonitorService(cfg)
            self.assertFalse(svc._config_had_token)
            svc.cfg.token = "NEWSECRET"
            svc._persist_token_if_configured()
            self.assertNotIn("NEWSECRET", cfg_path.read_text("utf-8"),
                             "不该把环境变量来的 token 写进配置文件")

    def test_configured_token_is_written_back(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = pathlib.Path(d) / "w.json"
            cfg_path.write_text('{"token": "OLD", "targets": []}', "utf-8")
            cfg = AppConfig.load(cfg_path)
            svc = MonitorService(cfg)
            self.assertTrue(svc._config_had_token)
            svc.cfg.token = "NEWSECRET"
            svc._persist_token_if_configured()
            self.assertIn("NEWSECRET", cfg_path.read_text("utf-8"))


class _DummyClient:
    def __init__(self):
        self.cookies = None

    def set_cookies(self, c):
        self.cookies = c


class SelfHealService(MonitorService):
    """测 `_client_call` 的两级自愈：先重引导 cookie，再续期 token。"""

    def __init__(self, fail_times, renew_ok=True, has_creds=True):
        cfg = AppConfig(
            targets=[Target("ship", "MSC IRINA")],
            settings=Settings(state_file=str(pathlib.Path(tempfile.mkdtemp()) / "s.json")),
        )
        super().__init__(cfg)
        self.fail_times = fail_times      # 前 N 次调用抛 SessionExpired
        self.calls = 0
        self.renew_calls = 0
        self._renew_ok = renew_ok
        self._has_creds = has_creds

    def ensure_client(self):
        return _DummyClient()

    def _has_credentials(self) -> bool:
        # 显式指定，避免测试结果取决于这台机器上有没有存过凭证
        return self._has_creds

    def renew_token(self):
        self.renew_calls += 1
        self._auto_renew = True           # renew 后仍按调用方设定，这里保持
        if self._renew_ok:
            return RenewResult(True, "NEWTOKEN", "stub")
        return RenewResult(False, None, "stub 失败")

    def _fn(self, client, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise SessionExpired(f"第 {self.calls} 次失败")
        return f"ok-after-{self.calls}"


class TestSelfHeal(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self._p = patch("ytmon.service.bootstrap_cookies",
                        return_value={"EO-Bot-Js-Token": "x"})
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_success_first_try_no_healing(self):
        svc = SelfHealService(fail_times=0)
        self.assertEqual(svc._client_call(svc._fn), "ok-after-1")
        self.assertEqual(svc.renew_calls, 0)

    def test_rebootstrap_heals_without_renewing(self):
        """只失败一次 → 重新引导 cookie 就够了，不该浪费一次登录。"""
        svc = SelfHealService(fail_times=1)
        self.assertEqual(svc._client_call(svc._fn), "ok-after-2")
        self.assertEqual(svc.renew_calls, 0, "重引导能解决就不该续期")

    def test_renew_when_rebootstrap_insufficient(self):
        """重引导后仍失败 → 才续期 token。"""
        svc = SelfHealService(fail_times=2)
        self.assertEqual(svc._client_call(svc._fn), "ok-after-3")
        self.assertEqual(svc.renew_calls, 1)

    def test_renew_failure_propagates(self):
        svc = SelfHealService(fail_times=2, renew_ok=False)
        with self.assertRaises(SessionExpired):
            svc._client_call(svc._fn)
        self.assertEqual(svc.renew_calls, 1)

    def test_auto_renew_disabled_does_not_renew(self):
        svc = SelfHealService(fail_times=2)
        svc._auto_renew = False
        with self.assertRaises(SessionExpired):
            svc._client_call(svc._fn)
        self.assertEqual(svc.renew_calls, 0)

    def test_no_credentials_skips_renew_entirely(self):
        """没配账号时不该去续期。

        实测公众查询根本不需要 token，所以这条自愈路径本来就不是必需的。
        硬走一遍只会打印出看起来像配置错误的"续期失败"，
        把"站点临时降级/限流"这个真实原因盖掉。
        """
        svc = SelfHealService(fail_times=2, has_creds=False)
        with self.assertRaises(SessionExpired):
            svc._client_call(svc._fn)
        self.assertEqual(svc.renew_calls, 0, "没凭证就不该尝试续期")
        self.assertEqual(svc.calls, 2, "重引导那一次仍应发生")

    def test_credentials_present_still_renews(self):
        """真配了账号的话，这条兜底路径要保住 —— 不能一刀切删掉。"""
        svc = SelfHealService(fail_times=2, has_creds=True)
        self.assertEqual(svc._client_call(svc._fn), "ok-after-3")
        self.assertEqual(svc.renew_calls, 1)

    def test_has_credentials_is_robust(self):
        """凭证读取本身出错（注册表/加密异常）也不能把监控带崩。"""
        from unittest.mock import patch
        # 必须用基类实例 —— SelfHealService 覆盖了 _has_credentials
        svc = MonitorService(AppConfig(targets=[Target("ship", "MSC IRINA")]))
        with patch("ytmon.service.load_credentials",
                   side_effect=OSError("注册表读不了")):
            self.assertFalse(svc._has_credentials())


if __name__ == "__main__":
    unittest.main(verbosity=2)
