"""Qt 绑定兼容层：同一套界面代码同时支持 PySide2 与 PySide6。

## 绑定优先级（2026-09-16 定案：**PySide2 优先**）

    PySide2 → Qt 5.15 —— **交付目标**。Win7 → Win11 都能跑。
    PySide6 → Qt 6    —— **遗留分支，非交付目标**。Qt 6 不支持 Win7。

为什么是 PySide2 在前，而不是"新版本优先"：

    交付目标是 Win7，而 Qt 6 不支持 Win7。如果让 PySide6 优先，
    一旦环境里同时存在两者（或将来环境升级到 3.9/3.10），就会**静默选中 Qt 6** ——
    "能在 Win7 上跑"这个前提会无声失效，而且要到现场才发现。
    宁可让"更旧、但能交付"的那个说了算。

`qfluentwidgets` 的模块名在两个绑定下**是同一个**，所以只有 Qt 本身的
import 需要分叉。把这些差异集中在这里，`app.py` 就只写一套。

用法：
    from qt_compat import BINDING, QThread, Signal, QtWidgets, ...
"""

from __future__ import annotations

# ------------------------------------------------------------------ Qt 绑定

BINDING = ""
QT_VERSION = ""

try:                                    # 首选：Qt 5.15（交付目标，Win7 也能跑）
    from PySide2 import QtCore, QtGui, QtWidgets          # type: ignore # noqa: F401
    from PySide2.QtCore import QThread, Signal, Slot      # type: ignore # noqa: F401
    BINDING = "PySide2"
except ImportError:                     # 退回：Qt 6（遗留分支，非交付目标）
    try:
        from PySide6 import QtCore, QtGui, QtWidgets      # noqa: F401
        from PySide6.QtCore import QThread, Signal, Slot  # noqa: F401
        BINDING = "PySide6"
    except ImportError as e:
        raise ImportError(
            "没有找到 Qt 绑定。按**交付目标**安装：\n"
            "  pip install -r requirements-win7-gui.txt    # PySide2 / Qt 5.15（交付目标）\n"
            "PySide6（Qt 6）是遗留分支 —— Qt 6 不支持 Win7，\n"
            "只在确定不需要 Win7 时才考虑。\n"
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
