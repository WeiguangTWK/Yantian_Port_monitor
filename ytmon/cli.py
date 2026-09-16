"""命令行查询、循环监控及通知编排。"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
import time

from .config import DEFAULT_CONFIG_PATH, AppConfig
from .matching import MATCH_FUZZY
from .notify import Notifier, describe, test_message
from .service import (STATUS_CHANGED, STATUS_ERROR, STATUS_FIRST,
                      STATUS_MISSING, STATUS_SAME, CycleReport,
                      MonitorService, TargetOutcome)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_QUERY = 3
EXIT_CHANGED = 10          # 有变更：供计划任务/监控系统触发告警

MAX_REPORT_ROWS = 10


# ------------------------------------------------------------------ 渲染


def emit(line: str = "") -> None:
    print(line, flush=True)


def render_outcome(o: TargetOutcome, max_rows: int = MAX_REPORT_ROWS) -> None:
    label = o.label

    if o.status == STATUS_ERROR:
        emit(f"[错误] {label}：{o.error}")
        return
    if o.status == STATUS_MISSING:
        emit(f"[未查到] {label}：本轮无匹配结果"
             f"（可能已离港，或超出站点约 30 天的发布窗口）")
        return

    shown = o.voyages[:max_rows]
    for v in shown:
        changes = o.changes.get(v.voyage_code, [])
        if o.status == STATUS_FIRST:
            emit(f"[首次] {label}：{v.one_line()}")
        elif changes:
            emit(f"[变更] {label}")
            emit(f"        {v.one_line()}")
            for c in changes:
                emit(f"        · {c.describe()}")
        else:
            emit(f"[无变化] {label}：航次 {v.voyage_code} | "
                 f"ETB {v.etb_raw} | ETD {v.etd_raw}")

    if len(o.voyages) > len(shown):
        emit(f"        …另有 {len(o.voyages) - len(shown)} 条命中未显示"
             f"（共 {len(o.voyages)} 条）")
    if o.match_mode == MATCH_FUZZY:
        emit(f"        （注意：{label} 为模糊命中，站点按子串匹配，"
             f"请改用更完整的船名以精确定位）")


def render_report(rep: CycleReport) -> None:
    for o in rep.outcomes:
        render_outcome(o)
    emit(f"—— 本轮结束：{rep.summary_line()} ——")


# ------------------------------------------------------------------ 单轮


def make_event_handler(quiet: bool, verbose: bool = False):
    """按 quiet 和 verbose 设置显示事件日志。"""
    def handler(kind: str, payload: dict) -> None:
        if kind == "log":
            level = payload.get("level", "info")
            message = payload.get("message", "")
            if level == "debug" and not verbose:
                return
            if quiet and level == "info":
                return
            emit(f"        {message}")
        elif kind == "alert_sent":
            if payload.get("ok"):
                emit(f"        [告警] 已发出 → {payload.get('channel')}")
            else:
                emit(f"        [告警] {payload.get('message')}")
    return handler


def _handler_for(args) -> object:
    """统一从 args 构造事件渲染器（quiet / verbose 都从命令行来）。"""
    return make_event_handler(getattr(args, "quiet", False),
                              getattr(args, "verbose", False))


def build_notifier(cfg: AppConfig, args) -> Notifier | None:
    """没配通道、或显式 --no-notify 时返回 None。"""
    if getattr(args, "no_notify", False):
        return None
    channels = cfg.enabled_channels
    if not channels:
        return None
    return Notifier(channels, cfg.settings, on_event=_handler_for(args))


def run_test_alert(cfg: AppConfig, args) -> int:
    """向启用通道发送测试消息并报告结果。"""
    channels = cfg.enabled_channels
    if not channels:
        emit("没有配置任何启用的告警通道（watchlist.json 里的 notify 段是空的）。")
        emit("在 watchlist.example.json 里能看到各家通道的写法。")
        return EXIT_ERROR

    emit(f"—— 告警通道自检 · {len(channels)} 个通道 ——")
    for line in describe(cfg.notify):
        emit(f"    {line}")
    emit("")

    notifier = Notifier(channels, cfg.settings, on_event=_handler_for(args))
    results = notifier.send(test_message())

    ok = 0
    for r in results:
        if r.ok:
            ok += 1
            emit(f"  [✓] {r.channel}")
        else:
            emit(f"  [✗] {r.channel}：{r.detail}")
    emit("")
    emit(f"—— 成功 {ok} / {len(results)} ——")
    return EXIT_OK if ok == len(results) else EXIT_ERROR


def run_once(cfg: AppConfig, args) -> int:
    handler = _handler_for(args)
    service = MonitorService(cfg, on_event=handler, dump_dir=args.dump_html)
    notifier = build_notifier(cfg, args)

    emit(f"—— 船期监控 {dt.datetime.now():%Y-%m-%d %H:%M:%S} "
         f"| 目标 {len(cfg.enabled_targets)} 个 ——")

    rep = service.run_cycle(etb_time=args.etb_time)

    # 告警独立于终端输出：quiet 只影响屏幕，不影响有没有人收到通知
    if notifier is not None:
        notifier.notify_cycle(rep)

    if args.quiet and not rep.has_changes:
        return EXIT_OK

    render_report(rep)

    if any(o.status == STATUS_ERROR for o in rep.outcomes):
        return EXIT_QUERY
    return EXIT_CHANGED if rep.has_changes else EXIT_OK


# ------------------------------------------------------------------ CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ytmon",
        description="按船名或码头航次监控船期变更。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python run_monitor.py                          # 按 watchlist.json 跑一轮
  python run_monitor.py --watch 600              # 每 10 分钟跑一轮
  python run_monitor.py --quiet                  # 只输出变更/异常
  python run_monitor.py --test-alert             # 先测告警通道通不通
  python run_monitor.py --no-notify              # 这轮不发告警

退出码：0=无变更  10=有变更  1=错误  3=查询失败
""")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p.add_argument("--etb-time", default=None, help="查询起始日 YYYYMMDD（默认 今天-N 天）")
    p.add_argument("--watch", type=int, default=0, metavar="秒",
                   help="循环监控，每 N 秒一轮；不填只跑一轮")
    p.add_argument("--quiet", action="store_true", help="只输出变更与异常")
    p.add_argument("--verbose", action="store_true",
                   help="显示调试信息")
    p.add_argument("--show-browser", action="store_true", help="引导 cookie 时显示浏览器窗口")
    p.add_argument("--dump-html", default=None, metavar="目录",
                   help="保存首分页 HTML 快照供复核")
    p.add_argument("--no-notify", action="store_true",
                   help="本轮不发告警（即使 watchlist.json 里配了通道）")
    p.add_argument("--test-alert", action="store_true",
                   help="给每个告警通道发一条测试消息，然后退出")
    return p


