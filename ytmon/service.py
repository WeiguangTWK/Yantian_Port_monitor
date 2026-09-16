"""服务层：UI 无关的编排逻辑。

这一层是 GUI 的基座 —— 它**不做任何打印**，只返回结构化结果，
并通过 `on_event` 回调把进度抛给调用方。

  * CLI  → 把事件和结果渲染成文本
  * GUI  → 把事件推到进度条/日志面板，把结果绑到表格

所有方法都是同步的，GUI 请在 worker 线程里调用（不要阻塞 UI 线程），
因为内部会做网络请求，首次还会拉起一次浏览器做 cookie 引导。
"""

from __future__ import annotations

import datetime as dt
import pathlib
import random
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from .auth import Authenticator, RenewResult, load_credentials
from .config import AppConfig, Target
from .errors import RateLimited
from .http_client import (HttpClient, SessionExpired, TokenStatus,
                          bootstrap_cookies, is_fatal_error,
                          load_cached_cookies)
from .matching import MATCH_FUZZY, MATCH_NONE, match_rows
from .parse import Voyage
from .store import FieldChange, StateStore, compare, target_key

EventHook = Callable[[str, dict], None]

STATUS_FIRST = "first"
STATUS_CHANGED = "changed"
STATUS_SAME = "same"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"

STATUS_LABEL = {
    STATUS_FIRST: "首次",
    STATUS_CHANGED: "变更",
    STATUS_SAME: "无变化",
    STATUS_MISSING: "未查到",
    STATUS_ERROR: "错误",
}

# 预检查（settings.precheck，默认关闭）里"必然导致后续也失败"的原因。
# 只用于决定要不要提前中止；正常路径不依赖它。
FATAL_REASONS = ("waf", "network", "ratelimited")


@dataclass
class TargetOutcome:
    target: Target
    status: str
    voyages: list[Voyage] = field(default_factory=list)
    changes: dict[str, list[FieldChange]] = field(default_factory=dict)
    match_mode: str = MATCH_NONE
    error: str = ""
    # 这个失败是否意味着"换下一个目标也一定失败"（网络断/WAF/限流）。
    # 用来在站点异常时停止继续加压。
    fatal: bool = False

    @property
    def label(self) -> str:
        return self.target.display()

    @property
    def changed(self) -> bool:
        return self.status == STATUS_CHANGED


@dataclass
class CycleReport:
    started_at: str
    finished_at: str
    etb_time: str
    outcomes: list[TargetOutcome]
    token_status: TokenStatus | None = None
    token_renewed: bool = False

    @property
    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in STATUS_LABEL}
        for o in self.outcomes:
            c[o.status] = c.get(o.status, 0) + 1
        return c

    @property
    def has_changes(self) -> bool:
        return any(o.changed for o in self.outcomes)

    @property
    def alerted(self) -> list[TargetOutcome]:
        """需要提醒用户的：变更 + 未查到（船消失了也可能是问题）。"""
        return [o for o in self.outcomes
                if o.status in (STATUS_CHANGED, STATUS_MISSING, STATUS_ERROR)]

    def summary_line(self) -> str:
        c = self.counts
        return (f"变更 {c[STATUS_CHANGED]} / 首次 {c[STATUS_FIRST]} / "
                f"无变化 {c[STATUS_SAME]} / 未查到 {c[STATUS_MISSING]}"
                + (f" / 错误 {c[STATUS_ERROR]}" if c[STATUS_ERROR] else ""))


