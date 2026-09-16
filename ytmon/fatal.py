"""启动期致命错误的出口 —— 让"双击后什么都没发生"不再发生。

## 为什么需要

GUI 产物是 **windowed**（无控制台）。这意味着 `sys.stdout` / `sys.stderr` 没有去处：
一句 `print(..., file=sys.stderr)` 会**彻底消失**，未捕获的异常也不会显示出来。

对现场人员来说，症状就一个字：**"打不开"** —— 双击、闪一下、没了，没有任何线索。
最典型的触发条件是 **exe 同目录找不到 `watchlist.json`**
（冻结后配置基准就是 exe 目录，见 `ytmon/paths.py`）。

> 这个坑是实测撞出来的：剪掉产物目录里的配置后，exe 立刻以退出码 1 静默退出。
> 在开发机上看起来像"打包坏了"，其实只是**没有错误出口**。

## 两条都要可靠的路

1. **落盘** —— 往 exe 同目录的 `ytmon-gui.log` 追加一条带时间戳的记录。
   这是**唯一能事后取证**的东西（让现场把日志发回来）。
   以 **utf-8** 写：日志里一定有中文，按 GBK 写会崩在编码上（`dead-ends.md` B7）。
2. **弹窗** —— `MessageBoxW`，纯 ctypes 零依赖，Win7 也有。
   注意它在 **user32.dll**，不是 kernel32 也不是 shell32（`dead-ends.md` B9 记过这个归属坑）。

## 为什么弹窗必须显式开启

`report_fatal(..., dialog=True)` 才弹。默认 **False**，两个原因：

* `MessageBoxW` 会**阻塞到用户点确定**；
* 测试里绝不能让一个模态框把测试挂住，也不该在自动化运行时弹到别人屏幕上。

所以测试是**替换掉 `show_dialog` 再断言调用与否**，而不是真去弹。
"""

from __future__ import annotations

import ctypes
import datetime as dt
import os
import sys
import traceback
from typing import Any, Optional

LOG_NAME = "ytmon-gui.log"

# MessageBoxW 的样式位
MB_OK = 0x00000000
MB_ICONERROR = 0x00000010
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_base() -> str:
    """日志与配置的基准目录：冻结时是 exe 所在目录，否则是项目根。"""
    if getattr(sys, "frozen", False):
        try:
            return os.path.dirname(os.path.abspath(sys.executable))
        except Exception:                                       # noqa: BLE001
            pass
    return _project_root()


def log_path(base: Optional[Any] = None) -> str:
    return os.path.join(str(base) if base is not None else default_base(), LOG_NAME)


def append_log(text: str, base: Optional[Any] = None) -> str:
    """追加一条记录，返回日志文件路径。

    **绝不抛异常** —— 它本身就是"出错时"才被调用的，再抛就把原始问题盖掉了。
    （这条纪律来自 `dead-ends.md` D2：凡是"绝不能影响主流程"的承诺，
      都要有一层显式的 try/except，不能指望被调用方。）
    """
    path = log_path(base)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = "\n".join("        " + line for line in str(text).splitlines())
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("[%s]\n%s\n" % (stamp, body))
    except Exception:                                           # noqa: BLE001
        pass
    return path


def stderr_is_lost() -> bool:
    """输出有没有去处。

    windowed 产物里 PyInstaller 不接控制台，`sys.stderr` 是 `None` ——
    这时"打印一条错误"等于没打印，必须改弹窗。
    """
    return getattr(sys, "stderr", None) is None


def show_dialog(title: str, text: str) -> bool:
    """弹一个原生错误框，返回是否成功。**绝不抛异常。**

    ⚠️ 它会**阻塞到用户点确定** —— 只用于"不弹就没法解释"的启动期失败。
    ⚠️ `MessageBoxW` 在 **user32.dll**。
    """
    if os.name != "nt":
        return False
    try:
        u = ctypes.windll.user32                               # type: ignore[attr-defined]
        u.MessageBoxW.restype = ctypes.c_int
        u.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                  ctypes.c_wchar_p, ctypes.c_uint]
        u.MessageBoxW(None, text, title,
                      MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST)
        return True
    except Exception:                                           # noqa: BLE001
        return False


def format_exception(exc: BaseException) -> str:
    """异常 → 给人看的文本（类型 + 消息 + 调用栈）。"""
    return "%s: %s\n\n%s" % (type(exc).__name__, exc, traceback.format_exc())


def report_fatal(title: str, detail: str, dialog: bool = False,
                 base: Optional[Any] = None) -> str:
    """把启动期致命错误说清楚：**先落盘，再（可选）弹窗**，返回日志路径。

    顺序不能反：弹窗可能失败（无人值守、会话 0、用户在别处），但落盘基本总会成功。
    """
    path = append_log("%s\n%s" % (title, detail), base=base)

    text = "%s\n\n%s\n\n详细记录已写入：\n%s" % (title, detail, path)

    # 有控制台（命令行、console 构建）时也留一份
    stream = getattr(sys, "stderr", None)
    if stream is not None:
        try:
            print(text, file=stream)
        except Exception:                                       # noqa: BLE001
            pass

    if dialog:
        # ⚠️ 不能指望 show_dialog 自己守约。dead-ends.md D2：
        # "绝不能影响主流程"的承诺，都要有一层**显式** try/except，
        # 不能依赖被调用方的实现细节 —— 这条我自己又犯过一次（被测试抓住）。
        try:
            show_dialog(title, text)
        except Exception:                                       # noqa: BLE001
            pass
    return path
