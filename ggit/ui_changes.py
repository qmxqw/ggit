"""“更改”面板：工作区和暂存区里未提交的文件（文件名 / 所在目录 / 状态字母）。"""
from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .gitcore import FileChange
from .ui_common import section_header


def _change_item(ch: FileChange) -> QTreeWidgetItem:
    directory, _, name = ch.path.rpartition("/")
    it = QTreeWidgetItem([name, directory, ch.kind])
    it.setData(0, Qt.ItemDataRole.UserRole, ch)
    it.setForeground(1, QBrush(QColor(theme.TEXT_FAINT)))
    it.setForeground(2, QBrush(QColor(theme.status_color(ch.kind))))
    it.setToolTip(0, f"{ch.orig_path} → {ch.path}" if ch.orig_path else ch.path)
    return it


class ChangesPanel(QWidget):
    fileActivated = Signal(object)      # 点了一个文件：gitcore.FileChange
    historyRequested = Signal(str)      # 右键 → 查看文件历史：文件路径

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        hdr, self.title, _ = section_header("更改")
        lay.addWidget(hdr)

        t = self.tree = QTreeWidget()
        t.setColumnCount(3)
        t.setHeaderHidden(True)
        t.setRootIsDecorated(False)
        t.setUniformRowHeights(True)
        t.setFrameShape(QFrame.Shape.NoFrame)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        h = t.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(t)

        t.itemClicked.connect(self._on_item)
        t.itemActivated.connect(self._on_item)
        t.customContextMenuRequested.connect(self._menu)

    # ---------------------------------------------------------------- 对外接口
    def set_changes(self, changes: list[FileChange], total: int) -> None:
        """重建列表，保持原来选中的文件。total：改动文件的真实总数（可能比列出来的多）。"""
        keep = self.current_path()
        sel = None
        self.tree.blockSignals(True)
        self.tree.clear()
        for ch in changes:
            it = _change_item(ch)
            self.tree.addTopLevelItem(it)
            if ch.path == keep:
                sel = it
        if sel is not None:
            self.tree.setCurrentItem(sel)
        self.tree.blockSignals(False)
        if total > len(changes):
            self.title.setText(f"更改  ({total}，只列出前 {len(changes)} 个)")
            self.title.setToolTip("改动的文件太多，列表只显示按路径排序后的前一部分。")
        else:
            self.title.setText(f"更改  ({len(changes)})" if changes else "更改")
            self.title.setToolTip("")

    def paths(self) -> list[str]:
        items = (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        return [self._change(it).path for it in items if it is not None]

    def current_path(self) -> str | None:
        it = self.tree.currentItem()
        return self._change(it).path if it is not None else None

    def clear_selection(self) -> None:
        self.tree.blockSignals(True)
        self.tree.setCurrentIndex(QModelIndex())
        self.tree.clearSelection()
        self.tree.blockSignals(False)

    def refresh_view(self) -> None:
        self.tree.viewport().update()

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _change(item: QTreeWidgetItem) -> FileChange:
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _on_item(self, item: QTreeWidgetItem | None, _col: int = 0) -> None:
        if item is not None:
            self.fileActivated.emit(self._change(item))

    def _menu(self, pos: QPoint) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        ch = self._change(it)
        menu = QMenu(self)
        a_hist = menu.addAction("查看文件历史")
        a_hist.setEnabled(ch.kind != "U")             # 未跟踪文件还没有历史
        a_copy = menu.addAction("复制路径")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == a_hist:
            self.historyRequested.emit(ch.path)
        elif chosen == a_copy:
            QGuiApplication.clipboard().setText(ch.path)
