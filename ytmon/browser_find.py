"""浏览器定位（**不依赖 aiohttp**，纯标准库）。

单独成模块的原因：Win7 机器上第一次自检时往往还没装任何第三方依赖，
而"这台机器有没有可用的 Chromium 系浏览器"恰恰是最该先回答的问题。
所以这段逻辑必须能在裸 Python 上跑。
"""

from __future__ import annotations

import glob
import os
import pathlib
import shutil
import sys

# (安装根目录, 可执行文件名)。Chromium 系各家布局都是 <root>\<版本>\<exe>，
# 便携版则直接放 <root>\<exe>。
CANDIDATES: list[tuple[str, str]] = [
    # Edge（Win10/11 常见布局；Win7 最高只到 109）
    (r"C:\Program Files (x86)\Microsoft\EdgeCore", "msedge.exe"),
    (r"C:\Program Files\Microsoft\EdgeCore", "msedge.exe"),
    (r"C:\Program Files (x86)\Microsoft\Edge\Application", "msedge.exe"),
    (r"C:\Program Files\Microsoft\Edge\Application", "msedge.exe"),
    # Chrome
    (r"C:\Program Files\Google\Chrome\Application", "chrome.exe"),
    (r"C:\Program Files (x86)\Google\Chrome\Application", "chrome.exe"),
    # Supermium —— Win7 上的主力选择（内核跟进到 Chromium 138）
    (r"C:\Program Files\Supermium", "supermium.exe"),
    (r"C:\Program Files (x86)\Supermium", "supermium.exe"),
    (r"C:\Program Files\Supermium", "chrome.exe"),
    (r"C:\Program Files (x86)\Supermium", "chrome.exe"),
    (r"C:\Supermium", "supermium.exe"),
    (r"C:\Supermium\App", "supermium.exe"),
    (r"C:\Tools\Supermium", "supermium.exe"),
    (r"C:\Supermium", "chrome.exe"),
    (r"C:\Tools\Supermium", "chrome.exe"),
    # Chromium 原版
    (r"C:\Program Files\Chromium\Application", "chrome.exe"),
    (r"C:\Program Files (x86)\Chromium\Application", "chrome.exe"),
    (r"C:\Chromium\Application", "chrome.exe"),
]

# 用户级安装（%LOCALAPPDATA%\<子目录>\<版本>\<exe>）
LOCAL_SUBDIRS: list[tuple[str, str]] = [
    (r"Chromium\Application", "chrome.exe"),
    (r"Google\Chrome\Application", "chrome.exe"),
    (r"Microsoft\Edge\Application", "msedge.exe"),
    (r"Supermium", "supermium.exe"),
    (r"Supermium\Application", "supermium.exe"),
    (r"Supermium", "chrome.exe"),
    (r"Supermium\Application", "chrome.exe"),
]

# Linux 发行版通常通过 PATH 提供浏览器；优先使用已在 AOSC 验证的 Chromium。
LINUX_COMMANDS = (
    "chromium", "chromium-browser", "google-chrome-stable", "google-chrome",
    "microsoft-edge-stable", "microsoft-edge",
)

BROWSER_NOTE = (
    "Win7 上 Edge 最高只能到 109（已 EOL）。建议改用 Supermium：\n"
    "        https://github.com/win32ss/supermium/releases\n"
    "        下载后解压到任意目录，再把 watchlist.json 里的\n"
    "        settings.edge_path 指向 supermium.exe"
)

# probe_browser 的三态结果。用 unknown 而不是布尔，是因为
# "探测不出来"和"确定坏了"是两回事，混为一谈会误杀能用的浏览器。
PROBE_OK = "ok"
PROBE_FAIL = "fail"
PROBE_UNKNOWN = "unknown"


def version_key(path: str) -> tuple:
    """从 ...\\152.0.4191.66\\msedge.exe 取出版本元组用于比较。

    便携版没有版本目录，返回空元组（排序时靠后）。
    """
    part = pathlib.Path(path).parent.name
    chunks = part.split(".")
    if not chunks or not chunks[0].isdigit():
        return ()
    return tuple(int(c) if c.isdigit() else -1 for c in chunks)


