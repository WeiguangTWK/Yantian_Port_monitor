"""Qt 绑定兼容层：同一套界面代码同时支持 PySide6 与 PySide2。

为什么需要：
    * Win10/11 → PySide6（Qt 6）
    * Win7     → PySide2（Qt 5.15），因为 Qt 6 不支持 Win7

`qfluentwidgets` 的模块名在两个绑定下**是同一个**，所以只有 Qt 本身的
import 需要分叉。把这些差异集中在这里，`app.py` 就只写一套。

用法：
    from qt_compat import BINDING, QThread, Signal, QtWidgets, ...
"""

from __future__ import annotations

# ------------------------------------------------------------------ Qt 绑定

BINDING = ""
QT_VERSION = ""

try:                                    # 首选：Qt 6（Win10/11）
    from PySide6 import QtCore, QtGui, QtWidgets          # noqa: F401
    from PySide6.QtCore import QThread, Signal, Slot      # noqa: F401
    BINDING = "PySide6"
except ImportError:                     # 退回：Qt 5.15（Win7）
    try:
        from PySide2 import QtCore, QtGui, QtWidgets      # type: ignore # noqa: F401
        from PySide2.QtCore import QThread, Signal, Slot  # type: ignore # noqa: F401
        BINDING = "PySide2"
    except ImportError as e:
        raise ImportError(
            "没有找到 Qt 绑定。请按平台安装：\n"
            "  Win10/11:  pip install PySide6-Fluent-Widgets\n"
            "  Win7:      pip install -r requirements-win7-gui.txt\n"
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
