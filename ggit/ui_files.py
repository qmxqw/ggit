"""file 模式的界面：左侧文件树（以项目根目录为起点），右侧文本 / 代码预览（可同时打开多个）。"""
from __future__ import annotations

import os

from PySide6.QtCore import QDir, QModelIndex, QObject, QPersistentModelIndex, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .ui_diff import MAX_ROWS, Blk, CodeView
from .ui_docs import DocView


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


# ---------------------------------------------------------------- 文本 / 代码预览
class FileDocument(DocView):
    """一个标签页里的文件预览：标题栏（路径、行数、大小、编码）+ 带行号的只读文本。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fc = None
        self._msg = ""
        self._dirty = True
        self._keep = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("fileBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 4, 8, 4)
        self.title = QLabel()
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info = QLabel()
        self.info.setObjectName("fileInfo")
        bl.addWidget(self.title, 1)
        bl.addWidget(self.info)
        lay.addWidget(bar)

        self.stack = QStackedWidget()
        self.msg_label = QLabel()
        self.msg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.msg_label.setWordWrap(True)
        self.msg_label.setObjectName("placeholder")
        self.view = CodeView(1)
        self.stack.addWidget(self.msg_label)    # 0
        self.stack.addWidget(self.view)         # 1
        lay.addWidget(self.stack, 1)

    # ---- 与 DiffDocument 统一的接口 ----
    def set_title(self, text: str) -> None:
        self.title.setText(text)
        self.title.setToolTip(text)

    def set_message(self, text: str) -> None:
        self._fc = None
        self._msg = text
        self.info.setText("")
        self._render(False)

    def show_result(self, fc, keep_scroll: bool = False) -> None:
        if fc.message or fc.is_binary:
            if fc.size:
                self.info.setText(_fmt_size(fc.size))
            self._fc = None
            self._msg = fc.message or "二进制文件，不预览。"
            self._render(False)
            return
        self._fc = fc
        shown = min(len(fc.lines), MAX_ROWS)
        parts = [f"{len(fc.lines)} 行", _fmt_size(fc.size), fc.encoding]
        if fc.truncated:
            parts.append("文件较大，只显示开头")
        elif shown < len(fc.lines):
            parts.append(f"只显示前 {shown} 行")
        self.info.setText(" · ".join(p for p in parts if p))
        self._render(keep_scroll)

    @staticmethod
    def result_signature(fc):
        return (fc.message, fc.is_binary, fc.size, fc.encoding, len(fc.lines), hash(tuple(fc.lines)))

    def set_mode(self, mode: str) -> None:
        pass

    def apply_font_size(self, size: int) -> None:
        self.view.apply_ui_size(size)
        if self._fc is not None:
            self._render(True)

    def goto_change(self, forward: bool = True) -> None:
        pass

    # ---- 内部 ----
    def _render(self, keep_scroll: bool) -> None:
        if not self.isVisible():                # 没显示出来的标签页先不渲染，切过去时再画
            self._dirty = True
            self._keep = keep_scroll
            return
        self._dirty = False
        if self._fc is None:
            self.msg_label.setText(self._msg)
            self.stack.setCurrentIndex(0)
            return
        bar = self.view.verticalScrollBar()
        saved = bar.value() if keep_scroll else 0
        lines = self._fc.lines
        blocks = [Blk(t, None, None, (str(i + 1),), False) for i, t in enumerate(lines[:MAX_ROWS])]
        if len(lines) > MAX_ROWS:
            blocks.append(Blk(f"… 已省略后面 {len(lines) - MAX_ROWS} 行", None, None, ("",), False))
        self.view.set_blocks(blocks)
        self.stack.setCurrentIndex(1)
        if keep_scroll and saved:
            QTimer.singleShot(0, lambda: bar.setValue(saved))

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self._render(self._keep)


# ---------------------------------------------------------------- 文件树
class _HideGitFilter(QSortFilterProxyModel):
    """文件树里不显示 .git 目录（其它以点开头的文件，比如 .gitignore，照常显示）。"""

    def __init__(self, fs: QFileSystemModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fs = fs
        self.setSourceModel(fs)

    def filterAcceptsRow(self, row: int, parent: QModelIndex | QPersistentModelIndex) -> bool:
        return self._fs.fileName(self._fs.index(row, 0, parent)) != ".git"


class FileTreePanel(QWidget):
    fileActivated = Signal(str)         # 点击 / 回车选中了一个文件：它的绝对路径

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.root = os.path.normpath(root)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        hdr = QFrame()
        hdr.setObjectName("fileTreeHeader")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 6, 8, 6)
        title = QLabel(f"文件  ·  {os.path.basename(self.root) or self.root}")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        title.setObjectName("sectionTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setToolTip(self.root)
        hl.addWidget(title, 1)
        lay.addWidget(hdr)

        self.fs = QFileSystemModel(self)
        self.fs.setReadOnly(True)
        self.fs.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        self.fs.setRootPath(self.root)
        self.proxy = _HideGitFilter(self.fs, self)

        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.fs.index(self.root)))
        for col in (1, 2, 3):                   # 只留“名称”列
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lay.addWidget(self.tree, 1)

        self.tree.clicked.connect(self._on_clicked)
        self.tree.activated.connect(self._on_activated)
        self.tree.customContextMenuRequested.connect(self._menu)

    def _path_of(self, idx) -> str:
        return os.path.normpath(self.fs.filePath(self.proxy.mapToSource(idx)))

    def _is_dir(self, idx) -> bool:
        return self.fs.isDir(self.proxy.mapToSource(idx))

    def _on_clicked(self, idx):
        if self._is_dir(idx):                   # 点目录：展开 / 收起
            self.tree.setExpanded(idx, not self.tree.isExpanded(idx))
        else:                                   # 点文件：打开预览
            self.fileActivated.emit(self._path_of(idx))

    def _on_activated(self, idx):               # 回车 / 双击
        if not self._is_dir(idx):
            self.fileActivated.emit(self._path_of(idx))

    def _menu(self, pos):
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        path = self._path_of(idx)
        menu = QMenu(self)
        a_open = menu.addAction("打开预览")
        a_open.setEnabled(not self._is_dir(idx))
        a_rel = menu.addAction("复制相对路径")
        a_abs = menu.addAction("复制完整路径")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == a_open:
            self.fileActivated.emit(path)
        elif chosen == a_rel:
            QGuiApplication.clipboard().setText(os.path.relpath(path, self.root).replace("\\", "/"))
        elif chosen == a_abs:
            QGuiApplication.clipboard().setText(path)
