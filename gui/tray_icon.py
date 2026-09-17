"""Qt5 托盘图标着色；保留原透明度，不修改 SVG 资源。"""

from qt_compat import QtCore, QtGui
from gui.tray_theme import supports_system_theme, taskbar_dark


def tinted_icon(source, color):
    icon = QtGui.QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64):
        pixmap = source.pixmap(size, size)
        if pixmap.isNull():
            continue
        painter = QtGui.QPainter(pixmap)
        try:
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), QtGui.QColor(color))
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    return icon if not icon.isNull() else source


class TrayIconTheme(QtCore.QObject):
    def __init__(self, tray, source, parent=None):
        super().__init__(parent)
        self.tray = tray
        self.source = source
        self.icons = {}
        self.last_theme = object()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def set_enabled(self, enabled):
        self.refresh()
        if enabled and supports_system_theme():
            self.timer.start()
        else:
            self.timer.stop()

    def refresh(self):
        dark = taskbar_dark()
        if dark == self.last_theme:
            return
        icon = self.source
        if dark is not None:
            try:
                if dark not in self.icons:
                    self.icons[dark] = tinted_icon(self.source, '#ffffff' if dark else '#000000')
                icon = self.icons[dark]
            except (RuntimeError, ValueError):
                # 图标处理失败不影响托盘驻留，保留原图并允许下次重试。
                self.tray.setIcon(self.source)
                return
        self.tray.setIcon(icon)
        self.last_theme = dark
