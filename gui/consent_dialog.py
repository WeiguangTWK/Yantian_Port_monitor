"""首次使用时阻塞主窗口的站点条款确认。"""

from qt_compat import QtCore, QtWidgets, exec_app
from qfluentwidgets import BodyLabel, CaptionLabel, CheckBox, PrimaryPushButton, PushButton, SubtitleLabel

TERMS_URL = 'https://www.156yt.cn/register/register_protocol.jsp'


class SiteTermsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('使用前确认')
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setMinimumWidth(540)
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(28, 24, 28, 24)
        box.setSpacing(14)
        box.addWidget(SubtitleLabel('使用易物流盐田数据前请确认', self))
        terms = BodyLabel(self)
        terms.setTextFormat(QtCore.Qt.RichText)
        terms.setOpenExternalLinks(True)
        terms.setText('请先阅读<a href="%s">易物流盐田服务协议（点击打开官方页面）</a>。' % TERMS_URL)
        box.addWidget(terms)
        promise = BodyLabel(
            '我承诺不高并发、全量扫描、持续重试或绕过访问控制；'
            '遇到访问限制或运营方停止要求时暂停查询。', self)
        promise.setWordWrap(True)
        box.addWidget(promise)
        caution = CaptionLabel(
            '程序仅在用户同意以上条款时运行', self)
        caution.setWordWrap(True)
        box.addWidget(caution)
        self.agree = CheckBox('我已阅读并同意以上条款', self)
        box.addWidget(self.agree)
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch()
        cancel = PushButton('退出程序', self)
        cancel.clicked.connect(self.reject)
        self.confirm = PrimaryPushButton('同意并继续', self)
        self.confirm.setEnabled(False)
        self.agree.toggled.connect(self.confirm.setEnabled)
        self.confirm.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(self.confirm)
        box.addLayout(actions)
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            self.adjustSize()
            self.move(screen.availableGeometry().center() - self.rect().center())


def ask_site_terms():
    return exec_app(SiteTermsDialog()) == QtWidgets.QDialog.Accepted
