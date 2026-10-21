"""快速选择对话框：输入关键字过滤，上下键选择，回车确认。
用于 Ctrl+P（切换项目）和 Ctrl+F（搜索文件、查看文件历史）。

  * 多个关键字用空格分开，不区分大小写，每个关键字都要出现
  * 最多列出 MAX_SHOWN 条；匹配更多时在下方提示总数，提醒再输入一些关键字
  * 继续往后输入时只在上一次的匹配结果里找，文件很多时也不卡
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from . import theme


class PickerDialog(QDialog):
    MAX_SHOWN = 300     # 只显示前这么多条匹配项

    def __init__(self, title: str, items: list[tuple[str, Any]], parent: QWidget | None = None,
                 placeholder: str = "输入关键字过滤（多个词用空格分开）…") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(660, 440)
        self._items = items                                  # [(显示文字, 返回值)]
        self._lower = [display.lower() for display, _ in items]
        self._last_text: str | None = None
        self._matches: list[int] = []                        # 上一次过滤结果（items 里的下标）
        self.value: Any = None

        lay = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setFont(theme.with_icon_font(self.list.font()))     # 分支前面的云朵图标
        lay.addWidget(self.list, 1)
        self.hint = QLabel()
        self.hint.setObjectName("pickerHint")
        self.hint.setVisible(False)
        lay.addWidget(self.hint)

        self.edit.textChanged.connect(self._refilter)
        self.edit.installEventFilter(self)
        self.list.itemActivated.connect(self._accept_item)     # 双击 / 回车
        self._refilter("")
        self.edit.setFocus()

    @property
    def match_count(self) -> int:
        """当前关键字匹配的总条数（可能比列出来的多）。"""
        return len(self._matches)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.edit and isinstance(ev, QKeyEvent) and ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.list.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self._accept_item(item)
                return True
        return super().eventFilter(obj, ev)

    def _refilter(self, text: str) -> None:
        tokens = text.lower().split()
        # 在原来的文字后面接着输入：新的匹配一定在上一次的匹配里，只需在其中继续筛
        if self._last_text is not None and text.startswith(self._last_text):
            candidates = self._matches
        else:
            candidates = list(range(len(self._items)))
        self._matches = [i for i in candidates if all(t in self._lower[i] for t in tokens)]
        self._last_text = text

        self.list.clear()
        for i in self._matches[:self.MAX_SHOWN]:
            display, value = self._items[i]
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

        total = len(self._matches)
        if total > self.MAX_SHOWN:
            self.hint.setText(f"共 {total} 条匹配，只列出前 {self.MAX_SHOWN} 条；再输入一些关键字可以缩小范围。")
        elif total == 0 and self._items:
            self.hint.setText("没有匹配的结果。")
        self.hint.setVisible(total > self.MAX_SHOWN or (total == 0 and bool(self._items)))

    def _accept_item(self, item: QListWidgetItem) -> None:
        self.value = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
