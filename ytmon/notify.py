"""告警出口。

设计上的四条硬约束：

1. **绝不因为告警失败而影响监控。** 任何异常都在这里被吃掉，转成事件
   回传给上层，监控循环照常往下跑。告警是附属品，不是主链路。
2. **不重复轰炸。** 船离港后 `missing` 会每轮都出现，如果不去重，10 分钟
   一条会把人淹没。所以按"目标 + 状态 + 内容指纹"做冷却。
3. **只用标准库 + requests。** 目标环境是 Win7 + Python 3.8，
   不能引入任何新依赖（SMTP 用 smtplib，签名用 hmac/hashlib，
   发 HTTP 用已有的 requests）。
4. **可在测试里完全离线。** 通道的 HTTP 调用走一个可注入的 `transport`，
   测试时不碰网络。

支持的通道：
  webhook   通用 JSON POST，涵括自建服务 / Slack / 任何接 JSON 的钩子
  dingtalk  钉钉群机器人（支持加签）
  wecom     企业微信群机器人
  feishu    飞书自定义机器人（支持加签）
  email     SMTP 邮件（监控机在受限网络里时最可靠的后路）
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import pathlib
import smtplib
import time
import urllib.parse
from dataclasses import dataclass
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formatdate

from .config import NotifyChannel
from .heartbeat import HeartbeatState, ping
from .service import (STATUS_CHANGED, STATUS_ERROR, STATUS_FIRST,
                      STATUS_LABEL, STATUS_MISSING, CycleReport,
                      TargetOutcome)

TIMEOUT = 15.0


# ------------------------------------------------------------------ 消息


@dataclass
class AlertMessage:
    """一条待发的告警。`text` 是纯文本，`markdown` 给支持的通道。"""

    title: str
    text: str

    @property
    def markdown(self) -> str:
        # 钉钉/飞书/企微的 markdown 方言相近，用同一份就够
        return f"### {self.title}\n\n{self.text}"


def _outcome_line(o: TargetOutcome) -> str:
    if o.status == STATUS_ERROR:
        return f"[错误] {o.label}：{o.error}"
    if o.status == STATUS_MISSING:
        return f"[未查到] {o.label}：本轮无匹配结果"
    if o.status == STATUS_FIRST:
        first = o.voyages[0] if o.voyages else None
        return f"[首次] {o.label}：{first.one_line() if first else '(无数据)'}"

    lines = [f"[变更] {o.label}"]
    for v in o.voyages:
        changes = o.changes.get(v.voyage_code, [])
        if not changes:
            continue
        lines.append(f"  航次 {v.voyage_code} · {v.ship_name}")
        lines.append(f"  现在 ETB {v.etb_raw} / ETD {v.etd_raw}")
        for c in changes:
            lines.append(f"  · {c.describe()}")
    return "\n".join(lines)


def build_alert(rep: CycleReport, alert_on: list[str] | None = None) -> AlertMessage | None:
    """把一轮报告里"该叫人的部分"挑出来。没有就返回 None。"""
    wanted = set(alert_on if alert_on is not None else ("changed", "missing", "error"))
    picked = [o for o in rep.outcomes if o.status in wanted]
    if not picked:
        return None

    counts = rep.counts
    parts = [f"{STATUS_LABEL.get(s, s)} {n}" for s, n in counts.items() if n]
    title = f"盐田船期告警 · {' / '.join(parts)}"

    lines = [f"时间：{rep.finished_at}", f"本轮：{rep.summary_line()}", ""]
    for o in picked:
        lines.append(_outcome_line(o))
        lines.append("")
    return AlertMessage(title=title, text="\n".join(lines).rstrip())


# ------------------------------------------------------------------ 去重


class AlertThrottle:
    """冷却去重 + 连续失败计数。

    两个职责放在一起，是因为它们共用同一份持久化状态、生命周期也一样。

    指纹一变（比如 ETB 又改了一次）就立刻放行，所以冷却不会吞掉真实变化，
    只会压掉"同一件事反复报"。
    """

    STATE_VERSION = 2

    def __init__(self, path: str | pathlib.Path, cooldown_seconds: int = 3600,
                 error_after: int = 2):
        self.path = pathlib.Path(path)
        self.cooldown = max(0, int(cooldown_seconds))
        self.error_after = max(1, int(error_after))
        self._state: dict[str, dict] = {}         # 目标 -> {fp, ts}
        self._errors: dict[str, int] = {}         # 目标 -> 连续失败轮数
        self.load()

    # ---------------------------------------------------------- 持久化

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self._state, self._errors = {}, {}
            return
        if not isinstance(raw, dict):
            self._state, self._errors = {}, {}
            return

        if "throttle" in raw or "errors" in raw:      # 当前格式
            self._state = {k: v for k, v in (raw.get("throttle") or {}).items()
                           if isinstance(v, dict)}
            self._errors = _int_map(raw.get("errors"))
        else:                                          # 旧格式：整个文件就是 throttle 表
            self._state = {k: v for k, v in raw.items() if isinstance(v, dict)}
            self._errors = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": self.STATE_VERSION,
                       "throttle": self._state,
                       "errors": self._errors}
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self.path)
        except OSError:
            pass                                  # 存不下就算了，不能影响监控

    # ---------------------------------------------------------- 连续失败

    def note_outcome(self, o: TargetOutcome) -> int:
        """每轮对每个目标调用一次；返回该目标当前连续失败轮数。

        成功一次就清零 —— 我们要的是"一直查不到"，
        不是"历史上错过几次"。
        """
        key = o.target.display()
        if o.status == STATUS_ERROR:
            self._errors[key] = self._errors.get(key, 0) + 1
        else:
            self._errors.pop(key, None)
        return self._errors.get(key, 0)

    def error_streak(self, o: TargetOutcome) -> int:
        return self._errors.get(o.target.display(), 0)

    # ---------------------------------------------------------- 去重

    @staticmethod
    def fingerprint(o: TargetOutcome) -> str:
        if o.status == STATUS_CHANGED:
            body = ";".join(
                f"{vc}:{','.join(c.describe() for c in cs)}"
                for vc, cs in sorted(o.changes.items()))
        elif o.status == STATUS_ERROR:
            body = o.error
        elif o.status == STATUS_MISSING:
            body = "missing"
        else:
            body = ";".join(sorted(v.voyage_code for v in o.voyages))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

    def should_send(self, o: TargetOutcome, now: float | None = None) -> bool:
        # 查询失败要先扛过阈值：一次失败大概率是站点抖动，不是事故
        if o.status == STATUS_ERROR and self.error_streak(o) < self.error_after:
            return False

        key = o.target.display()
        return self.should_send_key(key, self.fingerprint(o), now)

    def mark_sent(self, o: TargetOutcome, now: float | None = None) -> None:
        self.mark_sent_key(o.target.display(), self.fingerprint(o), now)

    # ---- 通用按键去重（心跳/停摆这类非"目标"消息也走同一套冷却）----

    def should_send_key(self, key: str, fingerprint: str,
                        now: float | None = None) -> bool:
        now = time.time() if now is None else now
        prev = self._state.get(key)
        if prev and prev.get("fp") == fingerprint:
            age = now - float(prev.get("ts", 0))
            if age < self.cooldown:
                return False
        return True

    def mark_sent_key(self, key: str, fingerprint: str,
                      now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._state[key] = {"fp": fingerprint, "ts": now}


def _int_map(raw) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


# ------------------------------------------------------------------ 通道


def _post_json(url: str, payload: dict, timeout: float = TIMEOUT) -> str:
    """POST 一个 JSON。返回响应体文本。

    用 requests 而不是 urllib：项目本来就依赖它，
    而且公司网络里常有需要走代理的情况，requests 认环境变量更省事。
    """
    import requests                                   # 延迟导入，保持模块可单独测

    r = requests.post(url, json=payload, timeout=timeout,
                      headers={"User-Agent": "ytmon/0.2"})
    return r.text


def _dingtalk_url(ch: NotifyChannel, now_ms: int | None = None) -> str:
    """加签：sign = base64(hmac_sha256(secret, f"{timestamp}\\n{secret}"))"""
    if not ch.secret:
        return ch.url
    ts = str(int(time.time() * 1000) if now_ms is None else now_ms)
    string_to_sign = f"{ts}\n{ch.secret}".encode("utf-8")
    digest = hmac.new(ch.secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    sep = "&" if "?" in ch.url else "?"
    return f"{ch.url}{sep}timestamp={ts}&sign={sign}"


def _feishu_sign(secret: str, now: float | None = None) -> tuple[str, str]:
    """飞书的签名方式和钉钉相反：时间是"消息"，secret 是"密钥"的键。"""
    ts = str(int(time.time()) if now is None else int(now))
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return ts, base64.b64encode(digest).decode("utf-8")


def send_email(ch: NotifyChannel, msg: AlertMessage,
               timeout: float = TIMEOUT) -> None:
    """走 SMTP 发一封邮件。"""
    m = MIMEText(msg.text, "plain", "utf-8")
    m["Subject"] = Header(msg.title, "utf-8")
    m["From"] = ch.mail_from or ch.smtp_user or "ytmon@localhost"
    m["To"] = ", ".join(ch.mail_to)
    m["Date"] = formatdate(localtime=True)

    if ch.use_ssl:
        server = smtplib.SMTP_SSL(ch.smtp_host, ch.smtp_port, timeout=timeout)
    else:
        server = smtplib.SMTP(ch.smtp_host, ch.smtp_port, timeout=timeout)
    try:
        if not ch.use_ssl:
            try:
                server.starttls()
            except smtplib.SMTPException:
                pass                                  # 服务器不支持 STARTTLS 就明文发
        if ch.smtp_user:
            server.login(ch.smtp_user, ch.smtp_password)
        server.sendmail(m["From"], list(ch.mail_to), m.as_string())
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass


def send_windows(ch: NotifyChannel, msg: AlertMessage) -> None:
    """弹一条 Windows 原生通知（托盘气泡）。

    ⚠️ 需要**已登录的交互式桌面会话**。计划任务勾了"不管用户是否登录"
    就会落在会话 0，弹不出来 —— 这是 Windows 的设计，绕不过去。
    所以它适合"托盘常驻程序"，不适合无人值守的后台任务。
    失败时会抛出带解释的异常，不会假装成功。

    气泡正文上限为 255 个 UTF-16 单元；长消息拆分发送，避免丢失后面的变化。
    """
    if ch.persistent:
        raise RuntimeError("保持显示通知仅支持 GUI，请运行 GUI 或关闭 persistent。")
    from .winnotify import show

    level = "info"
    title = msg.title
    if "错误" in title or "停摆" in title:
        level = "warning"
    chunks = windows_text_chunks(msg.text)
    for index, text in enumerate(chunks, 1):
        suffix = f' ({index}/{len(chunks)})' if len(chunks) > 1 else ''
        show(title[:40] + suffix, text, hold_seconds=ch.hold_seconds, level=level)


def send_linux(ch: NotifyChannel, msg: AlertMessage) -> None:
    """通过当前 Linux 桌面会话的通知服务发送；不等待通知消失。"""
    import shutil
    import subprocess
    import sys

    if not sys.platform.startswith('linux'):
        raise RuntimeError('Linux 桌面通知只能在 Linux 上使用')
    command = shutil.which('notify-send')
    if not command:
        raise RuntimeError('未找到 notify-send；请安装桌面通知客户端')
    args = [command, '-a', '盐田船期监控', '-t', str(round(ch.hold_seconds * 1000)),
            '--', msg.title, msg.text]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('桌面通知服务 10 秒内未响应') from error
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError('桌面通知发送失败（退出码 %s）%s' % (
            result.returncode, '：' + detail[:300] if detail else ''))


def windows_text_chunks(text: str) -> list[str]:
    """按 Windows WCHAR 容量拆分正文，不切断非 BMP 字符。"""
    chunks = []
    current = []
    units = 0
    for char in text:
        size = 2 if ord(char) > 0xffff else 1
        if units + size > 255:
            chunks.append(''.join(current))
            current, units = [], 0
        current.append(char)
        units += size
    if current or not chunks:
        chunks.append(''.join(current))
    return chunks


def build_payload(ch: NotifyChannel, msg: AlertMessage) -> tuple[str, dict]:
    """返回 (url, payload)。email 和 windows 不走这里。"""
    md = msg.markdown
    if ch.kind == "dingtalk":
        return _dingtalk_url(ch), {
            "msgtype": "markdown",
            "markdown": {"title": msg.title, "text": md},
        }
    if ch.kind == "wecom":
        return ch.url, {"msgtype": "markdown", "markdown": {"content": md}}
    if ch.kind == "feishu":
        payload: dict = {"msg_type": "text",
                         "content": {"text": f"{msg.title}\n\n{msg.text}"}}
        if ch.secret:
            ts, sign = _feishu_sign(ch.secret)
            payload["timestamp"] = ts
            payload["sign"] = sign
        return ch.url, payload
    # 通用 webhook：把结构化信息原样给出去，方便对接自己的系统
    return ch.url, {
        "title": msg.title,
        "text": msg.text,
        "source": "ytmon",
        "sent_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------ 总控


@dataclass
class NotifyResult:
    channel: str
    ok: bool
    detail: str = ""


class Notifier:
    """把告警分发给所有启用的通道。

    `transport` 可注入，测试时用来完全离线地断言发出去的内容。
    """

    def __init__(self, channels: list[NotifyChannel], settings,
                 transport=None, on_event=None, windows_transport=None):
        self.channels = [c for c in channels if c.enabled]
        self.settings = settings
        self.transport = transport or _post_json
        self.on_event = on_event
        self.windows_transport = windows_transport or send_windows
        self.throttle = AlertThrottle(
            getattr(settings, "alert_state_file", "state/alert_state.json"),
            getattr(settings, "alert_cooldown_seconds", 3600),
            getattr(settings, "alert_error_after", 2),
        )

    def _emit(self, kind: str, **payload) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:                          # noqa: BLE001
                pass                                   # 事件回调坏了也不能影响发送

    def notify_cycle(self, rep: CycleReport) -> list[NotifyResult]:
        """一轮结束后调用。返回每个通道的结果（没有该发的就是空列表）。

        除了目标级告警，这里还负责**停摆检测与心跳**（见 `ytmon/heartbeat.py`）——
        它们和告警共用同一份冷却状态与同一批通道。
        """
        if not self.channels:
            return []

        # ---- 停摆检测：必须在更新"上次成功"**之前**算，否则间隔永远是 0
        results: list[NotifyResult] = []
        hb_settings = self.settings
        stale_after = float(getattr(hb_settings, "stale_after_hours", 0) or 0)
        heartbeat_hours = float(getattr(hb_settings, "heartbeat_hours", 0) or 0)
        heartbeat_url = str(getattr(hb_settings, "heartbeat_url", "") or "")
        state_file = getattr(hb_settings, "heartbeat_state_file",
                             "state/heartbeat.json")
        hb = HeartbeatState.load(state_file)

        if stale_after > 0:
            reason = hb.stale_reason(stale_after)
            if reason and self.throttle.should_send_key("__stale__", reason):
                msg = AlertMessage(
                    title="盐田船期监控 · 停摆告警",
                    text=(f"{reason}\n\n"
                          f"这不代表船期没变 —— 只代表**这段时间没能查到**。\n"
                          f"请确认机器是否重启过、计划任务是否还在。"))
                results.extend(self.send(msg))
                self.throttle.mark_sent_key("__stale__", reason)
                self.throttle.save()

        # ---- 外部 dead-man ping：每轮都发，代表"进程跑起来了"
        #
        # 注意它和"查询成功"是两件事：站点挂了但进程活着，这里照样 ping。
        # 那是刻意的 —— 外部服务负责"进程还在吗"，查询失败由告警通道负责。
        if heartbeat_url:
            ok, detail = _ping_safe(heartbeat_url)
            self._emit("log", level="debug" if ok else "warn",
                       message=f"心跳 ping {'成功' if ok else '失败'}：{detail}")

        # ---- 记账：本轮算成功还是失败
        succeeded = bool(rep.outcomes) and all(
            o.status != STATUS_ERROR for o in rep.outcomes)
        if succeeded:
            hb.note_success()
        else:
            hb.note_failure()

        # ---- 定期"我还活着"
        if heartbeat_hours > 0 and hb.heartbeat_due(heartbeat_hours):
            summary = (f"监控正常运行中。\n"
                       f"本轮：{rep.summary_line()}\n"
                       f"目标 {len(rep.outcomes)} 个。")
            hb_ok, hb_detail = (True, "")
            if heartbeat_url:
                hb_ok, hb_detail = _ping_safe(heartbeat_url)
            msg = AlertMessage(title="盐田船期监控 · 心跳", text=summary)
            results.extend(self.send(msg))
            hb.mark_heartbeat()
            if heartbeat_url and not hb_ok:
                self._emit("log", level="warn",
                           message=f"心跳 ping 失败：{hb_detail}")

        return results + self._notify_targets(rep)

    def _notify_targets(self, rep: CycleReport) -> list[NotifyResult]:

        alert_on = list(getattr(self.settings, "alert_on", None)
                        or ("changed", "missing", "error"))
        wanted = set(alert_on)

        # 必须先记账再判断：连续失败次数的更新要发生在 should_send 之前
        for o in rep.outcomes:
            self.throttle.note_outcome(o)

        candidates = [o for o in rep.outcomes if o.status in wanted]
        if not candidates:
            self.throttle.save()            # 成功轮要把清零后的计数落盘
            return []

        fresh = [o for o in candidates if self.throttle.should_send(o)]
        if not fresh:
            self.throttle.save()            # 连续失败次数要落盘，否则永远攒不够
            pending = [o for o in candidates if o.status == STATUS_ERROR]
            if pending:
                n = self.throttle.error_streak(pending[0])
                self._emit("log", level="warn",
                           message=f"查询失败第 {n} 次（连续 {self.throttle.error_after} "
                                   f"次才告警），本轮先不发")
            else:
                self._emit("log", level="info",
                           message="告警仍在冷却期内（内容没变），本轮不重复发送")
            return []

        limit = max(1, int(getattr(self.settings, "alert_max_per_cycle", 5)))
        results = []
        # 旧上限改为每批大小，不能丢弃同轮后面的真实变化。
        for start in range(0, len(fresh), limit):
            batch = fresh[start:start + limit]
            msg = build_alert(_subset(rep, batch), alert_on)
            if msg is None:
                continue
            sent = self.send(msg)
            results.extend(sent)
            # 仅给至少一个渠道成功送出的这一批记账。
            if any(r.ok for r in sent):
                for o in batch:
                    self.throttle.mark_sent(o)
        self.throttle.save()
        return results

    def send(self, msg: AlertMessage) -> list[NotifyResult]:
        """把一条消息发给所有启用的通道（供 --test-alert 和心跳用）。"""
        out: list[NotifyResult] = []
        for ch in self.channels:
            try:
                if ch.kind == "email":
                    send_email(ch, msg)
                elif ch.kind == "windows":
                    self.windows_transport(ch, msg)
                elif ch.kind == "linux":
                    send_linux(ch, msg)
                else:
                    url, payload = build_payload(ch, msg)
                    body = self.transport(url, payload)
                    problem = _channel_error(ch.kind, body)
                    if problem:
                        raise RuntimeError(problem)
                out.append(NotifyResult(ch.display(), True))
                self._emit("alert_sent", channel=ch.display(), ok=True)
            except Exception as e:                     # noqa: BLE001 —— 告警绝不许冒泡
                detail = f"{type(e).__name__}: {e}"
                out.append(NotifyResult(ch.display(), False, detail))
                self._emit("alert_sent", channel=ch.display(), ok=False,
                           message=f"{ch.display()} 发送失败：{detail}")
        return out


def _ping_safe(url: str) -> tuple[bool, str]:
    """ping 的**兜底**包装。

    `heartbeat.ping` 内部已经 catch 了异常，但这里再包一层不是多余的：
    模块文档里写了"失败绝不能影响监控"，那就不能依赖被调用方的实现细节 ——
    哪天有人在 ping 里加了一行会抛的代码（或换了实现），
    这里就是最后一道防线。
    """
    try:
        return ping(url)
    except Exception as e:                                  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _subset(rep: CycleReport, outcomes: list[TargetOutcome]) -> CycleReport:
    return CycleReport(started_at=rep.started_at, finished_at=rep.finished_at,
                       etb_time=rep.etb_time, outcomes=list(outcomes))


def _channel_error(kind: str, body: str) -> str:
    """各家的机器人失败时也返回 HTTP 200，只能看响应体里的错误码。

    识别不出来就放过 —— 宁可漏报"发送失败"，也不要因为解析不出来
    把成功的消息判成失败而反复重试。
    """
    if not body:
        return ""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    if kind == "dingtalk":
        code = data.get("errcode")
        if code not in (None, 0):
            return f"钉钉返回 errcode={code} {data.get('errmsg', '')}"
    elif kind == "wecom":
        code = data.get("errcode")
        if code not in (None, 0):
            return f"企业微信返回 errcode={code} {data.get('errmsg', '')}"
    elif kind == "feishu":
        code = data.get("code")
        if code not in (None, 0):
            return f"飞书返回 code={code} {data.get('msg', '')}"
    return ""


# ------------------------------------------------------------------ 自检


def test_message() -> AlertMessage:
    return AlertMessage(
        title="盐田船期监控 · 测试消息",
        text=("这是一条测试告警。\n"
              "如果你看到它，说明这条通道配通了。\n"
              f"发送时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}"),
    )


def describe(channels: list[NotifyChannel]) -> list[str]:
    """给自检/CLI 用的人类可读描述（**不打印 url 里的 token**）。"""
    out = []
    for c in channels:
        state = "启用" if c.enabled else "停用"
        if c.kind == "windows":
            target = _windows_target()
        elif c.kind == "linux":
            target = 'Linux 桌面通知（notify-send）'
        elif c.url:
            target = _mask(c.url)
        else:
            target = c.smtp_host or ""
        out.append(f"{c.display()}（{c.kind}，{state}）→ {target}")
    return out


def _windows_target() -> str:
    """windows 通道的可读目标描述，并提前把"弹不出来"的原因说出来。

    会话 0（无人登录的计划任务）永远弹不出 —— 与其让用户对着一个
    静默失败的通道猜，不如在自检这一步就讲清楚。
    """
    try:
        from .winnotify import probe
        ok, detail = probe()
    except Exception as e:                                  # noqa: BLE001
        return f"系统通知（探测失败：{type(e).__name__}）"
    if ok:
        return "系统托盘通知"
    # 这里刻意不用 ⚠ 之类的符号：它们不在 GBK 里，
    # 一旦有人从没调 ensure_safe_stdout 的脚本里打印就会崩（真踩过）。
    return f"系统托盘通知 [弹不出来] {detail.splitlines()[0]}"


def _mask(url: str) -> str:
    """把 url 里的 access_token 之类遮掉，避免日志/截图泄露。"""
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url[:20] + "…"
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    masked = [(k, ("***" if any(s in k.lower() for s in
                                ("token", "key", "secret", "sign", "password"))
                   else v)) for k, v in query]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path,
         urllib.parse.urlencode(masked), ""))
