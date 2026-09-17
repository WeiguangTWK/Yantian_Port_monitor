"""告警层的离线测试。

这里守的东西比"能不能发出去"更要紧，是三条容易悄悄坏掉的判断：

  1. **冷却去重** —— 船一离港，"未查到"会每轮都出现。如果去重坏了，
     监控就变成每 10 分钟一条的骚扰器，用不了几天就会被关掉。
  2. **告警失败不冒泡** —— 通道抽风不能把监控循环带崩。
  3. **内容指纹** —— 同一条船 ETB 又改了一次时必须**立刻**放行，
     冷却不能吞掉真实变化。

全部离线：HTTP 走注入的 transport，邮件只在配置层面校验。
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.config import (AppConfig, NotifyChannel, Settings,  # noqa: E402
                          Target)
from ytmon.notify import (AlertMessage, AlertThrottle, Notifier,  # noqa: E402
                          build_alert, build_payload, test_message)
from ytmon.notify import _channel_error, _feishu_sign, _mask  # noqa: E402
from ytmon.parse import Voyage                                    # noqa: E402
from ytmon.service import (STATUS_CHANGED, STATUS_ERROR,          # noqa: E402
                           STATUS_FIRST, STATUS_MISSING, STATUS_SAME,
                           CycleReport, TargetOutcome)
from ytmon.store import FieldChange                               # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-notify-"))


def voyage(etb="2026-09-21", etd="2026-09-22", code="GJ634W",
           ship="MSC IRINA") -> Voyage:
    return Voyage(code, ship, "A1", etb, etd, "某船代")


def outcome(status: str, target="MSC IRINA", **kw) -> TargetOutcome:
    return TargetOutcome(target=Target("ship", target), status=status, **kw)


def report(*outcomes) -> CycleReport:
    return CycleReport("2026-09-14 10:00:00", "2026-09-14 10:00:02",
                       "20260907", list(outcomes))


class RecordingTransport:
    """假的 HTTP 出口，记录发出去的东西，也能指定某个 url 失败。"""

    def __init__(self, body='{"errcode":0}', fail_urls=()):
        self.body = body
        self.fail_urls = set(fail_urls)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        if url in self.fail_urls:
            raise RuntimeError("连接被拒绝")
        return self.body


def settings(**kw) -> Settings:
    kw.setdefault("alert_state_file", str(TMP / f"thr-{len(list(TMP.iterdir()))}.json"))
    return Settings(**kw)


def notifier(channels, transport=None, st=None, **kw):
    return Notifier(channels, st or settings(), transport=transport or
                    RecordingTransport(), **kw)


# ------------------------------------------------------------------ 触发范围


class TestWhatGetsAlerted(unittest.TestCase):

    def test_same_is_silent(self):
        rep = report(outcome(STATUS_SAME, voyages=[voyage()]))
        self.assertIsNone(build_alert(rep))

    def test_first_is_silent_by_default(self):
        # 首次只是建立基线，不是"出事了"，默认不该半夜叫人
        rep = report(outcome(STATUS_FIRST, voyages=[voyage()]))
        self.assertIsNone(build_alert(rep))

    def test_first_can_be_opted_in(self):
        rep = report(outcome(STATUS_FIRST, voyages=[voyage()]))
        msg = build_alert(rep, ["first"])
        self.assertIsNotNone(msg)
        self.assertIn("MSC IRINA", msg.text)

    def test_missing_is_alerted(self):
        self.assertIsNotNone(build_alert(report(outcome(STATUS_MISSING))))

    def test_error_is_alerted(self):
        msg = build_alert(report(outcome(STATUS_ERROR, error="连接超时")))
        self.assertIn("连接超时", msg.text)

    def test_change_carries_old_and_new(self):
        v = voyage(etb="2026-09-23")
        o = outcome(STATUS_CHANGED, voyages=[v], changes={
            "GJ634W": [FieldChange("etb_raw", "ETB", "2026-09-21",
                                   "2026-09-23", 48.0)]})
        text = build_alert(report(o)).text
        self.assertIn("2026-09-21 → 2026-09-23", text)
        self.assertIn("48.0 小时", text)
        self.assertIn("GJ634W", text)

    def test_mixed_report_only_mentions_alertable(self):
        rep = report(outcome(STATUS_SAME, target="安静船", voyages=[voyage()]),
                     outcome(STATUS_MISSING, target="失踪船"))
        msg = build_alert(rep)
        self.assertIn("失踪船", msg.text)
        self.assertNotIn("安静船", msg.text)


# ------------------------------------------------------------------ 去重


class TestThrottle(unittest.TestCase):

    def setUp(self):
        self.path = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-thr-")) / "a.json"

    def test_repeat_within_cooldown_is_suppressed(self):
        t = AlertThrottle(self.path, 3600)
        o = outcome(STATUS_MISSING)
        self.assertTrue(t.should_send(o, now=1000))
        t.mark_sent(o, now=1000)
        self.assertFalse(t.should_send(o, now=1001))
        self.assertFalse(t.should_send(o, now=4599))

    def test_released_after_cooldown(self):
        t = AlertThrottle(self.path, 600)
        o = outcome(STATUS_MISSING)
        t.mark_sent(o, now=1000)
        self.assertTrue(t.should_send(o, now=1601))

    def test_real_change_bypasses_cooldown(self):
        """同一条船又改了一次 —— 必须立刻放行，不能被冷却吃掉。"""
        t = AlertThrottle(self.path, 3600)
        old = outcome(STATUS_CHANGED, voyages=[voyage(etb="2026-09-23")], changes={
            "GJ634W": [FieldChange("etb_raw", "ETB", "2026-09-21",
                                   "2026-09-23", 48.0)]})
        t.mark_sent(old, now=1000)

        new = outcome(STATUS_CHANGED, voyages=[voyage(etb="2026-09-25")], changes={
            "GJ634W": [FieldChange("etb_raw", "ETB", "2026-09-23",
                                   "2026-09-25", 48.0)]})
        self.assertTrue(t.should_send(new, now=1001))

    def test_state_survives_reload(self):
        t = AlertThrottle(self.path, 3600)
        o = outcome(STATUS_MISSING)
        t.mark_sent(o, now=1000)
        t.save()

        t2 = AlertThrottle(self.path, 3600)
        self.assertFalse(t2.should_send(o, now=1001))

    def test_corrupt_state_file_does_not_crash(self):
        self.path.write_text("{ 这不是 JSON", "utf-8")
        t = AlertThrottle(self.path, 3600)
        self.assertTrue(t.should_send(outcome(STATUS_MISSING), now=1000))

    def test_different_targets_are_independent(self):
        t = AlertThrottle(self.path, 3600)
        t.mark_sent(outcome(STATUS_MISSING, target="A"), now=1000)
        self.assertTrue(t.should_send(outcome(STATUS_MISSING, target="B"), now=1001))

    def test_zero_cooldown_never_suppresses(self):
        t = AlertThrottle(self.path, 0)
        o = outcome(STATUS_MISSING)
        t.mark_sent(o, now=1000)
        self.assertTrue(t.should_send(o, now=1000))


class TestErrorThreshold(unittest.TestCase):
    """查询失败要连续几次才告警。

    实测站点会偶发降级/限流（HTTP 567、会话被弹回）。一次失败就叫人
    等于狼来了 —— 用不了多久告警本身就会被无视，比不告警还糟。
    """

    def setUp(self):
        self.path = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-err-")) / "a.json"

    def test_first_failure_is_held_back(self):
        t = AlertThrottle(self.path, 3600, error_after=2)
        o = outcome(STATUS_ERROR, error="连接超时")
        self.assertEqual(t.note_outcome(o), 1)
        self.assertFalse(t.should_send(o))

    def test_second_consecutive_failure_alerts(self):
        t = AlertThrottle(self.path, 3600, error_after=2)
        o = outcome(STATUS_ERROR, error="连接超时")
        t.note_outcome(o)
        self.assertEqual(t.note_outcome(o), 2)
        self.assertTrue(t.should_send(o))

    def test_success_resets_the_streak(self):
        t = AlertThrottle(self.path, 3600, error_after=3)
        err = outcome(STATUS_ERROR, error="连接超时")
        ok = outcome(STATUS_SAME, voyages=[voyage()])
        t.note_outcome(err)
        t.note_outcome(err)
        t.note_outcome(ok)
        self.assertEqual(t.error_streak(err), 0)
        self.assertEqual(t.note_outcome(err), 1)         # 重新从头数
        self.assertFalse(t.should_send(err))

    def test_threshold_one_alerts_immediately(self):
        t = AlertThrottle(self.path, 3600, error_after=1)
        o = outcome(STATUS_ERROR, error="连接超时")
        t.note_outcome(o)
        self.assertTrue(t.should_send(o))

    def test_streak_persists_across_restart(self):
        """监控是每轮起一个进程跑的，计数必须落盘，否则永远攒不到 2。"""
        o = outcome(STATUS_ERROR, error="连接超时")
        t = AlertThrottle(self.path, 3600, error_after=2)
        t.note_outcome(o)
        t.save()

        t2 = AlertThrottle(self.path, 3600, error_after=2)
        self.assertEqual(t2.error_streak(o), 1)
        t2.note_outcome(o)
        self.assertTrue(t2.should_send(o))

    def test_missing_is_not_subject_to_the_threshold(self):
        """missing 是"船查不到了"，是结论，不是抖动，不该等。"""
        t = AlertThrottle(self.path, 3600, error_after=5)
        o = outcome(STATUS_MISSING)
        t.note_outcome(o)
        self.assertTrue(t.should_send(o))

    def test_notifier_counts_even_when_it_does_not_send(self):
        tr = RecordingTransport()
        st = settings(alert_state_file=str(TMP / "streak.json"),
                      alert_error_after=2)
        n = notifier([NotifyChannel(kind="webhook", url="https://a")], tr, st=st)
        rep = report(outcome(STATUS_ERROR, error="连接超时"))

        self.assertEqual(n.notify_cycle(rep), [])        # 第 1 次：压住
        self.assertEqual(tr.calls, [])
        self.assertEqual(n.throttle.error_streak(rep.outcomes[0]), 1)

        self.assertEqual(len(n.notify_cycle(rep)), 1)    # 第 2 次：发
        self.assertEqual(len(tr.calls), 1)

    def test_older_state_file_format_still_loads(self):
        """旧版本存的是扁平结构，升级后不能因为读不懂就把冷却清零。"""
        o = outcome(STATUS_MISSING)
        self.path.write_text(json.dumps({
            o.target.display(): {"fp": AlertThrottle.fingerprint(o), "ts": 1000.0}
        }, ensure_ascii=False), "utf-8")
        t = AlertThrottle(self.path, 3600)
        self.assertFalse(t.should_send(o, now=1001))


# ------------------------------------------------------------------ 通道载荷


class TestPayloads(unittest.TestCase):

    def test_wecom_markdown(self):
        url, p = build_payload(NotifyChannel(kind="wecom", url="https://q"),
                               test_message())
        self.assertEqual(url, "https://q")
        self.assertEqual(p["msgtype"], "markdown")
        self.assertIn("测试告警", p["markdown"]["content"])

    def test_feishu_text(self):
        url, p = build_payload(NotifyChannel(kind="feishu", url="https://f"),
                               test_message())
        self.assertEqual(p["msg_type"], "text")
        self.assertIn("测试告警", p["content"]["text"])

    def test_generic_webhook_is_structured(self):
        _, p = build_payload(NotifyChannel(kind="webhook", url="https://w"),
                             test_message())
        self.assertEqual(p["source"], "ytmon")
        self.assertIn("title", p)
        self.assertIn("sent_at", p)

    def test_dingtalk_signature_added_to_url(self):
        ch = NotifyChannel(kind="dingtalk", url="https://d/robot/send?access_token=T",
                           secret="SEC")
        url, p = build_payload(ch, test_message())
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(q["access_token"], ["T"])       # 原参数要保住
        self.assertIn("timestamp", q)
        self.assertIn("sign", q)
        self.assertEqual(p["msgtype"], "markdown")

    def test_dingtalk_without_secret_keeps_url_untouched(self):
        ch = NotifyChannel(kind="dingtalk", url="https://d/robot/send?access_token=T")
        url, _ = build_payload(ch, test_message())
        self.assertEqual(url, ch.url)

    def test_feishu_signature_is_deterministic(self):
        ts1, s1 = _feishu_sign("SEC", now=1700000000)
        ts2, s2 = _feishu_sign("SEC", now=1700000000)
        self.assertEqual((ts1, s1), (ts2, s2))
        self.assertEqual(ts1, "1700000000")
        # 换个时间就必须变，否则飞书会判重放
        self.assertNotEqual(_feishu_sign("SEC", now=1700000001)[1], s1)

    def test_feishu_secret_goes_into_payload(self):
        ch = NotifyChannel(kind="feishu", url="https://f", secret="SEC")
        _, p = build_payload(ch, test_message())
        self.assertIn("timestamp", p)
        self.assertIn("sign", p)


class TestResponseErrors(unittest.TestCase):

    def test_dingtalk_error_detected(self):
        # 机器人报错时 HTTP 仍是 200，只能看响应体
        self.assertIn("errcode=310000",
                      _channel_error("dingtalk", '{"errcode":310000,"errmsg":"sign not match"}'))

    def test_wecom_error_detected(self):
        self.assertIn("errcode=93000",
                      _channel_error("wecom", '{"errcode":93000,"errmsg":"invalid webhook"}'))

    def test_feishu_error_detected(self):
        self.assertIn("code=19021", _channel_error("feishu", '{"code":19021,"msg":"bad sign"}'))

    def test_success_codes_pass(self):
        for kind, body in (("dingtalk", '{"errcode":0,"errmsg":"ok"}'),
                           ("wecom", '{"errcode":0}'),
                           ("feishu", '{"code":0}')):
            self.assertEqual(_channel_error(kind, body), "")

    def test_unparsable_body_is_not_an_error(self):
        # 自建 webhook 常返回纯文本；不能因此判成失败而反复重发
        self.assertEqual(_channel_error("webhook", "<html>ok</html>"), "")


# ------------------------------------------------------------------ 分发


class TestNotifierDispatch(unittest.TestCase):

    def test_sends_to_every_enabled_channel(self):
        tr = RecordingTransport()
        n = notifier([NotifyChannel(kind="webhook", url="https://a"),
                      NotifyChannel(kind="wecom", url="https://b")], tr)
        results = n.send(test_message())
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(len(tr.calls), 2)

    def test_disabled_channel_is_skipped(self):
        tr = RecordingTransport()
        n = notifier([NotifyChannel(kind="webhook", url="https://a", enabled=False),
                      NotifyChannel(kind="webhook", url="https://b")], tr)
        n.send(test_message())
        self.assertEqual([u for u, _ in tr.calls], ["https://b"])

    def test_one_bad_channel_does_not_block_others(self):
        tr = RecordingTransport(fail_urls=["https://bad"])
        n = notifier([NotifyChannel(kind="webhook", url="https://bad", label="坏通道"),
                      NotifyChannel(kind="webhook", url="https://good")], tr)
        results = n.send(test_message())
        self.assertFalse(results[0].ok)
        self.assertIn("连接被拒绝", results[0].detail)
        self.assertTrue(results[1].ok)                   # 后面的照发

    def test_cycle_with_nothing_to_report_sends_nothing(self):
        tr = RecordingTransport()
        n = notifier([NotifyChannel(kind="webhook", url="https://a")], tr)
        self.assertEqual(n.notify_cycle(report(outcome(STATUS_SAME,
                                                       voyages=[voyage()]))), [])
        self.assertEqual(tr.calls, [])

    def test_cycle_sends_once_then_suppresses_repeat(self):
        tr = RecordingTransport()
        n = notifier([NotifyChannel(kind="webhook", url="https://a")], tr,
                     st=settings(alert_state_file=str(TMP / "cycle.json")))
        rep = report(outcome(STATUS_MISSING))
        self.assertEqual(len(n.notify_cycle(rep)), 1)
        self.assertEqual(n.notify_cycle(rep), [])        # 第二轮被冷却压掉
        self.assertEqual(len(tr.calls), 1)

    def test_all_channels_failing_does_not_mark_as_sent(self):
        """全挂时不记账 —— 否则一次网络抖动就把这条告警永久吞了。"""
        tr = RecordingTransport(fail_urls=["https://bad"])
        st = settings(alert_state_file=str(TMP / "retry.json"))
        n = notifier([NotifyChannel(kind="webhook", url="https://bad")], tr, st=st)
        rep = report(outcome(STATUS_MISSING))
        self.assertFalse(n.notify_cycle(rep)[0].ok)
        self.assertTrue(n.throttle.should_send(rep.outcomes[0]))   # 仍待发

    def test_batch_size_does_not_drop_later_targets(self):
        tr = RecordingTransport()
        n = notifier([NotifyChannel(kind="webhook", url="https://a")], tr,
                     st=settings(alert_state_file=str(TMP / "cap.json"),
                                 alert_max_per_cycle=2))
        rep = report(*[outcome(STATUS_MISSING, target=f"船{i}") for i in range(6)])
        n.notify_cycle(rep)
        sent = json.loads(json.dumps(tr.calls[0][1], ensure_ascii=False))
        self.assertIn("船0", sent["text"])
        self.assertIn("船1", sent["text"])
        self.assertNotIn("船5", sent["text"])
        self.assertEqual(len(tr.calls), 3)
        combined = '\n'.join(payload['text'] for _, payload in tr.calls)
        for i in range(6):
            self.assertIn(f'船{i}', combined)
        for o in rep.outcomes:
            self.assertFalse(n.throttle.should_send(o))

    def test_all_changed_vessels_include_etb_and_etd(self):
        tr = RecordingTransport()
        n = notifier([NotifyChannel(kind='webhook', url='https://a')], tr,
                     st=settings(alert_max_per_cycle=1))
        rep = report(*[
            outcome(STATUS_CHANGED, target=f'船{i}', voyages=[voyage(code=f'V{i}')],
                    changes={f'V{i}': [FieldChange('etb_raw', 'ETB', '旧ETB', '新ETB'),
                                      FieldChange('etd_raw', 'ETD', '旧ETD', '新ETD')]})
            for i in range(6)])
        n.notify_cycle(rep)
        self.assertEqual(len(tr.calls), 6)
        for i, (_, payload) in enumerate(tr.calls):
            self.assertIn(f'船{i}', payload['text'])
            self.assertIn('新ETB', payload['text'])
            self.assertIn('新ETD', payload['text'])

    def test_failed_batch_does_not_mark_sent_or_skip_next_batch(self):
        tr = RecordingTransport()
        def transport(url, payload):
            if '船0' in payload['text']:
                raise RuntimeError('发送失败')
            return tr(url, payload)
        n = notifier([NotifyChannel(kind='webhook', url='https://a')], transport,
                     st=settings(alert_max_per_cycle=1))
        rep = report(outcome(STATUS_MISSING, target='船0'),
                     outcome(STATUS_MISSING, target='船1'))
        results = n.notify_cycle(rep)
        self.assertEqual([r.ok for r in results], [False, True])
        self.assertTrue(n.throttle.should_send(rep.outcomes[0]))
        self.assertFalse(n.throttle.should_send(rep.outcomes[1]))

    def test_no_channels_is_a_no_op(self):
        n = notifier([])
        self.assertEqual(n.notify_cycle(report(outcome(STATUS_MISSING))), [])

    def test_broken_event_hook_does_not_break_sending(self):
        def boom(kind, payload):
            raise RuntimeError("回调坏了")

        tr = RecordingTransport()
        n = notifier([NotifyChannel(kind="webhook", url="https://a")], tr,
                     on_event=boom)
        self.assertEqual(len(n.send(test_message())), 1)


# ------------------------------------------------------------------ 脱敏


class TestMasking(unittest.TestCase):

    def test_access_token_is_masked(self):
        out = _mask("https://oapi.dingtalk.com/robot/send?access_token=SECRETVALUE")
        self.assertNotIn("SECRETVALUE", out)
        self.assertIn("access_token=%2A%2A%2A", out)

    def test_non_secret_params_survive(self):
        out = _mask("https://x/y?foo=bar&token=SECRET")
        self.assertIn("foo=bar", out)
        self.assertNotIn("SECRET", out)

    def test_empty_url(self):
        self.assertEqual(_mask(""), "")


# ------------------------------------------------------------------ 配置


class TestNotifyConfig(unittest.TestCase):

    def test_roundtrip_through_json(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-cfg-"))
        cfg = AppConfig(
            targets=[Target("ship", "MSC IRINA")],
            notify=[
                NotifyChannel(kind="dingtalk",
                              url="https://d/robot/send?access_token=T", secret="S"),
                NotifyChannel(kind="email", smtp_host="smtp.corp", smtp_user="u",
                              smtp_password="p", mail_from="u@corp",
                              mail_to=["a@corp", "b@corp"], smtp_port=587,
                              use_ssl=False),
            ],
        )
        p = cfg.save(d / "w.json")
        back = AppConfig.load(p)
        self.assertEqual(len(back.notify), 2)
        self.assertEqual(back.notify[0].kind, "dingtalk")
        self.assertEqual(back.notify[0].secret, "S")
        self.assertEqual(back.notify[1].mail_to, ["a@corp", "b@corp"])
        self.assertEqual(back.notify[1].smtp_port, 587)
        self.assertFalse(back.notify[1].use_ssl)

    def test_bare_url_string_is_accepted(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-cfg-"))
        p = d / "w.json"
        p.write_text(json.dumps({"targets": ["MSC IRINA"],
                                 "notify": ["https://hook/x"]}, ensure_ascii=False),
                     "utf-8")
        cfg = AppConfig.load(p)
        self.assertEqual(cfg.notify[0].kind, "webhook")
        self.assertEqual(cfg.notify[0].url, "https://hook/x")

    def test_unknown_channel_field_is_ignored(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-cfg-"))
        p = d / "w.json"
        p.write_text(json.dumps({"targets": ["MSC IRINA"],
                                 "notify": [{"kind": "webhook", "url": "https://x",
                                             "将来的字段": 1}]}, ensure_ascii=False),
                     "utf-8")
        cfg = AppConfig.load(p)
        self.assertEqual(cfg.notify[0].url, "https://x")

    def test_empty_notify_is_absent_from_saved_json(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-cfg-"))
        cfg = AppConfig(targets=[Target("ship", "MSC IRINA")])
        p = cfg.save(d / "w.json")
        self.assertNotIn("notify", json.loads(p.read_text("utf-8")))

    def test_missing_url_is_rejected(self):
        cfg = AppConfig(notify=[NotifyChannel(kind="dingtalk")])
        errs = cfg.validate_channels()
        self.assertTrue(any("缺少 url" in e for e in errs))

    def test_bad_kind_is_rejected(self):
        cfg = AppConfig(notify=[NotifyChannel(kind="短信")])
        self.assertTrue(any("通道类型" in e for e in cfg.validate_channels()))

    def test_email_requires_host_and_recipients(self):
        cfg = AppConfig(notify=[NotifyChannel(kind="email")])
        errs = " ".join(cfg.validate_channels())
        self.assertIn("smtp_host", errs)
        self.assertIn("mail_to", errs)

    def test_validate_channels_ignores_bad_targets(self):
        """--test-alert 要能在还没配好目标时先测通道。"""
        cfg = AppConfig(targets=[Target("ship", "")],
                        notify=[NotifyChannel(kind="webhook", url="https://x")])
        self.assertEqual(cfg.validate_channels(), [])
        self.assertTrue(cfg.validate())                  # 完整校验仍会报错

    def test_alert_on_is_validated(self):
        self.assertTrue(Settings(alert_on=["changed", "瞎写"]).validate())

    def test_enabled_channels_filters_disabled(self):
        cfg = AppConfig(notify=[NotifyChannel(kind="webhook", url="https://a"),
                                NotifyChannel(kind="webhook", url="https://b",
                                              enabled=False)])
        self.assertEqual(len(cfg.enabled_channels), 1)


class TestWindowsChannel(unittest.TestCase):
    """Windows 原生通知通道。

    守住的关键点是**失败要如实报告**：Windows 通知需要已登录的交互式桌面会话，
    计划任务勾了"不管用户是否登录"就会落在会话 0，永远弹不出来。
    这时候通道必须报失败并说明原因，绝不能静默地假装成功 ——
    否则用户会以为自己配好了告警，直到真出事那天才发现从来没收到过。
    """

    def test_windows_is_a_valid_kind(self):
        from ytmon.config import VALID_CHANNELS
        self.assertIn("windows", VALID_CHANNELS)
        self.assertEqual(NotifyChannel(kind="windows").validate(), [])

    def test_windows_needs_no_url(self):
        """它走系统托盘，不该被 URL 校验拦住。"""
        self.assertEqual(NotifyChannel(kind="windows", url="").validate(), [])

    def test_send_windows_calls_show(self):
        from unittest.mock import patch
        from ytmon.notify import send_windows
        ch = NotifyChannel(kind="windows", hold_seconds=2.5)
        with patch("ytmon.winnotify.show") as show:
            send_windows(ch, test_message())
        show.assert_called_once()
        kwargs = show.call_args.kwargs
        self.assertEqual(kwargs.get("hold_seconds"), 2.5)
        self.assertIn("测试消息", show.call_args.args[0])

    def test_error_messages_use_warning_level(self):
        """错误类通知在系统里要显示成警告图标，不是普通信息。"""
        from unittest.mock import patch
        from ytmon.notify import send_windows
        ch = NotifyChannel(kind="windows")
        msg = AlertMessage(title="盐田船期告警 · 错误 1", text="查询失败")
        with patch("ytmon.winnotify.show") as show:
            send_windows(ch, msg)
        self.assertEqual(show.call_args.kwargs.get("level"), "warning")

    def test_failure_is_reported_not_swallowed(self):
        from unittest.mock import patch
        from ytmon.notify import Notifier
        ch = NotifyChannel(kind="windows", label="系统通知")
        n = Notifier([ch], settings())
        with patch("ytmon.winnotify.show",
                   side_effect=RuntimeError("非交互式会话，弹不出来")):
            results = n.send(test_message())
        self.assertFalse(results[0].ok)
        self.assertIn("非交互式会话", results[0].detail)

    def test_windows_failure_does_not_block_other_channels(self):
        from unittest.mock import patch
        from ytmon.notify import Notifier
        tr = RecordingTransport()
        n = Notifier([NotifyChannel(kind="windows", label="系统"),
                      NotifyChannel(kind="webhook", url="https://ok")],
                     settings(), transport=tr)
        with patch("ytmon.winnotify.show", side_effect=RuntimeError("弹不出来")):
            results = n.send(test_message())
        self.assertFalse(results[0].ok)
        self.assertTrue(results[1].ok, "系统通知失败不该影响 webhook")

    def test_describe_mentions_it_needs_a_desktop(self):
        from ytmon.notify import describe
        out = describe([NotifyChannel(kind="windows")])
        self.assertIn("托盘", out[0])


class TestHeartbeatInNotifier(unittest.TestCase):
    """心跳/停摆接在 notify_cycle 里。"""

    def _settings(self, **kw):
        kw.setdefault("alert_state_file", str(TMP / f"hb-{len(list(TMP.iterdir()))}.json"))
        kw.setdefault("heartbeat_state_file", str(TMP / f"hbs-{len(list(TMP.iterdir()))}.json"))
        return Settings(**kw)

    def _notifier(self, st=None, tr=None):
        return notifier([NotifyChannel(kind="webhook", url="https://a")],
                        tr or RecordingTransport(), st=st or self._settings())

    def test_success_is_recorded(self):
        from ytmon.heartbeat import HeartbeatState
        st = self._settings()
        n = self._notifier(st=st)
        n.notify_cycle(report(outcome(STATUS_SAME, voyages=[voyage()])))
        hb = HeartbeatState.load(st.heartbeat_state_file)
        self.assertTrue(hb.last_success, "成功的轮次必须记下来，否则停摆检测没基准")
        self.assertEqual(hb.consecutive_failures, 0)

    def test_failure_is_counted(self):
        from ytmon.heartbeat import HeartbeatState
        st = self._settings()
        n = self._notifier(st=st)
        n.notify_cycle(report(outcome(STATUS_ERROR, error="超时")))
        hb = HeartbeatState.load(st.heartbeat_state_file)
        self.assertEqual(hb.consecutive_failures, 1)
        self.assertEqual(hb.last_success, "")

    def test_stale_gap_is_alerted(self):
        import datetime as dt
        from ytmon.heartbeat import HeartbeatState
        st = self._settings(stale_after_hours=3.0)
        # 造一个"上次成功是 5 小时前"的状态
        hb = HeartbeatState.load(st.heartbeat_state_file)
        hb.last_success = (dt.datetime.now() - dt.timedelta(hours=5)).isoformat(
            timespec="seconds")
        hb.save()

        tr = RecordingTransport()
        n = self._notifier(st=st, tr=tr)
        n.notify_cycle(report(outcome(STATUS_SAME, voyages=[voyage()])))
        self.assertEqual(len(tr.calls), 1, "停摆应该报出来")
        self.assertIn("停摆", tr.calls[0][1]["title"])

    def test_stale_alert_is_not_repeated_in_cooldown(self):
        import datetime as dt
        from ytmon.heartbeat import HeartbeatState
        st = self._settings(stale_after_hours=3.0, alert_error_after=99)
        hb = HeartbeatState.load(st.heartbeat_state_file)
        hb.last_success = (dt.datetime.now() - dt.timedelta(hours=5)).isoformat(
            timespec="seconds")
        hb.save()

        tr = RecordingTransport()
        n = self._notifier(st=st, tr=tr)

        def stale_count() -> int:
            return sum(1 for _, body in tr.calls if "停摆" in body["title"])

        # 连着两轮都失败（last_success 不会被失败轮清掉，所以间隔一直在）
        n.notify_cycle(report(outcome(STATUS_ERROR, error="超时")))
        n.notify_cycle(report(outcome(STATUS_ERROR, error="超时")))
        self.assertEqual(stale_count(), 1,
                         "停摆告警在冷却期内不该重复刷")

    def test_no_stale_alert_when_disabled(self):
        st = self._settings(stale_after_hours=0)
        tr = RecordingTransport()
        n = self._notifier(st=st, tr=tr)
        n.notify_cycle(report(outcome(STATUS_SAME, voyages=[voyage()])))
        self.assertEqual(tr.calls, [])

    def test_heartbeat_is_sent_when_due(self):
        st = self._settings(heartbeat_hours=1.0)
        tr = RecordingTransport()
        n = self._notifier(st=st, tr=tr)
        n.notify_cycle(report(outcome(STATUS_SAME, voyages=[voyage()])))
        self.assertEqual(len(tr.calls), 1)
        self.assertIn("心跳", tr.calls[0][1]["title"])

    def test_heartbeat_not_repeated(self):
        st = self._settings(heartbeat_hours=1.0)
        tr = RecordingTransport()
        n = self._notifier(st=st, tr=tr)
        n.notify_cycle(report(outcome(STATUS_SAME, voyages=[voyage()])))
        n.notify_cycle(report(outcome(STATUS_SAME, voyages=[voyage()])))
        self.assertEqual(len(tr.calls), 1, "心跳间隔没到就不该再发")

    def test_heartbeat_url_is_pinged_every_cycle(self):
        st = self._settings(heartbeat_url="https://hc-ping.com/abc")
        n = self._notifier(st=st)
        # 注意要打在 ytmon.notify.ping 上 —— notify 模块是 `from .heartbeat import ping`
        # 绑定的名字，改 ytmon.heartbeat.ping 影响不到它。
        with unittest.mock.patch("ytmon.notify.ping",
                                 return_value=(True, "HTTP 200")) as pinged:
            n.notify_cycle(report(outcome(STATUS_SAME, voyages=[voyage()])))
            n.notify_cycle(report(outcome(STATUS_SAME, voyages=[voyage()])))
        self.assertEqual(pinged.call_count, 2,
                         "外部 ping 每轮都要发 —— 它代表'进程还活着'")

    def test_ping_failure_does_not_break_the_cycle(self):
        """外部服务挂了不能把监控带崩。"""
        st = self._settings(heartbeat_url="https://hc-ping.com/abc")
        n = self._notifier(st=st)
        with unittest.mock.patch("ytmon.notify.ping",
                                 side_effect=OSError("DNS 挂了")):
            results = n.notify_cycle(
                report(outcome(STATUS_CHANGED, voyages=[voyage()], changes={
                    "GJ634W": [FieldChange("etb_raw", "ETB", "a", "b", 1.0)]})))
        # 变更告警仍然照发
        self.assertTrue(any(r.ok for r in results))


class TestConfigTypoDetection(unittest.TestCase):
    """拼错的配置项必须被**明确指出来**，不能静默忽略。

    静默忽略是最坏的一种失败：用户以为配上了，其实没生效，
    而且要等到"告警怎么一直没来"才发现 —— 那时候已经错过真事件了。
    配置项一多，这种手误几乎必然发生。
    """

    def _load(self, raw: dict) -> AppConfig:
        d = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-typo-"))
        p = d / "w.json"
        p.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
        return AppConfig.load(p)

    def test_typo_in_settings_key_is_reported(self):
        cfg = self._load({
            "targets": ["MSC IRINA"],
            "settings": {"retry_attempts": 2, "rtery_attempts": 5},
        })
        errs = " ".join(cfg.validate())
        self.assertIn("rtery_attempts", errs, "拼错的设置项必须被报出来")
        self.assertIn("不会生效", errs)

    def test_typo_in_channel_key_is_reported(self):
        cfg = self._load({
            "targets": ["MSC IRINA"],
            "notify": [{"kind": "dingtalk",
                        "url": "https://oapi.dingtalk.com/robot/send?access_token=" + "x" * 30,
                        "secrect": "SEC"}],
        })
        errs = " ".join(cfg.validate())
        self.assertIn("secrect", errs)

    def test_comment_keys_are_not_flagged(self):
        """以 _ 开头的是注释键（示例文件里全是），不能当成拼写错误。"""
        cfg = self._load({
            "_说明": "随便写",
            "targets": ["MSC IRINA"],
            "settings": {"_抗抖动说明": ["blah"], "retry_attempts": 2},
            "notify": [{"kind": "windows", "_备注": "这条是注释"}],
        })
        self.assertEqual(cfg.validate(), [], f"注释键被误报了：{cfg.validate()}")

    def test_typos_are_not_written_back_to_file(self):
        """内部记账字段不该被写回配置文件，否则配置会被越写越脏。"""
        cfg = self._load({"targets": ["MSC IRINA"],
                          "settings": {"rtery_attempts": 5}})
        d = {k: v for k, v in cfg.to_dict()["settings"].items()}
        self.assertNotIn("unknown_keys", d)
        self.assertNotIn("rtery_attempts", d)

    def test_url_on_windows_channel_is_rejected(self):
        """windows 通道填 url 一定是搞错了 —— 明确拦下来。

        静默忽略会让人以为那个 url 起了作用（比如以为通知被转发到了别处）。
        """
        cfg = AppConfig(notify=[NotifyChannel(kind="windows",
                                              url="https://example.com/hook")])
        errs = " ".join(cfg.validate_channels())
        self.assertIn("不用 url", errs)

    def test_unknown_key_survives_a_save_load_roundtrip(self):
        """重写配置文件之后再读回来，注释键不该变成"拼写错误"。"""
        d = pathlib.Path(tempfile.mkdtemp(prefix="ytmon-typo2-"))
        cfg = self._load({"targets": ["MSC IRINA"], "settings": {"retry_attempts": 2}})
        p = cfg.save(d / "w.json")
        back = AppConfig.load(p)
        self.assertEqual(back.validate(), [])

    def test_each_problem_is_reported_exactly_once(self):
        """同一个问题只能报一次。

        这条是补的回归守卫：unknown_keys 的检查一度同时放在
        Settings/NotifyChannel.validate() 和 AppConfig.validate_unknown_keys() 里，
        结果是同一条错误打印两遍，看起来像两个不同的毛病。
        """
        cfg = self._load({
            "targets": ["MSC IRINA"],
            "settings": {"rtery_attempts": 5},
            "notify": [{"kind": "webhook", "url": "https://x", "secrect": "s"}],
        })
        errs = cfg.validate()
        self.assertEqual(len(errs), len(set(errs)), f"有重复报错：{errs}")
        self.assertTrue(any("rtery_attempts" in e for e in errs))
        self.assertTrue(any("secrect" in e for e in errs))

    def test_test_alert_still_reports_typos_without_valid_targets(self):
        """--test-alert 的校验路径也要能查出拼写错误。

        之前那版只校验通道，settings 里的拼写错误在自检时被整个跳过 ——
        用户永远发现不了，直到"告警怎么一直没来"。
        """
        cfg = self._load({
            "targets": [{"type": "ship", "value": ""}],     # 目标故意是坏的
            "settings": {"rtery_attempts": 5},
            "notify": [{"kind": "windows"}],
        })
        # 自检路径 = 未知键 + 通道
        errs = cfg.validate_unknown_keys() + cfg.validate_channels()
        self.assertTrue(any("rtery_attempts" in e for e in errs),
                        "目标没配好时，拼写的设置项也必须被报出来")
        # 目标是坏的这个事实仍然要能拿到（由调用方决定怎么呈现）
        self.assertTrue(cfg.validate_targets())


if __name__ == "__main__":
    unittest.main()
