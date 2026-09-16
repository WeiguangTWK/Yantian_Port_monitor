"""按清单生成 Win7 源码交付包，并扫描疑似凭证。"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 要打包的路径（相对项目根）。目录会递归收集 .py（以及测试的 fixtures）。
INCLUDE_FILES = [
    "LICENSE",
    "README.md",
    "run_monitor.py",
    "watchlist.example.json",
    "requirements.txt",
    "requirements-gui.txt",
    "requirements-win7.txt",
    "requirements-win7-gui.txt",
]
INCLUDE_DIRS = ["ytmon", "tools", "gui", "docs", "licenses"]

# 绝不打包的东西
EXCLUDE_NAMES = {"__pycache__", ".recon", ".cache", "state", ".browser_profile",
                 "snapshots", "dist", ".git", ".venv", "venv"}
# 含真实凭证的文件 —— 即使被 include 命中也要剔除
DEVELOPMENT_DOCS = {"docs/GUI-技术选型与基座.md", "docs/GUI-Win7打包实测.md",
                    "docs/OpenViking-Codex.md"}

SECRET_FILES = {"watchlist.json", "cookies.json", "token.txt"}

# 打包后要扫描的敏感形态
# 注意：门槛都设得比较长，这样文档里的示例占位（access_token=...）不会误报，
# 只有真的粘了一串密钥进来才会命中。
SECRET_PATTERNS = [
    (r"loginVerifyCode=[A-Za-z0-9+/=]{40,}", "疑似真实 token"),
    (r"6\$5g%", "疑似密码"),
    (r"MTc4MjI3MA", "疑似账号 ID 的 base64"),
    (r"dingtalk\.com/robot/send\?access_token=[A-Za-z0-9]{20,}", "疑似钉钉机器人 token"),
    (r"qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]{30,}",
     "疑似企业微信机器人 key"),
    (r"open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]{30,}", "疑似飞书机器人 hook"),
    (r"smtp_password\"\s*:\s*\"[^\"]{6,}\"", "疑似明文邮箱密码"),
]

# 本文件里写着上面这些模式本身，扫描时会自我命中，故豁免。
# 其余所有文件一律照扫。
SCAN_EXEMPT = {"tools/make_bundle.py"}

_ALLOWED_SUFFIX = {".py", ".md", ".json", ".txt", ".svg", ".jpg"}


def iter_files(include_tests: bool) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []

    for rel in INCLUDE_FILES:
        p = ROOT / rel
        if p.is_file():
            out.append(p)

    dirs = list(INCLUDE_DIRS) + (["tests"] if include_tests else [])
    for d in dirs:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_NAMES for part in p.relative_to(ROOT).parts):
                continue
            if p.relative_to(ROOT).as_posix() in DEVELOPMENT_DOCS:
                continue
            if p.name in SECRET_FILES:
                continue
            if p.suffix.lower() not in _ALLOWED_SUFFIX:
                continue
            out.append(p)

    # 去重并保持稳定顺序
    seen: set[str] = set()
    uniq: list[pathlib.Path] = []
    for p in out:
        key = str(p.relative_to(ROOT)).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return sorted(uniq, key=lambda p: str(p.relative_to(ROOT)).lower())


def match_secrets(text: str) -> list[str]:
    """**纯模式匹配**：文本里有哪些像密钥的片段。

    不做文件后缀过滤，也不做假样本豁免 —— 这样测试可以直接验证
    "规则本身抓不抓得住"，而不会被豁免逻辑掩盖掉。
    """
    problems: list[str] = []
    for pattern, label in SECRET_PATTERNS:
        for m in re.finditer(pattern, text):
            problems.append(f"{label} → {m.group(0)[:40]}…")
    return problems


def scan_texts(items: list[tuple[str, bytes]]) -> list[str]:
    """返回发现的问题描述（空列表=干净）。

    在 `match_secrets` 之上加两层：只扫文本文件，以及放行标了 FAKE 的假样本。
    """
    problems: list[str] = []
    for name, raw in items:
        if name in SCAN_EXEMPT:
            continue
        if not name.endswith((".py", ".md", ".json", ".txt")):
            continue
        text = raw.decode("utf-8", "replace")
        for pattern, label in SECRET_PATTERNS:
            for m in re.finditer(pattern, text):
                # 假样本：测试里必须放一些"形状像真密钥"的值，
                # 否则测不出规则到底有没有用。约定是让它们含 FAKE 标记。
                # 豁免的是**这一处匹配**，不是整个文件 —— 文件其余部分照扫。
                if "FAKE" in m.group(0):
                    continue
                problems.append(f"{name}: {label} → {m.group(0)[:40]}…")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="打包给 Win7 的 zip")
    ap.add_argument("--out", default=None, help="输出 zip 路径")
    ap.add_argument("--no-tests", action="store_true", help="不打包测试")
    ap.add_argument("--check-only", action="store_true", help="只扫描凭证，不写 zip")
    args = ap.parse_args()

    files = iter_files(include_tests=not args.no_tests)

    payload: list[tuple[str, bytes]] = []
    for p in files:
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        payload.append((rel, p.read_bytes()))

    print(f"共 {len(payload)} 个文件")
    for rel, raw in payload:
        print(f"  {len(raw):>8}  {rel}")

    problems = scan_texts(payload)
    print()
    if problems:
        print("!! 扫描到疑似凭证，已中止：")
        for p in problems:
            print(f"   - {p}")
        return 1
    print("凭证扫描：通过（没有发现真实 token / 密码 / 账号 ID）")

    if args.check_only:
        return 0

    out = pathlib.Path(args.out) if args.out else (
        ROOT / "dist" / f"ytmon-{dt.date.today():%Y%m%d}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, raw in payload:
            z.writestr(rel, raw)

    print(f"\n已生成：{out}  ({out.stat().st_size/1024:.0f} KB)")
    print(f"""
拷到 Win7 机器上后：
    1. 解压到任意目录，例如  C:\\ytmon\\
    2. 确认目录结构是  C:\\ytmon\\ytmon\\__init__.py  （ytmon 包必须在根目录下）
    3. cd /d C:\\ytmon
    4. python tools\\env_check.py

注意：压缩包里**不含 watchlist.json**（里面有告警通道的密钥）。
    第一次运行前把 watchlist.example.json 复制成 watchlist.json 再填。
    告警通道怎么写，见 watchlist.example.json 里的 _notify说明。
""")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
