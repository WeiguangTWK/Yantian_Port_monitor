"""运行期路径基准 —— 让"打包成 exe 之后"仍然找得到配置和状态。

## 为什么需要

源码运行时，`watchlist.json`、`state/`、`.cache/`、`.browser_profile/` 都是
**相对当前工作目录（CWD）** 解析的，在命令行里这很自然。

冻结成 exe 之后就不成立了：

* PyInstaller 会把东西解到自己的目录，`__file__` 指向那里（不是项目根）；
* 而双击 exe、走快捷方式、被计划任务拉起，**CWD 各不相同**
  （快捷方式可以设"起始位置"，计划任务常常是 `C:\\Windows\\System32`）。

结果会是"配置读不到"或"每次都当首次运行"这类**最难查**的故障。

## 做法

`anchor_to_app_dir()` **只在冻结时**把 CWD 钉到 exe 所在目录。
源码运行时它什么都不做 —— 所以这个模块在开发机上是**完全惰性**的。

另外提供 `is_writable_dir()`：它是**真去写一个文件**来判断的。
Windows 上权限位不可信（只读属性、ACL、沙箱都可能让 `os.access` 说错话），
"能不能写"只能试出来。放不下数据时早一点、明确一点地说出来，
比后面报一个看不懂的 `OSError` 好得多。
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Any


def is_frozen() -> bool:
    """是不是被 PyInstaller（或同类工具）冻结出来的。"""
    return bool(getattr(sys, "frozen", False))


def system_program_env() -> dict[str, str]:
    """外部系统程序不用冻结包自己的 Linux 动态库搜索路径。"""
    env = dict(os.environ)
    if is_frozen() and sys.platform.startswith("linux"):
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env


def app_base_dir() -> pathlib.Path:
    """配置与数据的基准目录。

    * 冻结 → **exe 所在目录**（便携语义：整个文件夹拷到哪就以哪为家）
    * 源码 → **项目根**（`ytmon` 的上一级）
    """
    if is_frozen():
        return pathlib.Path(sys.executable).resolve().parent
    return pathlib.Path(__file__).resolve().parent.parent


def is_writable_dir(path: Any) -> bool:
    """真写一个探针文件来判断该目录能不能写。目录不存在也算不能写。"""
    d = pathlib.Path(path)
    if not d.is_dir():
        return False

    probe = d / (".ytmon-write-probe-%d" % os.getpid())
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        return True
    except OSError:
        return False
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def anchor_to_app_dir() -> pathlib.Path:
    """冻结时把 CWD 钉到 exe 所在目录；源码运行时**原样返回、不做任何事**。

    返回基准目录，供调用方拼配置路径。
    """
    base = app_base_dir()
    if is_frozen():
        try:
            os.chdir(str(base))
        except OSError:
            # 切不过去就算了 —— 让 writable_warning() 去把问题说清楚，
            # 而不是在这里抛一个调用方没法处理的异常。
            pass
    return base


def writable_warning(base: Any = None) -> str:
    """基准目录不可写时返回一段给人看的说明；正常返回空串。

    为什么值得单独提示：exe 放在 `Program Files` 这类地方时，
    读配置没问题、跑起来也没问题，**要到写状态/缓存时才炸**，
    而那时代码已经深入业务逻辑，报出来的错跟"目录只读"八竿子打不着。
    """
    if not is_frozen():
        return ""

    d = pathlib.Path(base) if base is not None else app_base_dir()
    if is_writable_dir(d):
        return ""
    return (
        "[警告] 程序所在目录不可写：%s\n"
        "       读取配置没问题，但 state/ 、.cache/ 、.browser_profile/ 写不进去，\n"
        "       监控会失败或每轮都当成首次运行。\n"
        "       请把整个文件夹拷到可写位置（例如 D:\\ytmon）再运行，不要放在\n"
        "       C:\\Program Files 这类需要管理员权限的目录下。" % d
    )