def _expand(root: str, exe: str) -> list[str]:
    out: list[str] = []
    for pattern in (os.path.join(root, exe),
                    os.path.join(root, "*", exe),
                    os.path.join(root, "*", "*", exe)):
        out.extend(glob.glob(pattern))
    return out


def search_roots() -> list[str]:
    """按优先级返回找到的候选（已排重、只留真实文件）。"""
    if sys.platform.startswith('linux'):
        found = [path for command in LINUX_COMMANDS
                 if (path := shutil.which(command)) is not None]
        return list(dict.fromkeys(found))

    found: list[str] = []
    for root, exe in CANDIDATES:
        # 系统盘和安装目录可以不是 C:；64 位系统兼顾两个 Program Files。
        prefix32 = 'C:\\Program Files (x86)'
        prefix64 = 'C:\\Program Files'
        if root.startswith(prefix32):
            bases = [os.environ.get('ProgramFiles(x86)', prefix32)]
            suffix = root[len(prefix32):].lstrip('\\')
        elif root.startswith(prefix64):
            bases = [os.environ.get('ProgramW6432'), os.environ.get('ProgramFiles', prefix64)]
            suffix = root[len(prefix64):].lstrip('\\')
        else:
            bases, suffix = [root], ''
        for base in bases:
            if base:
                found.extend(_expand(os.path.join(base, suffix) if suffix else base, exe))

    local = os.environ.get("LOCALAPPDATA")
    if local:
        for sub, exe in LOCAL_SUBDIRS:
            found.extend(_expand(os.path.join(local, sub), exe))

    seen: set[str] = set()
    uniq: list[str] = []
    for p in found:
        rp = os.path.normcase(os.path.abspath(p))
        if rp in seen or not pathlib.Path(p).is_file():
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def is_windows7() -> bool:
    if sys.platform != 'win32':
        return False
    version = sys.getwindowsversion()
    return (version.major, version.minor) == (6, 1)


def browser_brand(path: str) -> str:
    """按候选安装位置识别名称；不宣称完成了浏览器身份或兼容性验证。"""
    parts = path.replace('\\', '/').lower().split('/')
    if 'supermium' in parts or parts[-1] == 'supermium.exe':
        return 'Supermium'
    if parts[-1] in ('msedge.exe', 'microsoft-edge', 'microsoft-edge-stable'):
        return 'Edge'
    return 'Chrome' if 'google' in parts or parts[-1].startswith('google-chrome') else 'Chromium'


def select_browser(found: list[str], windows7: bool) -> str:
    preferred = ('Supermium', 'Edge') if windows7 else ('Edge', 'Supermium')
    for brand in preferred + ('Chrome', 'Chromium'):
        candidates = [path for path in found if browser_brand(path) == brand]
        if candidates:
            return max(candidates, key=version_key)
    raise FileNotFoundError('没有浏览器候选文件。')


def find_browser(explicit: str | None = None) -> str:
    """返回可用的 Chromium 系浏览器可执行文件路径；找不到抛 FileNotFoundError。"""
    if explicit:
        p = pathlib.Path(explicit)
        if p.is_dir():
            raise FileNotFoundError(
                f"配置里给的是**目录**，不是 exe：{explicit}\n"
                f"        请指向具体的可执行文件，例如 {os.path.join(explicit, 'supermium.exe')}"
            )
        if not p.is_file():
            raise FileNotFoundError(f"配置里指定的浏览器不存在：{explicit}")
        return str(p)

    found = search_roots()
    if not found:
        if sys.platform.startswith('linux'):
            raise FileNotFoundError(
                '未在 PATH 中找到 Chromium 系浏览器。请安装 Chromium，'
                '或在监听设置中填写浏览器可执行文件的完整路径。'
            )
        raise FileNotFoundError(
            "未找到任何 Chromium 系浏览器（Edge / Chrome / Chromium / Supermium）。\n"
            f"        {BROWSER_NOTE}"
        )
    return found[0] if sys.platform.startswith('linux') else select_browser(found, is_windows7())


