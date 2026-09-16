"""Windows 原生通知（托盘气泡 / 操作中心通知），纯 ctypes，零依赖。

## 为什么用 Shell_NotifyIcon 而不是"Windows 通知"

Win10 那套 `Windows.UI.Notifications`（Toast）需要 AppUserModelID + 快捷方式，
而且 **Win7 上根本不存在**。`Shell_NotifyIcon` + `NIF_INFO` 这个老接口反而是
唯一跨版本的：

    Win7            → 经典托盘气泡
    Win10 / Win11   → 系统自动转成通知中心的气泡/Toast
    WinXP～Win11    → 全都支持

所以它是"Windows 自带通知"里唯一能满足 Win7 约束的选择。

## ⚠️ 两个必须知道的限制

**1. 需要有人登录、且是交互式会话。**
   如果监控是以计划任务运行、勾了"不管用户是否登录"，进程会落在
   会话 0（非交互），`Shell_NotifyIcon` 会失败 —— 而**没有任何办法绕过**，
   这是 Windows 的设计。这种情况必须改用外部通道（邮件 / webhook）。

**2. 没人看着屏幕就等于没通知。**
   它只弹给当前登录的那个人。机器开着但没人在 → 通知弹了也没人看见。

这两条意味着：**Windows 通知适合"托盘常驻程序"这个形态**（也就是 GUI
那条路线），而不是无人值守的后台任务。本模块会如实报告失败原因，
不会假装成功。

## 实现要点（踩过的坑）

* 必须真的创建一个窗口 —— `Shell_NotifyIcon` 要 HWND，
  没有窗口就没有托盘图标，也就没有气泡。
* **所有返回句柄的 API 都要设 `restype = c_void_p`**，
  否则 64 位下返回值会被截断成 32 位，拿到一个野指针然后崩。
* 显示期间要**泵一下消息**，否则气泡可能根本没被画出来。
* 用完要 `NIM_DELETE` + `DestroyWindow`，别在托盘里留幽灵图标。
"""

from __future__ import annotations

import ctypes
import sys
import time

# ---------------------------------------------------------------- 常量

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR = 0x00000003

IDI_INFORMATION = 32516
IDI_WARNING = 32515
IDI_ERROR = 32513

# 结构体里的字段长度是 Windows 定死的（含结尾 NUL）
TIP_LEN = 128
INFO_LEN = 256
TITLE_LEN = 64

WS_OVERLAPPED = 0x00000000
UOI_NAME = 2

# Shell_NotifyIcon 的失败原因大多是环境问题，翻译成人能懂的话
_SESSION_HINT = (
    "Shell_NotifyIcon 调用失败。最常见的原因是**当前不是交互式桌面会话**：\n"
    "        · 计划任务勾了「不管用户是否登录」→ 进程在会话 0，弹不出通知\n"
    "        · 或这台机器上根本没人登录\n"
    "        Windows 通知必须由一个已登录用户的桌面进程来弹，这是系统限制，"
    "无法绕过。\n"
    "        这种场景请改用邮件或 webhook 通道。"
)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    """Vista+ 的布局。字段顺序不能动，错了就会写坏内存。"""

    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * TIP_LEN),
        ("dwState", ctypes.c_ulong),
        ("dwStateMask", ctypes.c_ulong),
        ("szInfo", ctypes.c_wchar * INFO_LEN),
        ("uTimeout", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * TITLE_LEN),
        ("dwInfoFlags", ctypes.c_ulong),
        ("guidItem", _GUID),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


def platform_supported() -> bool:
    return sys.platform == "win32"


def _load_kernel32():
    """`GetModuleHandleW` 在 **kernel32.dll**，不在 user32。

    这三个 DLL 的归属很容易记错，而记错的报错长这样：
        AttributeError: function 'Xxx' not found
    看起来像"系统没这个 API"，其实只是问错了库：
        GetModuleHandleW  → kernel32
        Shell_NotifyIconW → shell32
        其余（窗口/消息/图标/窗口站）→ user32
    """
    k = ctypes.windll.kernel32
    k.GetModuleHandleW.restype = ctypes.c_void_p
    k.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    return k


def _load_user32():
    """按需加载并**设好 restype**（见模块开头说明）。"""
    u = ctypes.windll.user32
    u.CreateWindowExW.restype = ctypes.c_void_p
    u.CreateWindowExW.argtypes = [
        ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    u.DefWindowProcW.restype = ctypes.c_void_p
    u.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_void_p]
    u.LoadIconW.restype = ctypes.c_void_p
    u.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.DestroyWindow.argtypes = [ctypes.c_void_p]
    u.PeekMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                               ctypes.c_uint, ctypes.c_uint]
    u.TranslateMessage.argtypes = [ctypes.c_void_p]
    u.DispatchMessageW.argtypes = [ctypes.c_void_p]
    u.RegisterClassW.restype = ctypes.c_ushort
    u.RegisterClassW.argtypes = [ctypes.c_void_p]
    # 进程窗口站句柄也是指针，同样必须设 restype
    u.GetProcessWindowStation.restype = ctypes.c_void_p
    u.GetProcessWindowStation.argtypes = []
    u.GetUserObjectInformationW.restype = ctypes.c_int
    u.GetUserObjectInformationW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    return u


