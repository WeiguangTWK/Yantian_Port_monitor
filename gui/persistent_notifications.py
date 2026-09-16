"""无超时的非模态通知窗口，与主窗口最小化状态独立。"""

import datetime as dt

from qt_compat import QtCore, QtGui, QtWidgets, Signal
from qfluentwidgets import CaptionLabel, CardWidget, PushButton, TextEdit


class NotificationWindow(QtWidgets.QWidget):
    dismissed = Signal()

    def __init__(self):
        super().__init__(None, QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('船期通知')
        self.resize(460, 360)
        root = QtWidgets.QVBoxLayout(self)
        card = CardWidget(self)
        box = QtWidgets.QVBoxLayout(card)
        hint = CaptionLabel('保持显示，直到手动关闭；退出程序时关闭。', card)
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.messages = TextEdit(card)
        self.messages.setReadOnly(True)
        box.addWidget(self.messages, 1)
        close = PushButton('关闭通知', card)
        close.clicked.connect(self.close)
        box.addWidget(close)
        root.addWidget(card)
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(max(area.left(), area.right() - self.width() - 16),
                      max(area.top(), area.bottom() - self.height() - 16))

    def append_message(self, title, text):
        cursor = self.messages.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        if not self.messages.document().isEmpty():
            cursor.insertText('\n\n')
        cursor.insertText('%s  %s\n%s' % (dt.datetime.now().strftime('%H:%M:%S'), title, text))
        self.messages.setTextCursor(cursor)
        self.messages.ensureCursorVisible()
        self.show()

    def closeEvent(self, event):
        self.dismissed.emit()
        super().closeEvent(event)


class PersistentNotifications(QtCore.QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.popup = None

    @QtCore.Slot(str, str)
    def show_message(self, title, text):
        if self.popup is None:
            self.popup = NotificationWindow()
            self.popup.dismissed.connect(self._dismissed)
        self.popup.append_message(title, text)

    def _dismissed(self):
        self.popup = None

    def close_all(self):
        if self.popup is not None:
            self.popup.close()
