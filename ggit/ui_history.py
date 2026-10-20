"""“文件历史”面板：某个文件的提交历史（追踪重命名），点一条在右侧打开那次提交对它的修改。

和提交图共用左下方的位置（二选一）；Esc 或“← 返回图形”回到提交图。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from . import gitcore, theme
from .async_runner import AsyncRunner
from .gitcore import HistoryEntry
from .ui_common import section_header
from .utils import fmt_time


class HistoryPanel(QWidget):
    entryActivated = Signal(object)     # 点了一条历史：gitcore.HistoryEntry
    closeRequested = Signal()           # Esc / “← 返回图形”

    def __init__(self, runner: AsyncRunner, repo: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runner = runner
        self._repo = repo
        self._path: str | None = None
        self._gen = 0
        self._task: int | None = None
        self._alive = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        hdr, self.title, hl = section_header("文件历史")
        back = QPushButton("← 返回图形")
        back.setToolTip("Esc")
        hl.addWidget(back)
        lay.addWidget(hdr)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        lay.addWidget(self.list)

        back.clicked.connect(self.closeRequested)
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.closeRequested)
        self.list.itemClicked.connect(self._on_item)
        self.list.itemActivated.connect(self._on_item)

    # ---------------------------------------------------------------- 对外接口
    @property
    def path(self) -> str | None:
        """正在显示哪个文件的历史；没有显示则为 None。"""
        return self._path

    def load(self, path: str, quiet: bool = False) -> None:
        """后台读取 path 的历史。quiet=True（自动刷新）：不显示“加载中”，读完后保持原来选中的那一条。"""
        reselect = self._selected_oid() if quiet else None
        self.cancel()
        self._path = path
        self._gen += 1
        gen = self._gen
        self.title.setText(f"文件历史：{path}")
        if not quiet:
            self._show_text("加载中…")
        repo = self._repo
        self._task = self._runner.submit(
            lambda: gitcore.get_file_history(repo, path),
            lambda es: self._on_loaded(gen, es, reselect),
            lambda e: self._on_error(gen, e),
        )

    def stop(self) -> None:
        """不再显示历史（回到提交图时调用）。"""
        self.cancel()
        self._path = None

    def cancel(self) -> None:
        if self._task is not None:
            self._runner.cancel(self._task)
            self._task = None

    def entries(self) -> list[HistoryEntry]:
        items = (self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count()))
        return [e for e in items if isinstance(e, HistoryEntry)]

    def clear_selection(self) -> None:
        self.list.blockSignals(True)
        self.list.setCurrentRow(-1)
        self.list.clearSelection()
        self.list.blockSignals(False)

    def dispose(self) -> None:
        self._alive = False
        self.cancel()

    # ---------------------------------------------------------------- 内部
    def _selected_oid(self) -> str | None:
        it = self.list.currentItem()
        e = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        return e.commit.oid if isinstance(e, HistoryEntry) else None

    def _show_text(self, text: str) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItem(text)
        self.list.blockSignals(False)

    def _on_loaded(self, gen: int, entries: list[HistoryEntry], reselect: str | None) -> None:
        if not self._alive or gen != self._gen:
            return
        self._task = None
        if not entries:
            self._show_text("没有历史记录（文件可能还没有提交过）。")
            return
        self.list.blockSignals(True)
        self.list.clear()
        target = None
        for e in entries:
            c = e.commit
            text = f"{c.subject}\n{c.short} · {c.author} · {fmt_time(c.time)}"
            if e.old_path:
                text += f"\n重命名自 {e.old_path}"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, e)
            if e.status == "D":
                it.setForeground(QBrush(QColor(theme.DELETED)))
            self.list.addItem(it)
            if reselect and c.oid == reselect:
                target = it
        if target is not None:
            self.list.setCurrentItem(target)
        self.list.blockSignals(False)

    def _on_error(self, gen: int, err: BaseException) -> None:
        if not self._alive or gen != self._gen:
            return
        self._task = None
        self._show_text(f"出错了：{err}")

    def _on_item(self, item: QListWidgetItem | None) -> None:
        e = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(e, HistoryEntry):
            self.entryActivated.emit(e)
