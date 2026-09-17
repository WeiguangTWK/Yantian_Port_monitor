"""Fluent 通知配置与确认后的后台测试推送。"""

from __future__ import annotations

import copy

from qt_compat import QtGui, QtWidgets, QThread, Signal, exec_app
from gui.notify_config import NotificationConfig, POLICY_RANGES, redact
from gui.notification_transport import dispatch_windows
from ytmon.config import AppConfig, NotifyChannel, VALID_CHANNELS, URL_CHANNELS
from ytmon.notify import Notifier, test_message
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox,
                            DoubleSpinBox, LineEdit, MessageBox, MessageBoxBase,
                            PrimaryPushButton, PushButton, ScrollArea, SpinBox,
                            SimpleExpandGroupSettingCard, SubtitleLabel, TextEdit)

CHANNEL_NAMES = {'windows': 'Windows 系统通知', 'dingtalk': '钉钉', 'wecom': '企业微信',
                 'feishu': '飞书', 'webhook': '通用 webhook', 'email': '邮件'}


class ChannelEditor(MessageBoxBase):
    def __init__(self, channel=None, parent=None):
        super().__init__(parent)
        self.save_action = None
        self.base = copy.deepcopy(channel or NotifyChannel(kind='windows'))
        self.viewLayout.addWidget(SubtitleLabel('编辑通知渠道' if channel else '新增通知渠道', self.widget))
        self.controls = {}
        self.rows = {}
        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(10)
        self.viewLayout.addLayout(form)
        self.kind = ComboBox(self.widget)
        for name in VALID_CHANNELS:
            self.kind.addItem(CHANNEL_NAMES[name], userData=name)
        form.addRow(BodyLabel('渠道类型', self.widget), self.kind)

        def field(key, title, control):
            label = BodyLabel(title, self.widget)
            form.addRow(label, control)
            self.controls[key] = control
            self.rows[key] = (label, control)
            return control

        for key, title in [('label', '显示名称'), ('url', '机器人 / webhook 地址'),
                           ('secret', '加签密钥（可选）'), ('smtp_host', 'SMTP 服务器'),
                           ('smtp_user', '登录账号（可选）'), ('smtp_password', '密码 / 授权码'),
                           ('mail_from', '发件人（可选）'), ('mail_to', '收件人邮箱')]:
            control = field(key, title, LineEdit(self.widget))
            value = getattr(self.base, key)
            control.setText(', '.join(value) if isinstance(value, list) else value)
            if key in ('url', 'secret', 'smtp_password'):
                control.setEchoMode(QtWidgets.QLineEdit.Password)
        self.controls['mail_to'].setPlaceholderText('多个地址以逗号分隔')
        port = field('smtp_port', 'SMTP 端口', SpinBox(self.widget))
        port.setRange(1, 65535)
        port.setValue(self.base.smtp_port)
        persistent = field('persistent', '保持显示', CheckBox('直到手动关闭（程序浮窗）', self.widget))
        persistent.setChecked(self.base.persistent)
        persistent.stateChanged.connect(self._update_fields)
        hold = field('hold_seconds', '通知停留时间', DoubleSpinBox(self.widget))
        hold.setRange(0, 300)
        hold.setSuffix(' 秒')
        hold.setValue(self.base.hold_seconds)
        ssl = field('use_ssl', '连接方式', CheckBox('使用 SSL', self.widget))
        ssl.setChecked(self.base.use_ssl)
        self.enabled = CheckBox('启用此渠道', self.widget)
        self.enabled.setChecked(self.base.enabled)
        form.addRow(self.enabled)
        self.show_sensitive = CheckBox('显示敏感信息', self.widget)
        self.show_sensitive.stateChanged.connect(self._show_sensitive)
        form.addRow(self.show_sensitive)
        self.hint = CaptionLabel(self.widget)
        self.hint.setWordWrap(True)
        self.viewLayout.addWidget(self.hint)
        self.error = BodyLabel(self.widget)
        self.error.setWordWrap(True)
        self.viewLayout.addWidget(self.error)
        self.yesButton.setText('保存渠道')
        self.cancelButton.setText('取消')
        self.widget.setMinimumWidth(560)
        self.kind.setCurrentIndex(self.kind.findData(self.base.kind))
        self.kind.currentIndexChanged.connect(self._update_fields)
        self._update_fields()

    def _show_sensitive(self):
        mode = QtWidgets.QLineEdit.Normal if self.show_sensitive.isChecked() else QtWidgets.QLineEdit.Password
        for key in ('url', 'secret', 'smtp_password'):
            self.controls[key].setEchoMode(mode)

    def _update_fields(self):
        kind = self.kind.currentData()
        visible = {'label'}
        if kind in URL_CHANNELS:
            visible.add('url')
        if kind in ('dingtalk', 'feishu'):
            visible.add('secret')
        if kind == 'email':
            visible.update(('smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'mail_from', 'mail_to', 'use_ssl'))
        if kind == 'windows':
            visible.update(('hold_seconds', 'persistent'))
        for key, widgets in self.rows.items():
            for widget in widgets:
                widget.setVisible(key in visible)
        for widget in self.rows['hold_seconds']:
            widget.setEnabled(not self.controls['persistent'].isChecked())
        self.hint.setText('保持显示使用程序浮窗，退出程序时关闭。关闭开关使用系统通知，实际停留时间由 Windows 决定；秒数控制托盘消息处理时长。需要已登录的桌面会话。'
                          if kind == 'windows' else '非 SSL 邮件会尝试 STARTTLS，服务器不支持时可能明文发送。建议保持 SSL。'
                          if kind == 'email' else '请填写平台提供的完整机器人地址。密钥保存在本地 watchlist.json，请勿分享。')

    def channel(self):
        channel = copy.deepcopy(self.base)
        channel.kind = self.kind.currentData()
        channel.enabled = self.enabled.isChecked()
        # 隐藏字段不带入新类型；编辑同类型时保留已有字段。
        for key, (_, widget) in self.rows.items():
            if widget.isHidden():
                setattr(channel, key, getattr(NotifyChannel(), key))
            elif key == 'mail_to':
                channel.mail_to = [address.strip() for address in widget.text().replace('；', ',').replace(';', ',').replace('，', ',').split(',') if address.strip()]
            elif key in ('use_ssl', 'persistent'):
                setattr(channel, key, widget.isChecked())
            elif key in ('smtp_port', 'hold_seconds'):
                setattr(channel, key, widget.value())
            else:
                value = widget.text()
                setattr(channel, key, value if key == 'smtp_password' else value.strip())
        return channel

    def validate(self):
        return self.save_action is not None and self.save_action(self.channel())


class TestPushWorker(QThread):
    results = Signal(object)
    failed = Signal(str)
    persistent_notification = Signal(str, str)

    def __init__(self, channels, settings, parent=None):
        super().__init__(parent)
        self.channels = copy.deepcopy(channels)
        self.settings = copy.deepcopy(settings)

    def run(self):
        try:
            results = Notifier(self.channels, self.settings,
                               windows_transport=lambda channel, message: dispatch_windows(
                                   channel, message, self.persistent_notification.emit)).send(test_message())
            self.results.emit([(redact(result.channel, self.channels), result.ok,
                                redact(result.detail, self.channels)) for result in results])
        except Exception as error:
            self.failed.emit(redact('%s: %s' % (type(error).__name__, error), self.channels))


class NotificationsPage(QtWidgets.QWidget):
    changed = Signal()
    testing_changed = Signal(bool)
    persistent_notification = Signal(str, str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.store = NotificationConfig(path)
        self.busy = False
        self.testing = False
        self.loaded = False
        self.worker = None
        self.selected_index = None
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)
        root.addWidget(SubtitleLabel('通知与测试', self))
        hint = BodyLabel('渠道修改立即保存；触发策略单独保存。测试会真实发送消息，但不会查询船期或修改告警冷却记录。', self)
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
        card = CardWidget(view)
        box = QtWidgets.QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.addWidget(SubtitleLabel('通知渠道', card))
        bar = QtWidgets.QHBoxLayout()
        self.buttons = []
        for text, callback in [('新增渠道', self.add), ('编辑', self.edit), ('启用 / 停用', self.toggle),
                               ('删除', self.delete), ('重新加载', self.reload)]:
            button = (PrimaryPushButton if text == '新增渠道' else PushButton)(text, card)
            button.clicked.connect(callback)
            bar.addWidget(button)
            self.buttons.append(button)
        bar.addStretch()
        box.addLayout(bar)
        self.channel_list = QtWidgets.QVBoxLayout()
        self.channel_list.setSpacing(8)
        box.addLayout(self.channel_list)
        layout.addWidget(card)
        policy_card = CardWidget(view)
        policy_box = QtWidgets.QVBoxLayout(policy_card)
        policy_box.setContentsMargins(20, 16, 20, 20)
        policy_box.addWidget(SubtitleLabel('触发策略', policy_card))
        checks = QtWidgets.QHBoxLayout()
        self.triggers = {}
        for state, text in [('changed', '船期变更'), ('missing', '不存在'), ('error', '查询失败'), ('first', '首次查询')]:
            control = CheckBox(text, policy_card)
            checks.addWidget(control)
            self.triggers[state] = control
        policy_box.addLayout(checks)
        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(12)
        self.policy_inputs = {}
        for key, title, suffix in [('alert_error_after', '连续错误阈值', ' 轮'),
                                    ('alert_cooldown_seconds', '同类通知冷却', ' 秒'),
                                    ('alert_max_per_cycle', '每批目标告警数量', ' 条')]:
            control = SpinBox(policy_card)
            control.setRange(*POLICY_RANGES[key])
            control.setSuffix(suffix)
            form.addRow(BodyLabel(title, policy_card), control)
            self.policy_inputs[key] = control
        policy_box.addLayout(form)
        self.save_policy_button = PrimaryPushButton('保存触发策略', policy_card)
        self.save_policy_button.clicked.connect(self.save_policy)
        policy_box.addWidget(self.save_policy_button)
        layout.addWidget(policy_card)
        test_card = CardWidget(view)
        test_box = QtWidgets.QVBoxLayout(test_card)
        test_box.setContentsMargins(20, 16, 20, 20)
        test_box.addWidget(SubtitleLabel('测试推送', test_card))
        test_hint = CaptionLabel('测试使用已保存配置，绕过触发条件和冷却限制。停用的渠道不会发送。', test_card)
        test_hint.setWordWrap(True)
        test_box.addWidget(test_hint)
        test_bar = QtWidgets.QHBoxLayout()
        for text, selected in [('测试选中渠道', True), ('测试全部启用渠道', False)]:
            button = PushButton(text, test_card)
            button.clicked.connect(lambda checked=False, selected=selected: self.test_push(selected))
            test_bar.addWidget(button)
            self.buttons.append(button)
        test_bar.addStretch()
        test_box.addLayout(test_bar)
        self.results = TextEdit(test_card)
        self.results.setReadOnly(True)
        self.results.setMinimumHeight(140)
        self.results.document().setMaximumBlockCount(200)
        test_box.addWidget(self.results)
        layout.addWidget(test_card)
        layout.addStretch()
        root.addWidget(scroll, 1)
        self.message = BodyLabel(self)
        self.message.setWordWrap(True)
        root.addWidget(self.message)
        self.view = view
        self.reload()

    def set_busy(self, busy):
        self.busy = busy
        self.view.setEnabled(not busy and not self.testing)
        if busy:
            self.message.setText('查询进行中，暂不可修改通知或测试推送。')

    def refresh(self):
        channels = self.store.channels
        while self.channel_list.count():
            item = self.channel_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.selected_index is not None and self.selected_index >= len(channels):
            self.selected_index = None
        if not channels:
            self.channel_list.addWidget(BodyLabel('尚未配置通知渠道，点击“新增渠道”开始。', self))
        for index, channel in enumerate(channels):
            card = SimpleExpandGroupSettingCard(
                QtGui.QIcon(), channel.display(),
                '%s · %s' % (CHANNEL_NAMES.get(channel.kind, channel.kind),
                             '启用' if channel.enabled else '停用'), parent=self)
            expand = PushButton('展开配置', card)
            card.addWidget(expand)
            details = QtWidgets.QWidget(card.view)
            detail_box = QtWidgets.QVBoxLayout(details)
            detail_box.setContentsMargins(16, 12, 16, 12)
            if channel.kind == 'email':
                summary = 'SMTP：%s:%s\n收件人：%s\n连接：%s；授权信息：%s' % (
                    channel.smtp_host, channel.smtp_port, ', '.join(channel.mail_to),
                    'SSL' if channel.use_ssl else '非 SSL（可能明文）',
                    '已配置' if channel.smtp_password else '未配置')
            elif channel.kind == 'windows':
                summary = ('程序浮窗保持显示，直到手动关闭或程序退出。' if channel.persistent else
                           '系统通知：处理时长 %s 秒，实际停留时间由 Windows 决定。' % channel.hold_seconds)
            else:
                summary = '通知地址：%s；加签密钥：%s' % (
                    '已配置（已隐藏）' if channel.url else '未配置',
                    '已配置（已隐藏）' if channel.secret else '未配置')
            label = CaptionLabel(summary, details)
            label.setWordWrap(True)
            detail_box.addWidget(label)
            actions = QtWidgets.QHBoxLayout()
            edit = PushButton('编辑此渠道', details)
            edit.clicked.connect(lambda checked=False, index=index: self.open_editor(index))
            test = PushButton('测试此渠道', details)
            test.clicked.connect(lambda checked=False, index=index: self._test_channel(index))
            actions.addWidget(edit)
            actions.addWidget(test)
            actions.addStretch()
            detail_box.addLayout(actions)
            card.addGroupWidget(details)

            def expanded(checked=False, index=index, card=card, expand=expand):
                self.selected_index = index
                expand.setText('收起配置' if card.isExpand else '展开配置')

            # 卡片标题和箭头由库切换；文字按钮也调用同一动画 API。
            card.card.expandButton.clicked.connect(expanded)
            def toggle(checked=False, card=card, expanded=expanded):
                card.toggleExpand()
                expanded()
            expand.clicked.connect(toggle)
            self.channel_list.addWidget(card)

    def _test_channel(self, index):
        self.selected_index = index
        self.test_push(True)

    def refresh_snapshot(self):
        try:
            self.store.reload()
            self.refresh()
        except (OSError, ValueError, TypeError) as error:
            self.message.setText(str(error))

    def reload(self):
        if self.busy or self.testing:
            return
        try:
            self.store.reload()
            self.refresh()
            policy = self.store.policy()
        except (OSError, ValueError, TypeError) as error:
            self.loaded = False
            self.message.setText(str(error))
            return
        for state, control in self.triggers.items():
            control.setChecked(state in policy['alert_on'])
        for key, control in self.policy_inputs.items():
            control.setValue(policy[key])
        self.loaded = True
        self.message.setText('配置已加载。未配置渠道时不会发送通知。')

    def selected(self):
        index = self.selected_index
        if index is None:
            self.message.setText('请先展开一个通知渠道，或使用卡片内的操作按钮。')
            return None
        return index

    def perform(self, operation):
        if self.busy or self.testing:
            return False
        try:
            operation()
        except (OSError, ValueError, TypeError) as error:
            self.message.setText(redact(error, self.store.channels))
            return False
        self.refresh()
        self.message.setText('通知配置已保存。')
        self.changed.emit()
        return True

    def open_editor(self, index=None):
        if self.busy or self.testing:
            return
        channel = self.store.channels[index] if index is not None else None
        dialog = ChannelEditor(channel, self.window())
        def save(value):
            ok = self.perform(lambda: self.store.put_channel(value, index))
            if not ok:
                dialog.error.setText(self.message.text())
            return ok
        dialog.save_action = save
        exec_app(dialog)

    def add(self):
        self.open_editor()

    def edit(self):
        index = self.selected()
        if index is not None:
            self.open_editor(index)

    def toggle(self):
        index = self.selected()
        if index is not None:
            channel = self.store.channels[index]
            channel.enabled = not channel.enabled
            self.perform(lambda: self.store.put_channel(channel, index))

    def delete(self):
        if self.busy or self.testing:
            return
        index = self.selected()
        if index is None:
            return
        dialog = MessageBox('删除通知渠道', '确定删除选中的渠道？', self.window())
        dialog.yesButton.setText('删除')
        dialog.cancelButton.setText('取消')
        dialog.cancelButton.setFocus()
        if exec_app(dialog):
            self.selected_index = None
            self.perform(lambda: self.store.delete_channel(index))

    def save_policy(self):
        if not self.loaded:
            self.message.setText('请先修正配置并重新加载通知策略。')
            return
        policy = {key: control.value() for key, control in self.policy_inputs.items()}
        policy['alert_on'] = [state for state, control in self.triggers.items() if control.isChecked()]
        self.perform(lambda: self.store.save_policy(policy))

    def test_push(self, selected):
        if self.busy or self.testing:
            return
        try:
            current = self.store.path.read_bytes() if self.store.path.exists() else None
            if current != self.store.original:
                raise ValueError('配置已被其他程序修改，请重新加载后测试。')
            config = AppConfig.load(self.store.path)
            errors = config.validate_unknown_keys() + config.validate_channels()
            if errors:
                raise ValueError('；'.join(errors))
            channels = config.enabled_channels
            if selected:
                index = self.selected()
                if index is None:
                    return
                channel = config.notify[index]
                channels = [channel] if channel.enabled else []
            if not channels:
                raise ValueError('没有可测试的启用渠道。')
        except (OSError, ValueError, TypeError) as error:
            self.message.setText(redact(error, self.store.channels))
            return
        dialog = MessageBox('确认测试推送', '将真实发送一条测试消息到 %d 个启用渠道。\n使用已保存配置，不受触发条件与冷却限制。' % len(channels), self.window())
        dialog.yesButton.setText('发送测试消息')
        dialog.cancelButton.setText('取消')
        dialog.cancelButton.setFocus()
        if not exec_app(dialog):
            return
        self.testing = True
        self.view.setEnabled(False)
        self.testing_changed.emit(True)
        self._append_result('正在发送测试消息…')
        self.worker = TestPushWorker(channels, config.settings, self)
        self.worker.results.connect(self._results)
        self.worker.persistent_notification.connect(self.persistent_notification)
        self.worker.failed.connect(self._append_result)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _append_result(self, message):
        cursor = self.results.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(str(message) + '\n')
        self.results.setTextCursor(cursor)
        self.results.ensureCursorVisible()

    def _results(self, results):
        for channel, ok, detail in results:
            self._append_result('%s：%s%s' % (channel, '发送成功' if ok else '发送失败', '' if ok else '；' + detail))

    def _finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.testing = False
        self.view.setEnabled(not self.busy)
        self.message.setText('测试推送已结束。成功仅代表渠道接受消息，请确认收件端是否收到。')
        self.testing_changed.emit(False)