def probe_browser(path: str, timeout: float = 40.0) -> tuple[str, str]:
    """真的把这个 exe 启一次，确认它能跑起来。返回 (三态结果, 说明)。

    为什么非做不可：**路径存在 != 能启动**。

    最坑的一种情况是缺运行时库（VC++ 运行库 / `api-ms-win-crt-*.dll`）。
    这时 Windows 的 `CreateProcess` 返回的是 `ERROR_FILE_NOT_FOUND(2)`，
    Python 抛 `FileNotFoundError: [WinError 2] 系统找不到指定的文件。`——
    看起来像"文件不存在"，可文件明明就在那儿。这个错义极其误导，
    会让人反复去确认路径、复制文件，而真正缺的是 DLL。

    ## 为什么是这个命令（实测挑出来的，别随便改）

    本机上实测 `msedge.exe` 各参数组合：

        --version                       挂死（>20s 不返回）
        --headless --version            挂死
        --headless=new --version        挂死
        --dump-dom about:blank          挂死
        --headless --dump-dom about:blank   3.7s 返回 rc=0  ✓

    Edge 是启动器存根，对 `--version` 根本不保证立即返回。
    所以**不能用 --version 做冒烟测试**（这个坑真踩过：一加就把自检弄挂了）。

    `--user-data-dir` 指向临时目录是必须的 —— 否则会去动用户真实的
    Edge 配置，既可能因为配置被占用而失败，也会污染用户数据。

    ## 三态而不是布尔

    `unknown` 是必要的：有些分支/环境下探测命令就是会挂住，
    这时候"无法判定"才是诚实的答案。把它当失败会误杀能用的浏览器。
    """
    import shutil
    import subprocess
    import tempfile

    if pathlib.Path(path).is_dir():
        return PROBE_FAIL, f"这是个目录，不是可执行文件：{path}"
    if not pathlib.Path(path).is_file():
        return PROBE_FAIL, f"文件不存在：{path}"

    tmpdir = tempfile.mkdtemp(prefix="ytmon-probe-")
    args = [
        path,
        "--headless",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={tmpdir}",
        "--dump-dom",
        "about:blank",
    ]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        # 路径明明存在却报"找不到文件" —— 几乎总是缺依赖库
        return PROBE_FAIL, (
            f"路径存在，但系统拒绝启动它，报的是「找不到文件」(WinError 2)。\n"
            f"        {e}\n"
            f"        这种错义几乎总是**缺少运行时库**，不是路径问题。请确认：\n"
            f"          · 解压时是整个压缩包解压的，没有只挑 exe 出来\n"
            f"          · 装了 VC++ 运行库（Win7 上是 vc_redist.x64.exe）\n"
            f"          · 32/64 位与系统匹配"
        )
    except subprocess.TimeoutExpired:
        return PROBE_UNKNOWN, (
            f"能启动，但 {timeout:.0f} 秒内没有返回输出。\n"
            f"        有些 Chromium 分支对无头参数就是会挂住，所以这里**无法判定**。\n"
            f"        先当作可用继续；如果后面引导失败，再回来查这个浏览器"
        )
    except OSError as e:
        return PROBE_FAIL, f"启动失败：{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    if proc.returncode == 0 and "<html" in out.lower():
        return PROBE_OK, "无头启动正常（能渲染并输出 DOM）"

    first = next((ln.strip() for ln in (err or out).splitlines() if ln.strip()), "")
    return PROBE_FAIL, (f"能启动但运行失败（退出码 {proc.returncode}）"
                        + (f"：{first[:200]}" if first else ""))



def describe_candidates() -> str:
    """给自检用的可读清单。"""
    found = search_roots()
    if not found:
        return "（一个都没找到）"
    return "\n          ".join(found)
