"""“更改”面板：工作区和暂存区里未提交的文件（勾选框 = 是否暂存 / 文件名 / 所在目录 / 状态字母），
下面是提交说明和“提交”按钮。

勾选框：打勾 = 已暂存，半勾 = 部分暂存（暂存后又改过），空 = 没暂存；点一下暂存 / 取消暂存。
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .gitcore import FileChange
from .ui_common import section_header


def stage_state(ch: FileChange) -> Qt.CheckState:
    """暂存状态：x 是暂存区的状态，y 是工作区的状态（'.' 表示没有改动）。"""
    if ch.x in (".", "?") or ch.kind == "C":
        return Qt.CheckState.Unchecked
    return Qt.CheckState.Checked if ch.y == "." else Qt.CheckState.PartiallyChecked


def _change_item(ch: FileChange) -> QTreeWidgetItem:
    directory, _, name = ch.path.rpartition("/")
    it = QTreeWidgetItem([name, directory, ch.kind])
    it.setData(0, Qt.ItemDataRole.UserRole, ch)
    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    it.setCheckState(0, stage_state(ch))
    it.setForeground(1, QBrush(QColor(theme.TEXT_FAINT)))
    it.setForeground(2, QBrush(QColor(theme.status_color(ch.kind))))
    it.setToolTip(0, f"{ch.orig_path} → {ch.path}" if ch.orig_path else ch.path)
    return it


class _CtrlEnter(QObject):
    """提交说明框里按 Ctrl+Enter = 点“提交”。"""

    def __init__(self, target: QObject, fn: Callable[[], None]) -> None:
        super().__init__(target)
        self._fn = fn
        target.installEventFilter(self)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if (e.type() == QEvent.Type.KeyPress and isinstance(e, QKeyEvent)
                and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._fn()
            return True
        return False


class ChangesPanel(QWidget):
    fileActivated = Signal(object)      # 点了一个文件：gitcore.FileChange
    historyRequested = Signal(str)      # 右键 → 查看文件历史：文件路径
    stageRequested = Signal(list)       # 暂存这些文件：[FileChange]
    unstageRequested = Signal(list)     # 取消暂存
    discardRequested = Signal(list)     # 放弃改动
    commitRequested = Signal(str, bool)     # 提交：说明, 是否修改上一次提交（amend）
    amendToggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._busy = False
        hdr, self.title, hl = section_header("更改")
        stage_all = QToolButton()
        stage_all.setText("＋ 全部暂存")
        stage_all.setToolTip("暂存列表里的全部文件")
        stage_all.clicked.connect(lambda: self.stageRequested.emit(self.all_changes()))
        hl.addWidget(stage_all)
        unstage_all = QToolButton()
        unstage_all.setText("－ 全部取消")
        unstage_all.setToolTip("取消暂存全部文件")
        unstage_all.clicked.connect(lambda: self.unstageRequested.emit(
            [c for c in self.all_changes() if stage_state(c) != Qt.CheckState.Unchecked]))
        hl.addWidget(unstage_all)
        lay.addWidget(hdr)

        t = self.tree = QTreeWidget()
        t.setColumnCount(3)
        t.setHeaderHidden(True)
        t.setRootIsDecorated(False)
        t.setUniformRowHeights(True)
        t.setFrameShape(QFrame.Shape.NoFrame)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        h = t.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(t, 1)

        # ---- 提交说明 + 提交按钮 ----
        box = QFrame()
        box.setObjectName("commitBox")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(6, 4, 6, 6)
        bl.setSpacing(4)
        self.message = QPlainTextEdit()
        self.message.setObjectName("commitMessage")
        self.message.setPlaceholderText("提交说明（Ctrl+Enter 提交）")
        self.message.setTabChangesFocus(True)
        self.message.setFixedHeight(self.fontMetrics().lineSpacing() * 3 + 14)
        _CtrlEnter(self.message, self._commit)
        bl.addWidget(self.message)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.amend = QCheckBox("修改上一次提交")
        self.amend.setToolTip("amend：把暂存的改动并入上一次提交，并可修改它的说明（已推送的提交不要这样做）")
        self.amend.toggled.connect(self.amendToggled)
        row.addWidget(self.amend)
        row.addStretch(1)
        self.commit_btn = QPushButton("提交")
        self.commit_btn.setObjectName("commitButton")
        self.commit_btn.setToolTip("提交已暂存的改动 (Ctrl+Enter)；没有暂存任何文件时会询问是否全部暂存")
        self.commit_btn.clicked.connect(self._commit)
        row.addWidget(self.commit_btn)
        bl.addLayout(row)
        lay.addWidget(box)

        t.itemClicked.connect(self._on_item)
        t.itemActivated.connect(self._on_item)
        t.itemChanged.connect(self._on_check)
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

    def all_changes(self) -> list[FileChange]:
        items = (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        return [self._change(it) for it in items if it is not None]

    def selected_changes(self) -> list[FileChange]:
        return [self._change(it) for it in self.tree.selectedItems()]

    def set_busy(self, busy: bool) -> None:
        """有写操作在执行：先别让人再点勾选框和提交。"""
        self._busy = busy
        self.commit_btn.setEnabled(not busy)

    def focus_message(self) -> None:
        self.message.setFocus()

    def clear_message(self) -> None:
        self.message.clear()
        self.amend.setChecked(False)

    def set_message_if_empty(self, text: str) -> None:
        if not self.message.toPlainText().strip():
            self.message.setPlainText(text)

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

    def _commit(self) -> None:
        self.commitRequested.emit(self.message.toPlainText(), self.amend.isChecked())

    def _on_check(self, item: QTreeWidgetItem, col: int) -> None:
        if col != 0:
            return
        ch = self._change(item)
        if item.checkState(0) == stage_state(ch):
            return
        if self._busy:                  # 正在执行别的操作：勾选框恢复原样
            self.tree.blockSignals(True)
            item.setCheckState(0, stage_state(ch))
            self.tree.blockSignals(False)
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self.stageRequested.emit([ch])
        else:
            self.unstageRequested.emit([ch])

    def _on_item(self, item: QTreeWidgetItem | None, _col: int = 0) -> None:
        if item is not None:
            self.fileActivated.emit(self._change(item))

    def _menu(self, pos: QPoint) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        ch = self._change(it)
        sel = self.selected_changes()
        if ch not in sel:
            sel = [ch]
        n = f"（{len(sel)} 个文件）" if len(sel) > 1 else ""
        menu = QMenu(self)
        a_stage = menu.addAction("暂存" + n)
        a_stage.setEnabled(any(stage_state(c) != Qt.CheckState.Checked for c in sel))
        a_unstage = menu.addAction("取消暂存" + n)
        a_unstage.setEnabled(any(stage_state(c) != Qt.CheckState.Unchecked for c in sel))
        a_discard = menu.addAction("放弃改动…" + n)
        menu.addSeparator()
        a_hist = menu.addAction("查看文件历史")
        a_hist.setEnabled(ch.kind != "U")             # 未跟踪文件还没有历史
        a_copy = menu.addAction("复制路径")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == a_stage:
            self.stageRequested.emit(sel)
        elif chosen == a_unstage:
            self.unstageRequested.emit(sel)
        elif chosen == a_discard:
            self.discardRequested.emit(sel)
        elif chosen == a_hist:
            self.historyRequested.emit(ch.path)
        elif chosen == a_copy:
            QGuiApplication.clipboard().setText(ch.path)
