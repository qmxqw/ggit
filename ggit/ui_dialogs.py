"""快速选择对话框：输入关键字过滤，上下键选择，回车确认。
用于 Ctrl+P（切换项目）和 Ctrl+F（搜索文件、查看文件历史）。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QDialog, QLineEdit, QListWidget, QListWidgetItem,
                               QVBoxLayout)


class PickerDialog(QDialog):
    MAX_SHOWN = 300     # 只显示前这么多条匹配项，文件很多时也不卡

    def __init__(self, title: str, items: list, parent=None,
                 placeholder: str = "输入关键字过滤（多个词用空格分开）…"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(660, 440)
        self._items = items              # [(显示文字, 返回值)]
        self.value = None

        lay = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        self.edit.textChanged.connect(self._refilter)
        self.edit.installEventFilter(self)
        self.list.itemActivated.connect(self._accept_item)     # 双击 / 回车
        self._refilter("")
        self.edit.setFocus()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.list.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
                row = max(0, min(self.list.count() - 1, row))
                self.list.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self._accept_item(item)
                return True
        return super().eventFilter(obj, ev)

    def _refilter(self, text: str) -> None:
        tokens = text.lower().split()
        self.list.clear()
        shown = 0
        for display, value in self._items:
            low = display.lower()
            if all(t in low for t in tokens):
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, value)
                self.list.addItem(item)
                shown += 1
                if shown >= self.MAX_SHOWN:
                    break
        if self.list.count():
            self.list.setCurrentRow(0)

    def _accept_item(self, item) -> None:
        self.value = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
