"""UI 无关的同步监控服务。通过事件回调上报进度，GUI 应在工作线程调用。"""

from __future__ import annotations

import datetime as dt
import pathlib
import random
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from .config import AppConfig, Target
from .errors import RateLimited
from .http_client import (HttpClient, SessionExpired,
                          bootstrap_cookies, is_fatal_error,
                          load_cached_cookies)
from .matching import MATCH_NONE, match_rows
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
        return HttpClient(cookies,
                          cache_file=self.cfg.settings.cookie_cache,
                          boot=self._boot_kwargs())

    def ensure_client(self, allow_bootstrap: bool = True) -> HttpClient:
        """复用查询会话；缓存缺失时引导浏览器 Cookie。"""
        if self._client is None:
            cached = load_cached_cookies(self.cfg.settings.cookie_cache)
            if cached is None:
                if not allow_bootstrap:
                    raise SessionExpired("cookie 缓存缺失或过期")
                self._log("正在建立查询会话…")
                cached = bootstrap_cookies(**self._boot_kwargs())
                self._log("查询会话已建立")
            self._client = self._new_http_client(cached)
        return self._client

    def close(self) -> None:
        self._client = None

    # ------------------------------------------------------------ 抓取

    def _client_call(self, fn, *args, **kwargs):
        """限流走退避重试；会话失效重新引导 Cookie。"""
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
        """会话失效后重新引导一次；仍失败则交给下一轮处理。"""
        try:
            return fn(client, *args, **kwargs)
        except SessionExpired as e:
            self._log(f"查询会话失效，正在重新建立（{e}）", "warn")
            cookies = bootstrap_cookies(**self._boot_kwargs())
            client.set_cookies(cookies)
            self._client = client
            return fn(client, *args, **kwargs)

    def _backoff_seconds(self, attempt: int) -> float:
        """指数退避，并加入随机抖动。"""
        base = float(self.cfg.settings.retry_backoff_seconds) * (2 ** attempt)
        return base + random.uniform(0, base * 0.5)

    def run_cycle(self, etb_time: str | None = None) -> CycleReport:
        started = dt.datetime.now()
        s = self.cfg.settings
        etb = etb_time or _default_etb(s.etb_back_days)
        store = StateStore(s.state_file).load()
        outcomes: list[TargetOutcome] = []

        targets = self.cfg.enabled_targets
        self._emit("cycle_start", total=len(targets), etb_time=etb)

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
        rep = CycleReport(started.isoformat(timespec="seconds"),
                          dt.datetime.now().isoformat(timespec="seconds"),
                          etb, outcomes)
        self._emit("cycle_done", report=rep)
        return rep

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