class MonitorService:
    """一次运行内复用 HTTP 会话；浏览器只在 cookie 失效时拉起。"""

    def __init__(self, cfg: AppConfig, on_event: EventHook | None = None,
                 dump_dir: str | None = None):
        self.cfg = cfg
        self.on_event = on_event or (lambda kind, payload: None)
        self.dump_dir = dump_dir
        self._client: HttpClient | None = None
        # 记下"配置里原本有没有 token"，决定续期后要不要写回文件
        self._config_had_token = bool((cfg.token or "").strip())
        self._auto_renew = True
        # 被限流时的重试次数（退避后才重试，且**不会**重新引导 cookie）
        self._retries = max(0, int(getattr(cfg.settings, "retry_attempts", 1)))

    # ------------------------------------------------------------ 事件

    def _emit(self, kind: str, **payload) -> None:
        try:
            self.on_event(kind, payload)
        except Exception:                     # noqa: BLE001  UI 回调不该拖垮监控
            pass

    def _log(self, msg: str, level: str = "info") -> None:
        self._emit("log", message=msg, level=level)

    # ------------------------------------------------------------ 会话

    @property
    def token(self) -> str:
        return (self.cfg.token or "").strip()

    def _boot_kwargs(self) -> dict:
        s = self.cfg.settings
        return {
            "profile_dir": s.profile_dir,
            "edge_path": s.edge_path,
            "headless": s.headless,
            "cache_file": s.cookie_cache,
        }

    def _new_http_client(self, cookies: dict[str, str]) -> HttpClient:
        # 注意：profile_dir/edge_path/headless 是给"重新引导"用的，
        # 必须通过 boot= 传，不能直接当构造参数。
        return HttpClient(self.token, cookies,
                          cache_file=self.cfg.settings.cookie_cache,
                          boot=self._boot_kwargs())

    def ensure_client(self, allow_bootstrap: bool = True) -> HttpClient:
        """确保有一个可用的 HTTP 客户端（cookie 有效）。

        注意：**不要求 token**。公众船期查询不需要 token，
        真正的门槛是用浏览器过一次 EdgeOne 挑战、拿到 cookie。
        """
        if self._client is None:
            cached = load_cached_cookies(self.cfg.settings.cookie_cache)
            if cached is None:
                if not allow_bootstrap:
                    raise SessionExpired("cookie 缓存缺失或过期")
                self._log("cookie 缺失/过期，正在用浏览器引导（首次约 10 秒）…")
                cached = bootstrap_cookies(self.token, **self._boot_kwargs())
                self._log(f"cookie 引导完成：{', '.join(cached)}")
            self._client = self._new_http_client(cached)
        return self._client

    def check_token(self, rebootstrap_on_waf: bool = True) -> TokenStatus:
        """**会话级**预检查（不是 token 有效性检查）。

        ⚠️ 它只发一次 GET。实测发现伪造的 token 也会正常返回查询页，
        所以本方法**判不出 token 是否有效** —— 它只能回答：
            "cookie/会话还通不通、查询页拿不拿得到"。

        真正需要授权的地方是 POST 查询，因此续期逻辑挂在
        `_client_call` 的实际失败上，而不是挂在这里的判定结果上。
        """
        client = self.ensure_client()
        st = client.check_token()
        if st.reason == "waf" and rebootstrap_on_waf:
            self._log("被 EdgeOne 拦截，重新引导 cookie 后复检…", "warn")
            try:
                client.rebootstrap()
                self._client = client
                st = client.check_token()
            except Exception as e:                      # noqa: BLE001
                return TokenStatus(False, "network", f"重新引导失败：{e}")
        return st

    def renew_token(self) -> RenewResult:
        """用账号密码续期 token（两个会话，见 auth 模块说明）。"""
        creds = load_credentials()
        if not creds:
            return RenewResult(False, None,
                               "没有可用凭证：请设置环境变量 YT_USER / YT_PASS")
        username, password = creds
        self._log(f"正在用账号 {username[:2]}*** 续期 token…")
        try:
            cookies = load_cached_cookies(self.cfg.settings.cookie_cache)
            if cookies is None:
                cookies = bootstrap_cookies(self.token or "x", **self._boot_kwargs())
            auth = Authenticator.from_cookies(cookies)
            result = auth.renew(username, password)
        except Exception as e:                          # noqa: BLE001
            return RenewResult(False, None, f"续期异常：{type(e).__name__}: {e}")

        if result.ok and result.token:
            self.cfg.token = result.token
            # token 变了，旧会话作废，下次会重建
            self._client = None
            self._log(result.detail, "info")
        else:
            self._log(result.detail, "error")
        return result

    def close(self) -> None:
        self._client = None

    # ------------------------------------------------------------ 抓取

    def _client_call(self, fn, *args, **kwargs):
        """执行一次查询。两层策略刻意分开：

            **限流** → 退避等待后重试（外层）
            **会话失效** → 重新引导 cookie / 续期 token（内层）

        为什么必须分开：被限流时"重新引导 cookie"是**有害**的 ——
        起一次浏览器本身就是一串请求，只会把自己推得更深。
        这两类问题在响应上都表现为"拿回来的不是结果页"，
        所以必须在状态码那一步就把它们拆开（见 http_client.query_page）。
        """
        client = self.ensure_client()
        for attempt in range(self._retries + 1):
            try:
                return self._call_with_session_heal(fn, client, *args, **kwargs)
            except RateLimited as e:
                if attempt >= self._retries:
                    raise
                wait = self._backoff_seconds(attempt)
                self._log(f"被站点限流（{e}）—— 退避 {wait:.1f} 秒后重试"
                          f"（{attempt + 1}/{self._retries}）", "warn")
                time.sleep(wait)

    def _call_with_session_heal(self, fn, client, *args, **kwargs):
        """会话级自愈：重引导 cookie → （可选）续期 token。

        为什么把"续期"放在这里而不是靠预检查：
            实测发现 `check_token`（只发 GET）**无法判别 token 是否有效** ——
            伪造 token 也会返回正常的查询页。
            所以"token 过期"其实不可靠地可观测。与其依赖一个假的探测，
            不如把续期绑在**真实查询失败**这个可信信号上。
        """
        try:
            return fn(client, *args, **kwargs)
        except SessionExpired as e:
            self._log(f"会话失效（{e}），重新引导 cookie 后重试…", "warn")
            cookies = bootstrap_cookies(self.token, **self._boot_kwargs())
            client.set_cookies(cookies)
            self._client = client
            try:
                return fn(client, *args, **kwargs)
            except SessionExpired as e2:
                if not self._auto_renew:
                    raise
                if not self._has_credentials():
                    # 实测：公众船期查询不需要 token，所以这条自愈路径本来
                    # 就不是必需的。没配账号时硬走一遍，只会打印出"续期失败"
                    # 这种看起来像配置错误的假象 —— 实际上这多半是站点临时
                    # 降级或限流（HTTP 567），下一轮自己就好了。
                    self._log(f"重新引导后仍失败（{e2}）。未配置账号凭证，"
                              f"跳过 token 续期（公众查询本就不需要 token）—— "
                              f"这通常是站点临时降级或限流，下一轮会自行恢复。", "warn")
                    raise
                self._log(f"重新引导后仍失败（{e2}），尝试续期 token…", "warn")
                rr = self.renew_token()
                if not rr.ok:
                    self._log(f"续期失败：{rr.detail}", "error")
                    raise
                self._persist_token_if_configured()
                self._client = None
                return fn(self.ensure_client(), *args, **kwargs)

    def _backoff_seconds(self, attempt: int) -> float:
        """退避时长：指数增长 + 抖动。

        抖动不是装饰 —— 计划任务和 --watch 循环很容易卡在同一个相位上，
        没有抖动的话每次重试都会同时打到站点，反而更像攻击。
        """
        base = float(self.cfg.settings.retry_backoff_seconds) * (2 ** attempt)
        return base + random.uniform(0, base * 0.5)

    def _has_credentials(self) -> bool:
        """有没有可用于续期的账号。**只判断存在性，不读取内容。**"""
        try:
            return bool(load_credentials())
        except Exception:                               # noqa: BLE001
            return False

    def run_cycle(self, etb_time: str | None = None,
                  auto_renew: bool = True) -> CycleReport:
        self._auto_renew = auto_renew
        started = dt.datetime.now()
        s = self.cfg.settings
        etb = etb_time or _default_etb(s.etb_back_days)
        store = StateStore(s.state_file).load()
        outcomes: list[TargetOutcome] = []

        targets = self.cfg.enabled_targets
        self._emit("cycle_start", total=len(targets), etb_time=etb)

        # 预检查默认**关闭**。
        #
        # 它原本是发一次 GET 来"提前发现 WAF/网络问题"，但实测有两个问题：
        #   1. 它恒报 expired（连一切正常时也是），信息量为零；
        #   2. 它每轮都多打一次站点 —— 而限流正是这套系统最大的运行风险。
        # 收益（早停）已经由下面"第一个目标失败就停"覆盖，且**零额外请求**。
        # 需要它时（比如排障）把 settings.precheck 设为 true。
        token_status = None
        if getattr(s, "precheck", False):
            token_status = self.check_token()
            self._emit("token_status", status=token_status)
            if not token_status.ok and token_status.reason in FATAL_REASONS:
                self._log(f"预检查失败（{token_status.reason}）：{token_status.detail}",
                          "error")
                rep = CycleReport(started.isoformat(timespec="seconds"),
                                  dt.datetime.now().isoformat(timespec="seconds"),
                                  etb, outcomes, token_status)
                self._emit("cycle_done", report=rep)
                return rep
            if not token_status.ok:
                self._log(f"预检查提示 {token_status.reason}（{token_status.detail}）—— "
                          f"这不是可靠判据，继续实际查询", "debug")

        for idx, t in enumerate(targets, 1):
            self._emit("target_start", index=idx, total=len(targets), target=t)
            outcome = self._run_target(t, etb, store)
            outcomes.append(outcome)
            self._emit("target_done", index=idx, total=len(targets), outcome=outcome)

            # 第一个目标就撞上"继续下去必然也失败"的问题 → 不再打后面的目标。
            # 这与被删掉的 GET 预检查是同一个目的，但不额外发请求。
            if len(outcomes) == 1 and outcome.fatal and len(targets) > 1:
                self._log(f"首个目标即失败（{outcome.error}），"
                          f"本轮不再查询其余 {len(targets) - 1} 个目标"
                          f"（避免在站点异常/限流时继续加压）", "warn")
                break

        store.save()
        # 报告里的 token_status 若只是"预检查可疑"，而实际查询都成功了，
        # 就该如实反映实际结果 —— 否则报告会自相矛盾。
        if (token_status is not None and not token_status.ok and outcomes
                and all(o.status != STATUS_ERROR for o in outcomes)):
            token_status = TokenStatus(True, "ok",
                                       f"查询实际成功（预检查提示 {token_status.reason}，已忽略）")
        rep = CycleReport(started.isoformat(timespec="seconds"),
                          dt.datetime.now().isoformat(timespec="seconds"),
                          etb, outcomes, token_status)
        self._emit("cycle_done", report=rep)
        return rep

    def _persist_token_if_configured(self) -> None:
        """把续期得到的新 token 写回配置文件。

        只在该文件**本来就有 token** 时才写 —— 也就是说用户已经选择把 token
        存在那里。若用户走的是环境变量，就不该偷偷往文件里落一个凭证。
        """
        if not self._config_had_token:
            self._log("配置里原本没有 token（走环境变量），新 token 仅保留在内存中")
            return
        try:
            path = self.cfg.save()
            self._log(f"新 token 已写回 {path}")
        except Exception as e:                               # noqa: BLE001
            self._log(f"写回 token 失败（不影响本次运行）：{e}", "warn")

    def _run_target(self, t: Target, etb: str, store: StateStore) -> TargetOutcome:
        s = self.cfg.settings
        key = target_key(t.type, t.value)
        kwargs = ({"ship_name": t.value} if t.type == "ship"
                  else {"voyage_code": t.value})
        try:
            result = self._client_call(
                lambda c: c.query_all(etb_time=etb, max_pages=s.max_pages, **kwargs))
        except Exception as e:                          # noqa: BLE001
            self._log(f"{t.display()} 查询失败：{type(e).__name__}: {e}", "error")
            return TargetOutcome(t, STATUS_ERROR, error=f"{type(e).__name__}: {e}",
                                 fatal=is_fatal_error(e))

        matched, mode = match_rows(result.rows, t.type, t.value)
        self._dump(t, result.raw)
        if not matched:
            store.mark_missing(key, f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} 无匹配")
            return TargetOutcome(t, STATUS_MISSING, match_mode=MATCH_NONE)

        changes: dict[str, list[FieldChange]] = {}
        status = STATUS_SAME
        for v in matched:
            row_key = key if len(matched) == 1 else f"{key}::{v.voyage_code}"
            prev = store.get(row_key)
            if prev is None:
                changes[v.voyage_code] = []
                status = STATUS_FIRST if status == STATUS_SAME else status
            else:
                ch = compare(prev.get("record"), v)
                changes[v.voyage_code] = ch
                if ch:
                    status = STATUS_CHANGED
            store.record(row_key, t.type, t.value, v)

        return TargetOutcome(t, status, voyages=matched, changes=changes, match_mode=mode)

    # ------------------------------------------------------------ GUI 辅助

    def _dump(self, t: Target, html: str) -> None:
        """留原始 HTML 快照，便于事后复核与解析器回归。"""
        if not self.dump_dir or not html:
            return
        try:
            d = pathlib.Path(self.dump_dir)
            d.mkdir(parents=True, exist_ok=True)
            safe = "".join(c if c.isalnum() else "_" for c in f"{t.type}_{t.value}")
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            (d / f"{safe}_{stamp}.html").write_text(html, "utf-8")
        except OSError as e:
            self._log(f"快照写入失败：{e}", "warn")

    def snapshot(self) -> dict:
        """给 GUI 表格用的当前监控状态（只读）。"""
        store = StateStore(self.cfg.settings.state_file).load()
        rows = []
        for t in self.cfg.targets:
            key = target_key(t.type, t.value)
            entry = store.get(key) or {}
            rec = entry.get("record") or {}
            rows.append({
                "label": t.display(),
                "type": t.type,
                "value": t.value,
                "enabled": t.enabled,
                "voyage_code": rec.get("voyage_code", ""),
                "ship_name": rec.get("ship_name", ""),
                "etb": rec.get("etb_raw", ""),
                "etd": rec.get("etd_raw", ""),
                "gate": rec.get("gate", ""),
                "agent": rec.get("agent", ""),
                "last_seen": entry.get("last_seen", ""),
                "history": len(entry.get("history", [])),
            })
        return {"targets": rows,
                "state_file": self.cfg.settings.state_file}


def _default_etb(back_days: int) -> str:
    return (dt.date.today() - dt.timedelta(days=back_days)).strftime("%Y%m%d")


def safe_repr(e: BaseException) -> str:
    return "".join(traceback.format_exception_only(type(e), e)).strip()
