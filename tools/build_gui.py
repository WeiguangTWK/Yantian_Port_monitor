"""把 GUI 打包成可以直接拷到目标机器的 onedir 程序。

## 用法

    .toolchain\\py38\\python.exe tools\\build_gui.py              # 默认 windowed
    .toolchain\\py38\\python.exe tools\\build_gui.py --console    # 带控制台，便于看报错
    .toolchain\\py38\\python.exe tools\\build_gui.py --force      # 明知解释器不对也要编

产物：`dist/gui/ytmon-gui/`（整个文件夹拷走，不是单个 exe）。

## 为什么这个脚本要做那些检查

冻结是**最容易把错误静默固化下来**的一步 —— 一个参数写错，
产物在开发机上跑得好好的，到 Win7 上才炸。所以这里宁可吵一点：

* **解释器必须是 3.8**。PyInstaller 会把**构建时那个 Python 运行时**打进产物：
  用 3.14 编出来的 exe 里是 `python314.dll`，而它**不支持 Win7**。
  这种错在开发机上完全看不出来 —— 所以除非显式 `--force`，一律拒绝。
* **必须能 import `win32comext.shell`**。`qfluentwidgets` 的依赖
  `qframelesswindow` 用了它；冻结时若漏收，运行期报
  `No module named 'win32com'`，而 `gui/app.py` 会把它误报成"没装 qfluentwidgets"。
  在这里先挡住，比到现场查好。
* **`PYINSTALLER_CONFIG_DIR` 要指到工作区内**。默认它去建
  `%LOCALAPPDATA%\\pyinstaller`，在"只有工作区可写"的环境里会被拒 ——
  而且是在**最后一步 COLLECT** 才报错，前面 `Building EXE ... completed successfully`
  明明已经打完，极易误判成打包失败。
* **TMP/TEMP 不可写时要换掉**。只在探测到不可用时才换，并打印说明
  （与 `tools/run_tests.py` 同一套"先探测再决定"的做法）。

## 为什么用 onedir 而不是 onefile

Qt 运行时 100 MB+。onefile 每次启动都要把它解压到临时目录，
在 Win7 上更慢、更容易被杀软误报，临时目录行为在 Win7 上变量也更多。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 冻结时必须显式补的隐藏导入 —— 不补会报 No module named 'win32com'
# （原因：qfluentwidgets 的依赖 qframelesswindow 用了
#   `from win32comext.shell import shellcon`，静态分析抓不到）
HIDDEN_IMPORTS = ["win32con", "pythoncom", "pywintypes"]
COLLECT_SUBMODULES = ["win32comext"]
COLLECT_ALL = ["qfluentwidgets"]

# (import 名, 装它用的包名)
REQUIRED_IMPORTS = [
    ("PySide2", "PySide2"),
    ("qfluentwidgets", "PySide2-Fluent-Widgets"),
    ("win32comext.shell", "pywin32"),
]

ENTRY = ROOT / "gui" / "app.py"


def check_environment(version_info=None) -> "list[str]":
    """返回问题列表（空 = 可以编）。

    `version_info` 只为可测性而留：传 `(3, 14, 0)` 就能在不换解释器的前提下
    验证"版本不对必须被拦住"这条守卫。
    """
    vi = version_info if version_info is not None else sys.version_info
    problems = []

    if tuple(vi[:2]) != (3, 8):
        problems.append(
            "解释器是 Python %d.%d，不是 3.8。\n"
            "        PyInstaller 会把构建时的 Python 运行时打进产物，"
            "用它编出来的 exe 在 Win7 上跑不起来。\n"
            "        用：.toolchain\\py38\\python.exe tools\\build_gui.py"
            "（确实知道自己在做什么就加 --force）"
            % (vi[0], vi[1])
        )

    for module, package in REQUIRED_IMPORTS:
        try:
            __import__(module)
        except ImportError as e:
            problems.append("import %s 失败（%s）：%s\n        装：pip install %s"
                            % (module, package, e, package))

    if not ENTRY.is_file():
        problems.append("找不到入口：%s" % ENTRY)

    return problems


def build_env(verbose: bool = True) -> "dict[str, str]":
    """准备子进程环境：该换的换掉，并把换了什么说出来。"""
    env = dict(os.environ)

    config_dir = env.get("PYINSTALLER_CONFIG_DIR")
    if not config_dir:
        config_dir = str(ROOT / ".toolchain" / "pyi-config")
        env["PYINSTALLER_CONFIG_DIR"] = config_dir
        if verbose:
            print("[build_gui] PYINSTALLER_CONFIG_DIR -> %s" % config_dir)

    # 只在当前临时目录**写不进去**时才接管 TMP/TEMP（先探测再决定）
    probe_dir = tempfile.gettempdir()
    try:
        probe = os.path.join(probe_dir, ".ytmon-tmp-probe-%d" % os.getpid())
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.unlink(probe)
    except OSError as e:
        fallback = ROOT / ".toolchain" / "tmp"
        fallback.mkdir(parents=True, exist_ok=True)
        env["TMP"] = env["TEMP"] = str(fallback)
        if verbose:
            print("[build_gui] 临时目录不可写（%s），已改用 %s" % (e, fallback))

    return env


def build_command(name: str, windowed: bool, dist_root: pathlib.Path) -> "list[str]":
    mode = "--windowed" if windowed else "--console"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", mode,
        "--name", name,
        "--paths", str(ROOT),
        "--paths", str(ROOT / "gui"),
        "--add-data", str(ROOT / "gui" / "assets") + os.pathsep + "gui/assets",
        "--workpath", str(ROOT / ".toolchain" / "pyi-build"),
        "--distpath", str(dist_root),
        "--specpath", str(ROOT / ".toolchain"),
    ]
    for mod in COLLECT_ALL:
        cmd += ["--collect-all", mod]
    for mod in COLLECT_SUBMODULES:
        cmd += ["--collect-submodules", mod]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    cmd.append(str(ENTRY))
    return cmd


def dir_size_mb(path: pathlib.Path) -> float:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total / (1024.0 * 1024.0)


def copy_template_into(out_dir: pathlib.Path) -> str:
    """把配置模板放进产物目录，返回落点（失败返回空串）。

    冻结后**配置基准就是 exe 所在目录**（见 `ytmon/paths.py`），
    所以模板必须在它旁边，否则第一次运行只会看到"找不到配置文件"。

    只放模板 `watchlist.example.json`，**不生成** `watchlist.json` ——
    配置里会有告警通道密钥，构建脚本不该凭空造一份出来。
    """
    src = ROOT / "watchlist.example.json"
    if not src.is_file() or not out_dir.is_dir():
        return ""
    dst = out_dir / "watchlist.example.json"
    try:
        shutil.copy2(str(src), str(dst))
    except OSError:
        return ""
    return str(dst)


def main(argv: "list[str]") -> int:
    ap = argparse.ArgumentParser(description="打包 GUI 成 onedir 程序")
    ap.add_argument("--name", default="ytmon-gui", help="产物名（默认 ytmon-gui）")
    ap.add_argument("--console", action="store_true",
                    help="保留控制台（排查启动问题时用）")
    ap.add_argument("--force", action="store_true", help="跳过环境检查")
    ap.add_argument("--dist", default=None, help="产物根目录（默认 dist/gui）")
    args = ap.parse_args(argv)

    if not args.force:
        problems = check_environment()
        if problems:
            print("[build_gui] 环境检查未通过，已中止：\n", file=sys.stderr)
            for p in problems:
                print("  - %s" % p, file=sys.stderr)
            print("", file=sys.stderr)
            return 2

    dist_root = pathlib.Path(args.dist) if args.dist else (ROOT / "dist" / "gui")
    cmd = build_command(args.name, not args.console, dist_root)
    env = build_env()

    print("[build_gui] 开始打包（onedir / %s）…"
          % ("windowed" if not args.console else "console"))
    proc = subprocess.run(cmd, env=env)
    if proc.returncode != 0:
        print("[build_gui] 打包失败，退出码 %d" % proc.returncode, file=sys.stderr)
        print("[build_gui] 注意：如果日志停在 COLLECT 且报 WinError 5，"
              "多半是 PYINSTALLER_CONFIG_DIR / TMP 指到了工作区外，不是打包本身的问题。",
              file=sys.stderr)
        return proc.returncode

    out = dist_root / args.name
    print("")
    print("[build_gui] 完成：%s" % out)
    if out.is_dir():
        print("[build_gui] 大小：约 %.1f MB" % dir_size_mb(out))
    tpl = copy_template_into(out)
    if tpl:
        print("[build_gui] 已放入配置模板：%s" % pathlib.Path(tpl).name)
    print("[build_gui] 拷到目标机器时**整个文件夹一起拷**，"
          "然后把 watchlist.example.json 复制成 watchlist.json 填好目标。")
    print("[build_gui] 上 Win7 前请对照 docs/GUI-Win7打包实测.md 第 7 节与 "
          "docs/Win7-实机验证指南.md。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
