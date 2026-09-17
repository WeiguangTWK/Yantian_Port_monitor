"""Fluent 风格监听设置页。参数由 GUI 自动监听与查询后端读取。"""

from __future__ import annotations

from qt_compat import QtWidgets, Signal
from gui.monitor_config import MonitorConfig, NUMERIC_FIELDS
from ytmon.browser_find import browser_brand, find_browser
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            DoubleSpinBox, LineEdit, PrimaryPushButton, PushButton,
                            ScrollArea, SpinBox, SubtitleLabel)


class MonitorSettingsPage(QtWidgets.QWidget):
    changed = Signal()

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.store = MonitorConfig(path)
        self.busy = False
        self.loaded = False
        self.inputs = {}
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)
        root.addWidget(SubtitleLabel('监听设置', self))
        hint = BodyLabel('在主页点击“开始监听”启用自动查询。设置保存后，等待中的倒计时重新开始；查询中暂不可修改。', self)
        hint.setWordWrap(True)
        root.addWidget(hint)
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        view = QtWidgets.QWidget()
        scroll.setWidget(view)
        layout = QtWidgets.QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        def card(title, description):
            widget = CardWidget(view)
            box = QtWidgets.QVBoxLayout(widget)
            box.setContentsMargins(20, 16, 20, 20)
            box.setSpacing(12)
            box.addWidget(SubtitleLabel(title, widget))
            label = CaptionLabel(description, widget)
            label.setWordWrap(True)
            box.addWidget(label)
            form = QtWidgets.QFormLayout()
            form.setVerticalSpacing(12)
            form.setHorizontalSpacing(24)
            box.addLayout(form)
            layout.addWidget(widget)
            return widget, form

        def number(form, parent, key, title, suffix):
            minimum, maximum, integer = NUMERIC_FIELDS[key]
            control = (SpinBox if integer else DoubleSpinBox)(parent)
            control.setRange(minimum, maximum)
            if not integer:
                control.setDecimals(3)
            control.setSuffix(suffix)
            control.setMinimumWidth(190)
            form.addRow(BodyLabel(title, parent), control)
            self.inputs[key] = control

        widget, form = card('监听计划', 'CLI 的循环间隔仍由 --watch 指定，不会自动读取这里的 GUI 间隔。')
        number(form, widget, 'watch_interval_seconds', '监听间隔', ' 秒')
        number(form, widget, 'watch_jitter_seconds', '额外随机等待上限', ' 秒')
        self.close_to_tray = CheckBox('关闭窗口后驻留系统托盘，继续后台监听', widget)
        form.addRow(self.close_to_tray)
        tray_hint = CaptionLabel('默认关闭。开启后关窗仅隐藏界面；从托盘菜单退出程序。不自动开始监听，也不设置开机自启。', widget)
        tray_hint.setWordWrap(True)
        form.addRow(tray_hint)
        widget, form = card('查询与重试', '起始日为今天减去回溯天数；增加分页会增加站点请求。限流时采用退避重试。')
        number(form, widget, 'etb_back_days', '回溯天数', ' 天')
        number(form, widget, 'max_pages', '最多查询分页', ' 页')
        number(form, widget, 'retry_attempts', '限流重试次数', ' 次')
        number(form, widget, 'retry_backoff_seconds', '退避基础等待', ' 秒')
        widget, form = card('浏览器', '浏览器用于获取查询 Cookie。留空路径时自动查找本机兼容浏览器。')
        path_row = QtWidgets.QHBoxLayout()
        self.browser = LineEdit(widget)
        self.browser.setPlaceholderText('自动查找，或填写浏览器 exe 完整路径')
        browse = PushButton('浏览…', widget)
        browse.clicked.connect(self.browse)
        path_row.addWidget(self.browser, 1)
        path_row.addWidget(browse)
        form.addRow(BodyLabel('浏览器路径', widget), path_row)
        self.browser_detected = CaptionLabel(widget)
        self.browser_detected.setWordWrap(True)
        form.addRow(self.browser_detected)
        detect = PushButton('重新检测', widget)
        detect.clicked.connect(self.detect_browser)
        form.addRow(detect)
        self.browser.textChanged.connect(self.detect_browser)
        self.headless = CheckBox('无头模式（不显示引导窗口）', widget)
        form.addRow(self.headless)
        layout.addStretch(1)
        root.addWidget(scroll, 1)
        buttons = QtWidgets.QHBoxLayout()
        self.save_button = PrimaryPushButton('保存设置', self)
        self.reload_button = PushButton('重新加载', self)
        self.save_button.clicked.connect(self.save)
        self.reload_button.clicked.connect(self.reload)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.reload_button)
        buttons.addStretch()
        root.addLayout(buttons)
        self.message = BodyLabel(self)
        self.message.setWordWrap(True)
        root.addWidget(self.message)
        self.form_view = view
        self.reload()

    def browse(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择浏览器', self.browser.text(), '可执行文件 (*.exe)')
        if filename:
            self.browser.setText(filename)

    def detect_browser(self):
        explicit = self.browser.text().strip()
        try:
            path = find_browser(explicit or None)
        except (OSError, ValueError) as error:
            self.browser_detected.setText(str(error))
            return
        self.browser_detected.setText('%s：%s\n%s（仅检查文件存在，未验证启动或查询兼容性）' % (
            '手动路径' if explicit else '自动检测到 ' + browser_brand(path), path,
            '手动路径优先' if explicit else 'Win7 优先 Supermium，其他系统优先 Edge'))

    def set_busy(self, busy):
        self.busy = busy
        self.form_view.setEnabled(not busy)
        self.save_button.setEnabled(not busy and self.loaded)
        self.reload_button.setEnabled(not busy)
        if busy:
            self.message.setText('查询进行中，暂不可修改设置。')
        else:
            self.message.setText('')

    def refresh_snapshot(self):
        # 其他 GUI 页面保存后只更新文件快照，不覆盖本页未保存的输入。
        try:
            self.store.reload()
        except (OSError, ValueError) as error:
            self.message.setText(str(error))

    def reload(self):
        if self.busy:
            return
        try:
            self.store.reload()
            values = self.store.values()
        except (OSError, ValueError) as error:
            self.message.setText(str(error))
            self.loaded = False
            self.save_button.setEnabled(False)
            return
        for key, control in self.inputs.items():
            control.setValue(values[key])
        self.browser.setText(values['edge_path'] or '')
        self.detect_browser()
        self.headless.setChecked(values['headless'])
        self.close_to_tray.setChecked(values['close_to_tray'])
        self.loaded = True
        self.save_button.setEnabled(True)
        self.message.setText('设置已加载。修改后点击“保存设置”生效。')

    def save(self):
        if self.busy or not self.loaded:
            return
        values = {key: control.value() for key, control in self.inputs.items()}
        values.update(edge_path=self.browser.text(), headless=self.headless.isChecked(),
                      close_to_tray=self.close_to_tray.isChecked())
        try:
            self.store.save_values(values)
        except (OSError, ValueError) as error:
            self.message.setText(str(error))
            return
        self.message.setText('设置已保存。监听中会重置下一轮倒计时；未监听时请在主页点击“开始监听”。')
        self.changed.emit()
