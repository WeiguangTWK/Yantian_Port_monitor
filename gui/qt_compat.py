"""Qt 绑定兼容层：同一套界面代码同时支持 PySide2 与 PySide6。

## 绑定选择

    Windows：PySide2 → Qt 5.15，保留 Win7 交付路线；缺失时退回 PySide6。
    Linux：PySide6 → Qt 6，不尝试导入 PySide2。

Windows 保持 PySide2 优先，避免同时安装两套绑定时静默选中不支持 Win7
的 Qt 6。AOSC LoongArch 实测 PySide2 顶层导入成功，但 QtCore 在
Shiboken 初始化时段错误；这种原生崩溃不能由 ImportError 捕获，
因此 Linux 必须在导入之前明确选择已验证可用的 PySide6。

`qfluentwidgets` 的模块名在两个绑定下**是同一个**，所以只有 Qt 本身的
import 需要分叉；两种 Fluent 包不可装入同一环境。把这些差异集中在这里，
`app.py` 就只写一套。

用法：
    from qt_compat import BINDING, QThread, Signal, QtWidgets, ...
"""

from __future__ import annotations
import sys

# ------------------------------------------------------------------ Qt 绑定

BINDING = ""
QT_VERSION = ""

if sys.platform.startswith("linux"):
    try:
        from PySide6 import QtCore, QtGui, QtWidgets      # noqa: F401
        from PySide6.QtCore import QThread, Signal, Slot  # noqa: F401
        BINDING = "PySide6"
    except ImportError as e:
        raise ImportError(
            "Linux GUI 需要 PySide6 / Qt 6；不会回退到 PySide2。\n"
            "请先安装与本机架构匹配的 PySide6，再安装 PySide6-Fluent-Widgets。\n"
            f"（原始错误：{e}）"
        ) from e
else:
    try:                                # Windows 优先：Qt 5.15，兼容 Win7
        from PySide2 import QtCore, QtGui, QtWidgets      # type: ignore # noqa: F401
        from PySide2.QtCore import QThread, Signal, Slot  # type: ignore # noqa: F401
        BINDING = "PySide2"
    except ImportError:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
            from PySide6.QtCore import QThread, Signal, Slot  # noqa: F401
            BINDING = "PySide6"
        except ImportError as e:
            raise ImportError(
                "没有找到 Qt 绑定。Windows 交付目标请安装 PySide2 / Qt 5.15：\n"
                "  pip install -r requirements-win7-gui.txt\n"
                f"（原始错误：{e}）"
            ) from e

try:
    QT_VERSION = QtCore.qVersion()
except Exception:                                           # noqa: BLE001
    pass


# ------------------------------------------------------------ 兼容细节

def exec_app(app) -> int:
    """PySide2/PySide6 都有 exec_()，但新代码用 exec()。这里统一。"""
    runner = getattr(app, "exec", None) or getattr(app, "exec_")
    return runner()


# 枚举访问：PySide6 4.x 起推荐 QAbstractItemView.EditTrigger.NoEditTriggers，
# 但旧的平坦写法在两者上都能用。集中在这里，将来只需改一处。
NO_EDIT_TRIGGERS = QtWidgets.QAbstractItemView.NoEditTriggers

__all__ = [
    "BINDING", "QT_VERSION", "QtCore", "QtGui", "QtWidgets",
    "QThread", "Signal", "Slot", "exec_app", "NO_EDIT_TRIGGERS",
]