def main(argv: list[str] | None = None) -> int:
    # 中文 Windows 下重定向输出时，✓/✗ 等符号不在 GBK 里会直接抛异常，
    # 把整个监控打断。见 ytmon/console.py。
    from .console import ensure_safe_stdout
    ensure_safe_stdout()

    # 冻结成 exe 后 CWD 不可信（双击 / 快捷方式 / 计划任务各不相同），
    # 而 state/、.cache/、.browser_profile/ 都是相对 CWD 解析的。
    # 源码运行时 anchor_to_app_dir() 什么都不做。
    from .paths import anchor_to_app_dir, writable_warning
    warn = writable_warning(anchor_to_app_dir())
    if warn:
        print(warn, file=sys.stderr)

    args = build_parser().parse_args(argv)
    try:
        cfg = AppConfig.load(args.config)
    except FileNotFoundError as e:
        print(f"{e}\n        请复制 watchlist.example.json 为 watchlist.json 后填写监控目标。",
              file=sys.stderr)
        return EXIT_ERROR

    if args.test_alert:
        # 自检只关心"通道配得对不对"，所以**不**因为目标没填好而拒绝。
        # 但拼写错误必须查 —— 那属于配置完整性，跟目标无关，
        # 而且静默跳过会让人永远发现不了。
        errs = cfg.validate_unknown_keys() + cfg.validate_channels()
        if errs:
            print("[配置有问题]", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            return EXIT_ERROR
        target_problems = cfg.validate_targets()
        if target_problems:
            emit("（注意：监控目标本身还有问题，正式运行前要修好；"
                 "自检不检查目标）")
            for e in target_problems:
                emit(f"    - {e}")
            emit("")
        return run_test_alert(cfg, args)

    errs = cfg.validate()
    if errs:
        print("[配置有问题]", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return EXIT_ERROR

    if args.show_browser:
        cfg.settings.headless = False

    if args.watch <= 0:
        return run_once(cfg, args)

    emit(f"进入循环监控，每 {args.watch} 秒一轮（Ctrl+C 退出）")
    jitter_max = float(getattr(cfg.settings, "watch_jitter_seconds", 0) or 0)
    if jitter_max > 0:
        emit(f"        每轮额外随机等待 0–{jitter_max:.0f} 秒（抖动），"
             f"避免固定间隔在站点那边累积成规律流量")
    while True:
        started = dt.datetime.now()
        try:
            run_once(cfg, args)
        except KeyboardInterrupt:
            return EXIT_OK
        elapsed = (dt.datetime.now() - started).total_seconds()
        # 抖动不是装饰：固定间隔 + 计划任务很容易和站点限流窗口对齐，
        # 每次都撞在同一个相位上，看起来更像爬虫。
        jitter = random.uniform(0, jitter_max) if jitter_max > 0 else 0.0
        try:
            time.sleep(max(5.0, args.watch - elapsed + jitter))
        except KeyboardInterrupt:
            return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
