"""目标管理页。每次操作立即保存，不修改查询历史。"""

from __future__ import annotations

from qt_compat import NO_EDIT_TRIGGERS, QtWidgets, Signal, exec_app
from target_config import TargetConfig
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            ComboBox, LineEdit, MessageBox, MessageBoxBase,
                            PrimaryPushButton, PushButton, SubtitleLabel,
                            TableWidget)


class TargetEditor(MessageBoxBase):
    def __init__(self, target=None, parent=None):
        super().__init__(parent)
        self.save_action = None
        self.viewLayout.addWidget(SubtitleLabel('编辑目标' if target else '新增目标', self.widget))
        hint = CaptionLabel('填写完整船名或码头航次，保存时自动检查重复目标。', self.widget)
        hint.setWordWrap(True)
        self.viewLayout.addWidget(hint)
        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(16)
        form.setHorizontalSpacing(20)
        self.viewLayout.addLayout(form)
        self.kind = ComboBox(self.widget)
        self.kind.addItem('船名', userData='ship')
        self.kind.addItem('码头航次', userData='voyage')
        self.value = LineEdit(self.widget)
        self.value.setPlaceholderText('例如 MSC IRINA / GJ634W')
        self.label = LineEdit(self.widget)
        self.label.setPlaceholderText('留空则使用目标名称')
        self.enabled = CheckBox('启用监控', self.widget)
        self.enabled.setChecked(True)
        if target:
            self.kind.setCurrentIndex(self.kind.findData(target.type))
            self.value.setText(target.value)
            self.label.setText(target.label)
            self.enabled.setChecked(target.enabled)
        form.addRow(BodyLabel('目标类型', self.widget), self.kind)
        form.addRow(BodyLabel('船名 / 码头航次', self.widget), self.value)
        form.addRow(BodyLabel('显示名称（可选）', self.widget), self.label)
        form.addRow(self.enabled)
        self.error = BodyLabel(self.widget)
        self.error.setWordWrap(True)
        form.addRow(self.error)
        self.yesButton.setText('保存')
        self.cancelButton.setText('取消')
        self.widget.setMinimumWidth(520)
        self.value.setFocus()

    def validate(self):
        # MessageBoxBase 只在保存成功时关闭；去重或写盘错误留在弹窗内。
        return self.save_action is not None and self.save_action()


class TargetsPage(QtWidgets.QWidget):
    changed = Signal()

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.store = TargetConfig(path)
        self.busy = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel('目标管理', self))
        hint = BodyLabel('修改后立即保存。删除目标不会删除历史查询记录。', self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.buttons = []
        for text, callback in [('新增', self.add), ('编辑', self.edit),
                               ('启用 / 停用', self.toggle), ('删除', self.delete),
                               ('重新加载', self.reload)]:
            button = (PrimaryPushButton if text == '新增' else PushButton)(text, self)
            button.setMinimumHeight(34)
            button.clicked.connect(callback)
            row.addWidget(button)
            self.buttons.append(button)
        row.addStretch()
        layout.addLayout(row)
        card = CardWidget(self)
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        self.table = TableWidget(card)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(['类型', '船名 / 码头航次', '显示名称', '状态'])
        self.table.setEditTriggers(NO_EDIT_TRIGGERS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setBorderVisible(False)
        self.table.setBorderRadius(8)
        card_layout.addWidget(self.table)
        layout.addWidget(card, 1)
        self.message = BodyLabel(self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.refresh()

    def set_busy(self, busy):
        self.busy = busy
        for button in self.buttons:
            button.setEnabled(not busy)
        self.message.setText('查询进行中，暂不可修改目标。' if busy else '')

    def refresh(self):
        self.table.setRowCount(len(self.store.targets))
        for index, target in enumerate(self.store.targets):
            for column, value in enumerate([
                    {'ship': '船名', 'voyage': '码头航次'}.get(target.type, target.type),
                    target.value, target.label, '启用' if target.enabled else '停用']):
                self.table.setItem(index, column, QtWidgets.QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def selected(self):
        index = self.table.currentRow()
        if index < 0:
            self.message.setText('请先选择一个目标。')
            return None
        return index

    def perform(self, operation):
        if self.busy:
            return False
        try:
            operation()
        except (OSError, ValueError) as error:
            self.message.setText(str(error))
            return False
        self.message.setText('配置已保存。')
        self.refresh()
        self.changed.emit()
        return True

    def reload(self):
        if self.perform(self.store.reload):
            self.message.setText('配置已重新加载。')

    def open_editor(self, index=None):
        if self.busy:
            return
        target = self.store.targets[index] if index is not None else None
        dialog = TargetEditor(target, self.window())
        def save():
            if self.perform(lambda: self.store.put(
                    dialog.kind.currentData(), dialog.value.text(), dialog.label.text(),
                    dialog.enabled.isChecked(), index)):
                return True
            else:
                dialog.error.setText(self.message.text())
                return False
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
            target = self.store.targets[index]
            self.perform(lambda: self.store.put(target.type, target.value, target.label,
                                                 not target.enabled, index))

    def delete(self):
        if self.busy:
            return
        index = self.selected()
        if index is None:
            return
        dialog = MessageBox('删除目标', '确定删除“%s”？历史记录将保留。' %
                            self.store.targets[index].display(), self.window())
        dialog.yesButton.setText('删除')
        dialog.cancelButton.setText('取消')
        dialog.cancelButton.setFocus()
        if exec_app(dialog):
            self.perform(lambda: self.store.delete(index))
