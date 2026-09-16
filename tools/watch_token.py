"""会话/ Cookie 可用性观测器。

⚠️ **重要修正（2026-09-14）**：本工具原本声称在测 `loginVerifyCode` 的有效期，
   但实测证明它**做不到** —— `check_token` 只发 GET，而伪造的 token 也能拿到
   正常的查询页，所以它判不出 token 是否有效。

   它测量的东西现在被如实重新定义：

        **缓存 cookie 的可用寿命** —— EO-Bot-Js-Token / 会话能撑多久

   这仍然是运维上有用的信息（它决定多久需要重新引导一次浏览器），
   但它**不是** token 的 TTL。
   要真正判别 token 需要走 `verify_token()`（发 POST 查询），
   而那条路径的判别力本身还有待一次干净实验确认。

   补充：如果主要靠"查询失败即自愈"的机制运行，其实**不需要知道 TTL**。

仍然有效的正确性设计：
  * cookie 比 token 短命。被 WAF 拦住 ≠ token 过期，
    所以遇到 waf 会先重新引导 cookie 再复检。
  * 网络错误绝不记为失效 —— 换网时实测验证过：一次 network 错误后自动恢复。
  * 单次异常不算数，需要连续 N 次确认，并再等一轮复查。

用法：
    python tools/watch_token.py                    # 每 60 秒体检一次
    python tools/watch_token.py --interval 30
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.config import AppConfig, resolve_token       # noqa: E402
from ytmon.errors import TokenError                     # noqa: E402
from ytmon.http_client import HttpClient, TokenStatus   # noqa: E402

CONFIRM_ROUNDS = 2          # 连续几次失效才判定
HEARTBEAT_EVERY = 10        # 每 N 次成功体检打一行心跳


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def probe(client: HttpClient, verbose: bool = True) -> tuple[TokenStatus, bool]:
    """体检一次。返回 (状态, 是否发生过重新引导)。"""
    st = client.check_token()
    if st.reason != "waf":
        return st, False

    if verbose:
        print(f"  [{now()}] 被 EdgeOne 拦截，重新引导 cookie 再复检…", flush=True)
    try:
        client.rebootstrap()
    except Exception as e:                                  # noqa: BLE001
        return TokenStatus(False, "network", f"重新引导失败：{type(e).__name__}: {e}"), True
    return client.check_token(), True


def main() -> int:
    ap = argparse.ArgumentParser(description="观测 loginVerifyCode 的有效期")
    ap.add_argument("--config", default="watchlist.json")
    ap.add_argument("--token", default=None)
    ap.add_argument("--interval", type=int, default=60, help="体检间隔秒，默认 60")
    ap.add_argument("--log", default="state/token_watch.jsonl")
    ap.add_argument("--summary", default="state/token_watch_summary.json")
    args = ap.parse_args()

    cfg = AppConfig.load(args.config)
    token = resolve_token(cfg, args.token)
    s = cfg.settings

    client = HttpClient.create(
        token,
        profile_dir=s.profile_dir,
        edge_path=s.edge_path,
        headless=s.headless,
        cache_file=s.cookie_cache,
    )

    log_path = pathlib.Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")

    print(f"=== 会话/Cookie 可用性观测 开始 {now()} ===")
    print(f"    间隔 {args.interval}s | 日志 {log_path}")
    print(f"    使用 token 尾部 ...{token[-16:]} (len={len(token)})")
    print(f"    首次体检…", flush=True)

    first = client.check_token()
    if not first.ok:
        print(f"    [!] 起始状态就不可用：{first.describe()}")
        print(f"        无法测量寿命（请换一个新鲜 token 再观测）")
        return 2
    valid_since = dt.datetime.now()
    print(f"    [OK] {first.describe()}")
    print(f"    开始计时（注意：这测的是 cookie/会话寿命，不是 token TTL。")
    print(f"    token 有效性无法用 GET 判别，详见文件头说明）\n")

    ok_streak = 0
    consecutive_bad = 0
    last_ok = valid_since
    rounds = 0

    while True:
        time.sleep(args.interval)
        rounds += 1
        st, rebootstrap = probe(client)

        record = {
            "ts": now(), "ok": st.ok, "reason": st.reason,
            "status": st.status_code, "ms": st.elapsed_ms,
            "detail": st.detail, "rebootstrap": rebootstrap,
            "valid_seconds": (dt.datetime.now() - valid_since).total_seconds(),
        }
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.flush()

        if st.ok:
            ok_streak += 1
            consecutive_bad = 0
            last_ok = dt.datetime.now()
            if ok_streak % HEARTBEAT_EVERY == 0:
                age = (last_ok - valid_since).total_seconds() / 60
                print(f"  [{now()}] 仍有效（已观测 {age:.0f} 分钟，第 {rounds} 次体检）")
            continue

        if st.reason == "network":
            print(f"  [{now()}] 网络异常，不计入判定：{st.detail}")
            continue

        consecutive_bad += 1
        print(f"  [{now()}] 失效迹象 {consecutive_bad}/{CONFIRM_ROUNDS}：{st.describe()}")

        if consecutive_bad < CONFIRM_ROUNDS:
            time.sleep(min(20, args.interval))
            continue

        # 确认失效：再复查一次，排除瞬时故障
        time.sleep(min(30, args.interval))
        final, _ = probe(client)
        if final.ok:
            print(f"  [{now()}] 复查又恢复正常，判定为抖动，继续观测")
            consecutive_bad = 0
            continue

        alive = (last_ok - valid_since).total_seconds()
        print()
        print("=" * 66)
        print(f"  ★ 会话确认失效  {now()}")
        print(f"    最后有效时间 : {last_ok:%Y-%m-%d %H:%M:%S}")
        print(f"    已观测可用时长: {alive/60:.1f} 分钟（{alive:.0f} 秒）  ← 下界")
        print(f"    失效原因     : {final.reason} — {final.detail}")
        print(f"    体检次数     : {rounds}")
        print("=" * 66)

        summary = {
            "token_tail": token[-16:],
            "observed_from": valid_since.isoformat(timespec="seconds"),
            "last_valid_at": last_ok.isoformat(timespec="seconds"),
            "confirmed_expired_at": now(),
            "observed_available_seconds": alive,
            "observed_available_minutes": round(alive / 60, 1),
            "note": "这测的是缓存 cookie/会话的可用时长，不是 loginVerifyCode 的 TTL。",
            "expire_reason": final.reason,
            "expire_detail": final.detail,
            "checks": rounds,
        }
        pathlib.Path(args.summary).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
        log.close()
        print(f"\n  结论已写入 {args.summary}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[中断] 观测停止")
        raise SystemExit(130)
    except TokenError as e:
        print(f"[致命] {e}", file=sys.stderr)
        raise SystemExit(2)
