"""打出"可以带去 Win7 现场"的完整包。

## 产出

    dist/release/ytmon-win7-<日期>/
      现场必读.txt                  ← 唯一入口文档
      第1步-环境自检.cmd
      第2步-跑一轮监控.cmd
      第3步-测告警通道.cmd
      ytmon/                        ← 三个 exe **共用一个文件夹**
        ytmon-gui.exe   + _internal-gui/
        ytmon.exe       + _internal-cli/
        ytmon-check.exe + _internal-check/
        watchlist.example.json
      浏览器/                       ← Supermium 安装包 + 说明
      备胎-源码与Python/            ← Python 安装包 + 源码 zip + 预下载的 wheels
      文档/                         ← 现场要用到的几份文档
    dist/release/ytmon-win7-<日期>.zip

## 三个 exe 为什么必须放在同一个文件夹

冻结后**配置与数据的基准就是 exe 所在目录**（见 `ytmon/paths.py`）。
如果它们各自待在自己的目录里，就会各自有一份 `watchlist.json`、
`state/`、`.cache/`、`.browser_profile/` —— 后果是：

* GUI 看不到服务端查到的状态（`state/voyage_state.json` 是两者之间的接口）；
* 每一边都要各自过一次 EdgeOne 挑战、各自拉起一次浏览器。

所以装配时把三个 exe 和它们各自的 `_internal-*` 目录**并到同一个文件夹**。
PyInstaller 的 `--contents-directory` 让每个 exe 有自己独立的运行时目录，
互不冲突（已验证）。

## 几个不显眼但会让现场包"看着能用其实不行"的处理

1. **`.cmd` 必须是 CRLF** —— cmd 对 LF-only 的 goto/label 处理不可靠。
2. **`.cmd` 内容只用 ASCII** —— 中文在 .cmd 里的显示取决于控制台代码页，
   不保险。所有中文解释都放在 `.txt` 里。
3. **`.txt` 写成 UTF-8 带 BOM** —— Win7 记事本对无 BOM 的 UTF-8 会猜错编码，
   中文变乱码。带 BOM 它就认。
4. **发布资产也要过凭证扫描** —— 复用 `make_bundle.scan_texts`，
   免得新加的文本文件把密钥带上路（`dead-ends.md` D1 的教训）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_gui                                              # noqa: E402
import make_bundle                                            # noqa: E402

PACKAGING = ROOT / "packaging"

# 每个目标：entry=入口脚本, name=产物名, windowed=是否无控制台,
# contents=它自己的运行时目录名（必须互不相同）
TARGETS = [
    dict(name="ytmon-gui", entry="gui/app.py", windowed=True,
         contents="_internal-gui",
         collect_all=[], collect_submodules=[], hidden=[]),
    dict(name="ytmon", entry="run_monitor.py", windowed=False,
         contents="_internal-cli",
         collect_all=[], collect_submodules=[], hidden=[]),
    dict(name="ytmon-check", entry="tools/env_check.py", windowed=False,
         contents="_internal-check",
         collect_all=[], collect_submodules=[], hidden=[]),
]

# 放进包里的文本资产：(源文件, 包内相对路径, 是否加 BOM)
TEXT_ASSETS = [
    ("现场必读.txt", "现场必读.txt", True),
    ("第1步-环境自检.cmd", "第1步-环境自检.cmd", False),
    ("第2步-跑一轮监控.cmd", "第2步-跑一轮监控.cmd", False),
    ("第3步-测告警通道.cmd", "第3步-测告警通道.cmd", False),
    ("浏览器-说明.txt", "浏览器/浏览器-说明.txt", True),
    ("备胎-安装说明.txt", "备胎-源码与Python/备胎-安装说明.txt", True),
]

DOCS = [
    "README.md",
    "docs/Win7-实机验证指南.md",
]

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def log(msg: str) -> None:
    print("[release] %s" % msg)


# ------------------------------------------------------------------ 文本资产

def write_windows_text(src: pathlib.Path, dst: pathlib.Path, bom: bool) -> None:
    """按 Windows 目标机的习惯写文本：CRLF 行尾；.txt 带 UTF-8 BOM。

    两件事都不是可有可无：CRLF 是 cmd 解析 `.cmd` 的可靠性要求；
    BOM 是让 Win7 记事本认出 UTF-8 —— 否则中文直接变乱码。
    """
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


def copy_text_assets(bundle: pathlib.Path) -> "list[tuple[str, bytes]]":
    """铺文本资产，返回 (包内路径, 字节) 供凭证扫描。"""
    payload = []
    for src_name, rel, bom in TEXT_ASSETS:
        src = PACKAGING / src_name
        if not src.is_file():
            raise FileNotFoundError("缺少打包资产：%s" % src)
        dst = bundle / rel
        write_windows_text(src, dst, bom)
        payload.append((rel.replace("\\", "/"), dst.read_bytes()))
    return payload


def copy_docs(bundle: pathlib.Path) -> None:
    out = bundle / "文档"
    out.mkdir(parents=True, exist_ok=True)
    for rel in DOCS:
        src = ROOT / rel
        if src.is_file():
            shutil.copy2(str(src), str(out / src.name))


# ------------------------------------------------------------------ 构建

def pyinstaller_argv(target: dict, staging: pathlib.Path, spec_dir: pathlib.Path) -> "list[str]":
    if target["entry"] == "gui/app.py":
        return build_gui.build_command(
            target["name"], target["windowed"], staging / "dist",
            contents_directory=target["contents"],
            workpath=staging / ("work-" + target["name"]), specpath=spec_dir)
    argv = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir",
        "--windowed" if target["windowed"] else "--console",
        "--name", target["name"],
        "--contents-directory", target["contents"],
        "--paths", str(ROOT),
        "--paths", str(ROOT / "gui"),
        "--workpath", str(staging / ("work-" + target["name"])),
        "--distpath", str(staging / "dist"),
        "--specpath", str(spec_dir),
    ]
    for mod in target["collect_all"]:
        argv += ["--collect-all", mod]
    for mod in target["collect_submodules"]:
        argv += ["--collect-submodules", mod]
    for mod in target["hidden"]:
        argv += ["--hidden-import", mod]
    argv.append(str(ROOT / target["entry"]))
    return argv


def build_targets(staging: pathlib.Path) -> "dict[str, pathlib.Path]":
    """构建三个目标，返回 {name: 产物目录}。"""
    build_gui.prepare_version_file()
    spec_dir = staging / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    env = build_gui.build_env()
    built = {}
    for target in TARGETS:
        log("打包 %s（%s）…" % (target["name"], "windowed" if target["windowed"] else "console"))
        proc = subprocess.run(pyinstaller_argv(target, staging, spec_dir), env=env)
        if proc.returncode != 0:
            raise RuntimeError("打包 %s 失败，退出码 %d" % (target["name"], proc.returncode))
        out = staging / "dist" / target["name"]
        if not out.is_dir():
            raise RuntimeError("产物目录不存在：%s" % out)
        if target["entry"] == "gui/app.py":
            build_gui.verify_gui_output(out, target["contents"])
        built[target["name"]] = out
        log("  -> %s（%.1f MB）" % (out, build_gui.dir_size_mb(out)))
    return built


def assemble_runtime(bundle: pathlib.Path, built: "dict[str, pathlib.Path]") -> pathlib.Path:
    """把三个产物并进同一个 `ytmon/` 文件夹。

    每个 exe 连同它自己的 `_internal-*` 一起搬 —— 那才是它与众不同的运行时。
    """
    dest = bundle / "ytmon"
    dest.mkdir(parents=True, exist_ok=True)
    for target in TARGETS:
        src = built[target["name"]]
        for item in src.iterdir():
            shutil.move(str(item), str(dest / item.name))
    tpl = ROOT / "watchlist.example.json"
    if tpl.is_file():
        shutil.copy2(str(tpl), str(dest / tpl.name))
    return dest


# ------------------------------------------------------------------ 备胎

def build_source_zip() -> pathlib.Path:
    """调 make_bundle.py 出源码包（它会自己扫凭证）。"""
    log("打源码包 make_bundle.py …")
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "make_bundle.py")])
    if proc.returncode != 0:
        raise RuntimeError("make_bundle.py 失败，退出码 %d" % proc.returncode)
    zips = sorted((ROOT / "dist").glob("ytmon-*.zip"))
    if not zips:
        raise RuntimeError("没找到 make_bundle 的产物")
    return zips[-1]


def download_wheels(dest: pathlib.Path, mirror: str,
                    use_system_proxy: bool = False) -> int:
    """预下载依赖 wheel，供 Win7 离线安装。

    为什么值得做：Win7 的 TLS 栈与根证书库太旧，pip 直连 PyPI 经常握不上手，
    报出来还是 `SSLError` 这类和"装不上包"看起来无关的错。
    """
    dest.mkdir(parents=True, exist_ok=True)
    log("预下载依赖 wheel（离线安装用）…")

    env = dict(os.environ)
    if not use_system_proxy:
        # ⚠️ Windows 上 pip 会从**注册表**读 WinINET 代理设置
        # （urllib.request.getproxies() 读 HKCU\...\Internet Settings），
        # 而那个代理可能早就没在跑了 —— 报出来是
        #   ProxyError('Cannot connect to proxy.', FileNotFoundError(2, ...))
        # 看着像"网络不通"，其实是去连一个不存在的本地端口。
        # 实测：`pip --proxy ""` **无效**（仍然走注册表），只有 no_proxy 管用。
        env["no_proxy"] = "*"
        env["NO_PROXY"] = "*"

    argv = [
        sys.executable, "-m", "pip", "download",
        "-r", str(ROOT / "requirements-win7.txt"),
        "-r", str(ROOT / "requirements-win7-gui.txt"),
        "-d", str(dest),
        "--only-binary=:all:",
        "--python-version", "38", "--platform", "win_amd64",
        "--implementation", "cp",
        "--disable-pip-version-check",
        "-i", mirror, "--timeout", "60", "--retries", "3",
    ]
    proc = subprocess.run(argv, env=env)
    if proc.returncode != 0:
        log("⚠️ wheel 预下载失败（不致命，但备胎路线在离线机器上就装不了依赖）")
        log("   若报 ProxyError：多半是注册表里的 WinINET 代理已失效，"
            "本脚本默认已用 no_proxy 绕开；仍失败就加 --use-system-proxy 反过来试。")
        return 0
    wheels = list(dest.glob("*.whl"))
    log("  -> %d 个 wheel，共 %.1f MB"
        % (len(wheels), sum(w.stat().st_size for w in wheels) / 1048576.0))
    return len(wheels)


def copy_payload_file(src: pathlib.Path, dst: pathlib.Path) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    return True


# ------------------------------------------------------------------ 装配

def scan_assets(payload: "list[tuple[str, bytes]]") -> "list[str]":
    """发布资产也要过一遍凭证扫描。

    `make_bundle` 只扫它自己的固定清单；新加的文本文件不在里面，
    D1 那条"自造凭证泄露"的教训要求这里补一次。

    `.cmd` 要特殊处理：`make_bundle.scan_texts` 只扫 `.py/.md/.json/.txt`，
    会把 `.cmd` 整份跳过 —— 而 `.cmd` 里一样可以藏一个 token。
    这里给它接个 `.txt` 后缀喂进去，只为过掉那层后缀过滤；
    报出来的名字会带上 `.cmd.txt`，是刻意的（一眼能看出是命名技巧而非真文件）。
    """
    to_scan = []
    for name, raw in payload:
        if name.lower().endswith(".cmd"):
            to_scan.append((name + ".txt", raw))
        else:
            to_scan.append((name, raw))
    return make_bundle.scan_texts(to_scan)


def make_zip(bundle: pathlib.Path, out_zip: pathlib.Path) -> None:
    log("压缩 %s …" % out_zip.name)
    with zipfile.ZipFile(str(out_zip), "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(bundle.rglob("*")):
            if p.is_file():
                z.write(str(p), str(pathlib.Path(bundle.name) / p.relative_to(bundle)))


def main(argv: "list[str]") -> int:
    ap = argparse.ArgumentParser(description="打 Win7 现场包")
    ap.add_argument("--supermium", default=None,
                    help="Supermium 安装包/exe 的路径（默认去 Downloads 找）")
    ap.add_argument("--python-installer", default=None,
                    help="python-3.8.10-amd64.exe 的路径（默认去 .toolchain / Downloads 找）")
    ap.add_argument("--skip-wheels", action="store_true", help="跳过 wheel 预下载")
    ap.add_argument("--use-system-proxy", action="store_true",
                    help="wheel 下载时**沿用**系统代理。默认不用"
                         "（Windows 上 pip 会读注册表里可能已失效的 WinINET 代理）")
    ap.add_argument("--no-zip", action="store_true", help="只出文件夹，不压缩")
    ap.add_argument("--mirror", default=MIRROR)
    args = ap.parse_args(argv)

    problems = build_gui.check_environment()
    if problems:
        print("[release] 环境检查未通过，已中止：\n", file=sys.stderr)
        for p in problems:
            print("  - %s" % p, file=sys.stderr)
        return 2

    # 先找齐外部素材，缺了就在动手前说清楚
    supermium = pathlib.Path(args.supermium) if args.supermium else None
    if supermium is None:
        for cand in sorted(pathlib.Path(os.path.expanduser("~")).glob("Downloads/supermium*")):
            if cand.is_file():
                supermium = cand
                break
    py_installer = pathlib.Path(args.python_installer) if args.python_installer else None
    if py_installer is None:
        for cand in (ROOT / ".toolchain" / "python-3.8.10-amd64.exe",
                     pathlib.Path(os.path.expanduser("~")) / "Downloads" / "python-3.8.10-amd64.exe"):
            if cand.is_file():
                py_installer = cand
                break

    if supermium is None or not supermium.is_file():
        log("⚠️ 没找到 Supermium 安装包；包里会缺浏览器（用 --supermium 指定）")
    if py_installer is None or not py_installer.is_file():
        log("⚠️ 没找到 python-3.8.10 安装包；备胎路线会缺少解释器")

    stamp = "%s" % dt.date.today().strftime("%Y%m%d")
    release_root = ROOT / "dist" / "release"
    bundle = release_root / ("ytmon-win7-%s" % stamp)
    staging = ROOT / ".toolchain" / "release-staging"
    for d in (bundle, staging):
        if d.exists():
            shutil.rmtree(str(d), ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    # 1) 文本资产 + 文档
    payload = copy_text_assets(bundle)
    copy_docs(bundle)

    # 2) 三个 exe
    built = build_targets(staging)
    runtime = assemble_runtime(bundle, built)
    log("运行时目录：%s（%.1f MB）" % (runtime, build_gui.dir_size_mb(runtime)))

    # 3) 浏览器
    if supermium is not None and supermium.is_file():
        copy_payload_file(supermium, bundle / "浏览器" / supermium.name)

    # 4) 备胎：Python + 源码 zip + wheels
    fallback = bundle / "备胎-源码与Python"
    if py_installer is not None and py_installer.is_file():
        copy_payload_file(py_installer, fallback / py_installer.name)
    src_zip = build_source_zip()
    copy_payload_file(src_zip, fallback / src_zip.name)
    if not args.skip_wheels:
        download_wheels(fallback / "wheels", args.mirror,
                        use_system_proxy=args.use_system_proxy)

    # 5) 凭证扫描（发布资产这一层）
    payload.append(("watchlist.example.json",
                    (ROOT / "watchlist.example.json").read_bytes()))
    found = scan_assets(payload)
    if found:
        print("[release] !! 发布资产里扫到疑似凭证，已中止：", file=sys.stderr)
        for f in found:
            print("   - %s" % f, file=sys.stderr)
        return 1
    log("发布资产凭证扫描：通过")

    # 6) 汇总
    total = build_gui.dir_size_mb(bundle)
    print("")
    print("=" * 74)
    print("  现场包已生成：%s" % bundle)
    print("  总大小：约 %.1f MB" % total)
    print("=" * 74)
    for item in sorted(bundle.iterdir()):
        if item.is_dir():
            print("  [目录] %-24s %6.1f MB" % (item.name, build_gui.dir_size_mb(item)))
        else:
            print("  [文件] %s" % item.name)
    print("")
    print("  三个 exe 同处 ytmon\\ 目录，共用 watchlist.json 与 state\\。")
    print("  现场按「现场必读.txt」走；要带回来的是 check-log.txt / run-log.txt /")
    print("  alert-log.txt，以及 GUI 起不来时的 ytmon\\ytmon-gui.log。")

    if not args.no_zip:
        out_zip = release_root / (bundle.name + ".zip")
        make_zip(bundle, out_zip)
        print("  压缩包：%s（%.1f MB）"
              % (out_zip, out_zip.stat().st_size / 1048576.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