def _load_shell32():
    s = ctypes.windll.shell32
    s.Shell_NotifyIconW.restype = ctypes.c_int
    s.Shell_NotifyIconW.argtypes = [ctypes.c_ulong,
                                    ctypes.POINTER(NOTIFYICONDATAW)]
    return s


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [("hWnd", ctypes.c_void_p),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p),
                ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_ulong),
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long)]


def _clip(text: str, limit: int) -> str:
    """按字段上限截断，并给省略号留位置。"""
    text = (text or "").replace("\x00", " ").strip()
    if len(text) <= limit - 1:
        return text
    return text[: limit - 2] + "…"


def show(title: str, text: str, hold_seconds: float = 5.0,
         level: str = "info") -> None:
    """弹一条 Windows 通知。失败**抛异常**（由调用方决定怎么处理）。

    `hold_seconds` 是"保持图标并泵消息"的时长。太快撤掉图标，
    气泡可能还没被画出来；所以默认等 5 秒。监控每 30 分钟才跑一次，
    这点延迟无所谓。
    """
    if not platform_supported():
        raise RuntimeError("Windows 通知只能在 Windows 上使用")

    u = _load_user32()
    shell = _load_shell32()
    kernel = _load_kernel32()
    hinstance = kernel.GetModuleHandleW(None)

    # 窗口过程：我们什么都不处理，交给系统默认的
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_uint, ctypes.c_void_p,
                                 ctypes.c_void_p)

    def _proc(hwnd, msg, wparam, lparam):
        return u.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(_proc)

    class_name = f"ytmon_notify_{int(time.time() * 1000) % 100000}"
    wc = _WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    wc.hInstance = hinstance
    wc.lpszClassName = class_name
    if not u.RegisterClassW(ctypes.byref(wc)):
        raise RuntimeError(
            f"注册窗口类失败（Windows 错误 {ctypes.GetLastError()}）。\n"
            f"        {_SESSION_HINT}")

    hwnd = u.CreateWindowExW(0, class_name, "ytmon", WS_OVERLAPPED,
                             0, 0, 0, 0, None, None, hinstance, None)
    if not hwnd:
        raise RuntimeError(
            f"创建隐藏窗口失败（Windows 错误 {ctypes.GetLastError()}）。\n"
            f"        {_SESSION_HINT}")

    icon_id = {"info": IDI_INFORMATION, "warning": IDI_WARNING,
               "error": IDI_ERROR}.get(level, IDI_INFORMATION)
    icon_flags = {"info": NIIF_INFO, "warning": NIIF_WARNING,
                  "error": NIIF_ERROR}.get(level, NIIF_INFO)

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE
    nid.uCallbackMessage = 0x0400 + 1
    nid.hIcon = u.LoadIconW(None, ctypes.c_void_p(icon_id))
    nid.szTip = _clip(title, TIP_LEN)

    if not shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        u.DestroyWindow(hwnd)
        raise RuntimeError(
            f"添加托盘图标失败（Windows 错误 {ctypes.GetLastError()}）。\n"
            f"        {_SESSION_HINT}")

    try:
        # NIM_MODIFY + NIF_INFO 才是"弹气泡"这一步
        nid.uFlags = NIF_INFO | NIF_ICON | NIF_TIP
        nid.szInfoTitle = _clip(title, TITLE_LEN)
        nid.szInfo = _clip(text, INFO_LEN)
        nid.dwInfoFlags = icon_flags
        nid.uTimeout = 10000
        if not shell.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
            raise RuntimeError(
                f"显示通知失败（Windows 错误 {ctypes.GetLastError()}）。\n"
                f"        {_SESSION_HINT}")

        # 泵消息，确保气泡真的被画出来
        msg = _MSG()
        deadline = time.monotonic() + max(0.0, hold_seconds)
        PM_REMOVE = 0x0001
        while time.monotonic() < deadline:
            while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.05)
    finally:
        # 别在托盘里留幽灵图标
        shell.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        u.DestroyWindow(hwnd)


def probe() -> tuple[bool, str]:
    """不弹通知，只判断"这台机器上能不能弹"。给自检用。

    会话 0（无人登录的计划任务、服务）永远弹不出通知 ——
    提前问出来，比让用户对着一个静默失败的通道猜要好。
    """
    if not platform_supported():
        return False, "不是 Windows 平台"
    try:
        u = _load_user32()
        h = u.GetProcessWindowStation()
        if h:
            need = ctypes.c_ulong(0)
            u.GetUserObjectInformationW(h, UOI_NAME, None, 0, ctypes.byref(need))
            size = max(need.value, 64)
            buf = ctypes.create_unicode_buffer(size)
            if u.GetUserObjectInformationW(h, UOI_NAME, buf, ctypes.sizeof(buf),
                                           ctypes.byref(need)):
                name = buf.value
                if name and name.lower() != "winsta0":
                    return False, (
                        f"当前进程在窗口站 {name!r}（不是 WinSta0），属于非交互式会话。\n"
                        f"        Windows 通知弹不出来 —— 这是系统限制，无法绕过。\n"
                        f"        请改用邮件或 webhook 通道。")
        return True, "可以弹通知"
    except Exception as e:                                  # noqa: BLE001
        return False, f"探测失败：{type(e).__name__}: {e}"
