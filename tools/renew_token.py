"""手动触发 token 续期。

用账号密码登录后，从「公共信息服务」页面抠出服务端新签发的 loginVerifyCode。

凭证来源（按优先级）：
    1. 环境变量 YT_USER / YT_PASS
    2. Windows 凭据管理器（可用 --save 先从环境变量存进去）

用法：
    set YT_USER=xxx & set YT_PASS=yyy
    python tools/renew_token.py               # 只打印新 token
    python tools/renew_token.py --write       # 并写回 watchlist.json
    python tools/renew_token.py --save        # 把凭证存进 Windows 凭据管理器
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ytmon.auth import (Authenticator, delete_credentials, load_credentials,  # noqa: E402
                        save_credentials)
from ytmon.config import AppConfig, resolve_token                         # noqa: E402
from ytmon.http_client import bootstrap_cookies, load_cached_cookies      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="续期 loginVerifyCode")
    ap.add_argument("--config", default="watchlist.json")
    ap.add_argument("--write", action="store_true", help="把新 token 写回配置文件")
    ap.add_argument("--save", action="store_true", help="把环境变量里的凭证存进凭据管理器")
    ap.add_argument("--delete", action="store_true",
                    help="删除凭据管理器里保存的凭证（推荐：主路径其实不需要账号）")
    ap.add_argument("--show-browser", action="store_true")
    args = ap.parse_args()

    cfg = AppConfig.load(args.config)
    s = cfg.settings

    if args.delete:
        ok = delete_credentials()
        print("[OK] 凭证已从 Windows 凭据管理器删除" if ok
              else "[提示] 没有找到可删除的凭证（可能本来就没存过）")
        return 0

    if args.save:
        u, p = os.environ.get("YT_USER"), os.environ.get("YT_PASS")
        if not (u and p):
            print("[错误] --save 需要先设置 YT_USER / YT_PASS 环境变量", file=sys.stderr)
            return 1
        ok = save_credentials(u, p)
        print("[OK] 凭证已存入 Windows 凭据管理器" if ok
              else "[失败] 凭据管理器写入失败，请改用环境变量")
        return 0 if ok else 1

    creds = load_credentials()
    if not creds:
        print("[错误] 没有可用凭证。请设置 YT_USER / YT_PASS，"
              "或先跑 --save 存进凭据管理器。", file=sys.stderr)
        return 1
    username, password = creds
    print(f"使用账号：{username[:2]}***（密码已载入，长度 {len(password)}）")

    # 登录请求同样要过 EdgeOne，所以需要有效 cookie
    try:
        token = resolve_token(cfg, None)
    except Exception:                       # noqa: BLE001
        token = "bootstrap"                 # 纯粹为了引导 cookie
    cookies = load_cached_cookies(s.cookie_cache)
    if cookies is None:
        print("cookie 缺失/过期，正在用浏览器引导…")
        cookies = bootstrap_cookies(token, profile_dir=s.profile_dir,
                                    edge_path=s.edge_path, headless=s.headless,
                                    cache_file=s.cookie_cache)
    print(f"cookie：{', '.join(cookies)}")

    auth = Authenticator.from_cookies(cookies)
    result = auth.renew(username, password)

    print()
    print("登录结果：", result.login.describe() if result.login else "(未执行)")
    if result.login and result.login.raw_snippet:
        print("  原始响应片段：", result.login.raw_snippet[:200])
    print("续期结果：", result.detail)

    if not result.ok or not result.token:
        print("\n[失败] 未能取得新 token。", file=sys.stderr)
        print("       若提示'登录被拒'，请核对账号密码；", file=sys.stderr)
        print("       若提示'页面仍是未登录态'，可能需要验证码或存在异地登录限制。",
              file=sys.stderr)
        return 2

    new_token = result.token
    print(f"\n新 token：\n{new_token}")

    if args.write:
        cfg.token = new_token
        path = cfg.save()
        print(f"\n[OK] 已写回 {path}")
    else:
        print("\n提示：加 --write 可写回配置文件；或把它设为环境变量 YT_TOKEN。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
