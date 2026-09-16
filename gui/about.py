"""关于、软件许可与独立的数据源使用风险说明。"""

import pathlib
import sys
from importlib.metadata import PackageNotFoundError, version

from qt_compat import BINDING, QT_VERSION, QtCore, QtGui, QtWidgets, exec_app
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, MessageBoxBase,
                            PushButton, ScrollArea, SubtitleLabel, TextEdit)
from ytmon import __version__

SOURCE_URL = 'https://github.com/WeiguangTWK/Yantian_Port_monitor'
RESOURCE_ROOT = (pathlib.Path(sys._MEIPASS) if getattr(sys, 'frozen', False)
                 else pathlib.Path(__file__).resolve().parent.parent)


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return '版本信息不可用'


class AboutPage(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)
        root.addWidget(SubtitleLabel('关于', self))
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        view = QtWidgets.QWidget()
        scroll.setWidget(view)
        layout = QtWidgets.QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        def card(title, paragraphs):
            widget = CardWidget(view)
            box = QtWidgets.QVBoxLayout(widget)
            box.setContentsMargins(20, 16, 20, 20)
            box.setSpacing(10)
            box.addWidget(SubtitleLabel(title, widget))
            for text in paragraphs:
                label = BodyLabel(text, widget)
                label.setWordWrap(True)
                label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                box.addWidget(label)
            layout.addWidget(widget)
            return widget, box

        info, box = card('盐田船期监控', [
            '版本 %s' % __version__,
            '按船名或码头航次查询船期，比较变化并发送通知。',
            '运行环境：Python %s · %s %s · Qt %s' % (
                sys.version.split()[0], BINDING, package_version(BINDING), QT_VERSION),
            SOURCE_URL])
        author_row = QtWidgets.QHBoxLayout()
        author_row.setSpacing(16)
        author = BodyLabel('作者 WeiguangTWK · 新桂轮未来生产实验室', info)
        author.setWordWrap(True)
        author.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        author_row.addWidget(author, 1)
        logo = QtWidgets.QLabel(info)
        logo.setFixedSize(220, 64)
        logo.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        logo.setAccessibleName('新桂轮标志')
        pixmap = QtGui.QPixmap(str(RESOURCE_ROOT / 'gui/assets/newguilun_logo.jpg'))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(logo.size(), QtCore.Qt.KeepAspectRatio,
                                         QtCore.Qt.SmoothTransformation))
        else:
            logo.setText('新桂轮')
        author_row.addWidget(logo)
        box.insertLayout(2, author_row)
        source = PushButton('打开源码仓库', info)
        source.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(SOURCE_URL)))
        box.addWidget(source)

        legal, box = card('软件许可证 · GPLv3', [
            'Copyright © 2026 WeiguangTWK。项目按 GNU General Public License 第 3 版分发。',
            '可按 GPLv3 使用、修改和再分发，包括商业用途。分发时须遵守对应源码提供、版权与许可证保留等义务；不附加“禁止商用”限制。',
            '在适用法律允许的范围内，本程序按现状提供，不承诺数据准确性、持续可用性、通知必达或特定用途适用性。本说明不排除依法不得排除或限制的责任。',
            '源码仓库入口不等于已完成对应源码交付。发布者应提供与所发版本匹配的完整对应源码及构建资料。'])
        license_button = PushButton('查看 GPLv3 全文', legal)
        license_button.clicked.connect(lambda: self.show_document('GNU GPLv3', 'LICENSE'))
        box.addWidget(license_button)

        card('数据来源与 API 使用风险', [
            '数据来源：www.156yt.cn（易物流盐田）。本程序为第三方辅助工具，并非港口或网站运营方提供的官方产品，不代表其认可、授权或合作关系。',
            '查询与通知可能存在延迟、遗漏或错误。实际船期、截关及作业安排请以官方渠道和相关业务方确认为准，请勿仅依据本程序安排运输、报关或费用。',
            '接口可访问或未设置鉴权，不代表运营方已授权第三方自动查询、复制或再利用数据。软件 GPLv3 许可不授予数据源的任何使用权。',
            '使用者应确认访问方式符合适用法律及数据源服务条款。避免全量扫描、高并发和持续重试；不得实施未经授权的访问、绕过访问控制或影响服务正常运行的行为。遇到访问限制或运营方停止要求，应暂停自动查询。',
            '数据对外发布、批量汇总、转售或其他再利用应另行确认授权。免费、个人使用或小范围分享，不构成合规保证。'])

        third, box = card('第三方组件', [
            '%s %s：Qt for Python；开源使用可适用 LGPLv3，具体组件以随附许可证为准。' % (BINDING, package_version(BINDING)),
            '%s-Fluent-Widgets %s：zhiyiYo 的 Fluent 界面组件，安装包标注 GPLv3。作者另提供商业授权；本项目不宣称已购买商业授权。' % (BINDING, package_version(BINDING + '-Fluent-Widgets')),
            'GPLv3 本身允许商用；Fluent 作者对商业授权的表述应另行核实。本页面不替代第三方许可原文或完整交付审查。'])
        for title, relative in [('查看第三方说明', 'licenses/THIRD_PARTY_NOTICES.md'),
                                ('查看 LGPLv3 全文', 'licenses/LGPL-3.0.txt')]:
            button = PushButton(title, third)
            button.clicked.connect(lambda checked=False, title=title, relative=relative: self.show_document(title, relative))
            box.addWidget(button)
        hint = CaptionLabel('本页面不自动联网；点击源码仓库按钮时才由系统浏览器打开链接。', view)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        root.addWidget(scroll, 1)

    def show_document(self, title, relative):
        dialog = MessageBoxBase(self.window())
        dialog.viewLayout.addWidget(SubtitleLabel(title, dialog.widget))
        content = TextEdit(dialog.widget)
        content.setReadOnly(True)
        content.setMinimumSize(560, 360)
        try:
            text = (RESOURCE_ROOT / relative).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            text = '许可文件无法读取，请检查交付包是否完整：%s' % relative
        content.setPlainText(text)
        dialog.viewLayout.addWidget(content)
        dialog.yesButton.setText('关闭')
        dialog.cancelButton.hide()
        exec_app(dialog)
