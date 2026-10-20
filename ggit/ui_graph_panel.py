"""“图形”面板：提交图。

  * 右上角的下拉框选显示范围：全部分支 / 当前分支 / 只看某个分支（从分支列表里选）
  * 每次读 LOG_PAGE 个提交，滚到底部自动接着读
  * 点提交行就地展开它修改的文件，再点一次收起；点文件行请求打开它的 diff
"""
from __future__ import annotations

import html
import time

from PySide6.QtCore import QModelIndex, QPoint, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QApplication, QComboBox, QMenu, QVBoxLayout, QWidget

from . import gitcore, theme
from .async_runner import AsyncRunner
from .gitcore import Commit, CommitFile, Snapshot
from .graph import GraphLayout
from .items import FileRow, Row
from .ui_common import colored, commit_info_html, section_header
from .ui_graph import CommitModel, GraphView
from .utils import LruCache

FILES_CACHE_MAX = 300           # 最多缓存这么多个提交的文件列表（提交不可变，缓存永远有效）
_ONLY_INDEX = 2                 # 下拉框里“只看：某分支”那一项的位置


class GraphPanel(QWidget):
    commitSelected = Signal(object)             # 选中了提交行或它下面的文件行：gitcore.Commit
    fileOpenRequested = Signal(object, object)  # 要打开提交里某个文件的 diff：(Commit, CommitFile)
    historyRequested = Signal(str)              # 右键 → 查看文件历史：文件路径
    infoRequested = Signal(str)                 # 要在说明栏显示的话（HTML）
    filesLoaded = Signal(object)                # 某个提交的文件列表读完了：gitcore.Commit
    loadFailed = Signal(str)                    # 读取提交图失败：错误信息
    scopeChanged = Signal(str)                  # 显示范围变了：SCOPE_ALL / SCOPE_AUTO / "ref:完整引用名"

    def __init__(self, runner: AsyncRunner, repo: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runner = runner
        self._repo = repo
        self._alive = True
        self._snap: Snapshot | None = None
        self._fp: str | None = None             # 引用指纹：没变就不重新加载提交图
        self._scope = gitcore.SCOPE_ALL         # 默认显示所有本地 / 远端分支和标签
        self._scope_ref: str | None = None      # “只看某个分支”时的完整引用名
        self._layout = GraphLayout()
        self._gen = 0                           # 加载序号：只接受最新一次的结果
        self._task: int | None = None
        self._loading = False
        self._files: LruCache[str, list[CommitFile]] = LruCache(FILES_CACHE_MAX)
        self._expanded: set[str] = set()        # 展开着的提交
        self._files_loading: set[str] = set()
        self._sel: tuple[str, str | None] | None = None    # 选中的 (提交 id, 文件路径或 None)
        self._last_toggle = ("", 0.0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        hdr, _, hl = section_header("图形")
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["全部分支", "当前分支"])
        self.scope_combo.setToolTip("全部分支：所有本地分支、远端分支和标签；当前分支：当前分支及其远端对应分支；\n"
                                    "也可以点左上角的分支名，选一个分支只看它的历史")
        hl.addWidget(self.scope_combo)
        lay.addWidget(hdr)
        self.model = CommitModel(self)
        self.view = GraphView()
        self.view.setModel(self.model)
        lay.addWidget(self.view)

        self.view.selectionModel().currentChanged.connect(self._on_current)
        self.view.clicked.connect(self._on_clicked)
        self.view.expandRequested.connect(self._on_expand_requested)
        self.view.customContextMenuRequested.connect(self._menu)
        self.model.moreRequested.connect(self.load_more)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_index)

    # ================================================================ 对外接口
    @property
    def scope(self) -> str:
        return self._scope

    def set_snapshot(self, snap: Snapshot, force: bool = False) -> bool:
        """项目状态刷新了。引用有变化（或 force）就重新加载提交图；返回是否重新加载了。"""
        first = self._snap is None
        self._snap = snap
        self.model.head_oid = snap.status.head_oid
        self.view.viewport().update()
        if self._scope.startswith("ref:") and self._scope[4:] not in snap.refs:
            # “只看”的那个分支已经被删掉了：回到全部分支，免得提交图一直报错
            gone = self._scope_ref or ""
            self._scope_ref = None
            self.scope_combo.blockSignals(True)
            self.scope_combo.setCurrentIndex(0)
            if self.scope_combo.count() > _ONLY_INDEX:
                self.scope_combo.removeItem(_ONLY_INDEX)
            self.scope_combo.blockSignals(False)
            self._set_scope(gitcore.SCOPE_ALL)
            self.infoRequested.emit(colored(f"分支 {html.escape(gone)} 已不存在，已切回“全部分支”。", theme.TEXT_DIM))
            force = True
        if force or first or snap.fingerprint != self._fp:
            self._fp = snap.fingerprint
            self._reload()
            return True
        return False

    def choose_branch(self, value: str) -> None:
        """从分支列表里选了一项：gitcore.SCOPE_ALL，或要单独查看的分支的完整引用名。"""
        if value == gitcore.SCOPE_ALL:
            self.scope_combo.setCurrentIndex(0)
            return
        self._scope_ref = value
        short = value.replace("refs/heads/", "", 1).replace("refs/remotes/", "", 1)
        label = f"只看：{short}"
        if self.scope_combo.count() <= _ONLY_INDEX:
            self.scope_combo.addItem(label)
        else:
            self.scope_combo.setItemText(_ONLY_INDEX, label)
        if self.scope_combo.currentIndex() == _ONLY_INDEX:
            self._on_scope_index(_ONLY_INDEX)          # 已经在“只看”：换了一个分支，手动重新加载
        else:
            self.scope_combo.setCurrentIndex(_ONLY_INDEX)

    def commit_count(self) -> int:
        """已经加载的提交数（不含展开出来的文件行）。"""
        return sum(1 for it in self.model.list.items if isinstance(it, Row))

    @property
    def loading(self) -> bool:
        return self._loading

    @property
    def has_more(self) -> bool:
        return self.model.more

    def load_more(self) -> None:
        """接着读下一页提交（滚到底部时自动调用）。"""
        if self._loading or self._snap is None or not self.model.more:
            return
        self._cancel_task()
        self._loading = True
        gen = self._gen
        snap, repo, scope, skip = self._snap, self._repo, self._scope, self.commit_count()
        self._task = self._runner.submit(
            lambda: gitcore.get_log(repo, snap, scope, skip, gitcore.LOG_PAGE),
            lambda r: self._on_log(gen, r, False),
            lambda e: self._on_log_error(gen, e),
        )

    def expand(self, oid: str) -> None:
        """展开一个提交（文件列表没读过就先在后台读）。"""
        row = self.model.row_at(self.model.index_of(oid).row())
        if isinstance(row, Row) and not row.expanded:
            self._expand(row)

    def collapse(self, oid: str) -> None:
        row = self.model.row_at(self.model.index_of(oid).row())
        if isinstance(row, Row) and row.expanded:
            self._collapse(row)

    def is_expanded(self, oid: str) -> bool:
        return oid in self._expanded

    def selected_commit_oid(self) -> str | None:
        return self._sel[0] if self._sel else None

    def clear_selection(self) -> None:
        self._sel = None
        sm = self.view.selectionModel()
        sm.blockSignals(True)
        self.view.setCurrentIndex(QModelIndex())
        self.view.clearSelection()
        sm.blockSignals(False)
        self.view.viewport().update()

    def apply_font_size(self) -> None:
        """全局字号变了：按新的行高重新排版。"""
        self.model.layoutChanged.emit()
        self.view.viewport().update()

    def dispose(self) -> None:
        self._alive = False
        self._cancel_task()

    # ================================================================ 加载提交图
    def _set_scope(self, scope: str) -> None:
        self._scope = scope
        self.scopeChanged.emit(scope)

    def _on_scope_index(self, idx: int) -> None:
        if idx == 0:
            self._set_scope(gitcore.SCOPE_ALL)
        elif idx == 1:
            self._set_scope(gitcore.SCOPE_AUTO)
        else:
            self._set_scope("ref:" + (self._scope_ref or ""))
        if self._snap is not None:
            self._reload()

    def _cancel_task(self) -> None:
        if self._task is not None:
            self._runner.cancel(self._task)
            self._task = None

    def _reload(self) -> None:
        snap = self._snap
        if snap is None:
            return
        self._cancel_task()
        self._gen += 1
        gen = self._gen
        self._loading = True
        repo, scope = self._repo, self._scope
        self._task = self._runner.submit(
            lambda: gitcore.get_log(repo, snap, scope, 0, gitcore.LOG_PAGE),
            lambda r: self._on_log(gen, r, True),
            lambda e: self._on_log_error(gen, e),
        )

    def _expanded_files(self) -> dict[str, list[CommitFile]]:
        out = {}
        for oid in self._expanded:
            files = self._files.peek(oid)
            if files is not None:
                out[oid] = files
        return out

    def _on_log(self, gen: int, commits: list[Commit], reset: bool) -> None:
        if not self._alive or gen != self._gen:
            return
        self._task = None
        self._loading = False
        more = len(commits) >= gitcore.LOG_PAGE
        if not reset:
            rows = [Row(c, self._layout.add(c.oid, c.parents)) for c in commits]
            self.model.append(rows, more, self._expanded_files())
            return
        scroll = self.view.verticalScrollBar().value()
        self._layout = GraphLayout()
        rows = [Row(c, self._layout.add(c.oid, c.parents)) for c in commits]
        self.model.reset(rows, self._snap.status.head_oid if self._snap else "", more, self._expanded_files())
        if self._sel:                           # 刷新后选回原来选中的提交 / 文件
            oid, path = self._sel
            idx = self.model.file_index(oid, path) if path else QModelIndex()
            if not idx.isValid():
                idx = self.model.index_of(oid)
            if idx.isValid():
                sm = self.view.selectionModel()
                sm.blockSignals(True)
                self.view.setCurrentIndex(idx)
                sm.blockSignals(False)
                self.view.viewport().update()
        QTimer.singleShot(0, lambda: self.view.verticalScrollBar().setValue(scroll))

    def _on_log_error(self, gen: int, err: BaseException) -> None:
        if not self._alive or gen != self._gen:
            return
        self._task = None
        self._loading = False
        self.model.more = False
        self.loadFailed.emit(str(err))

    # ================================================================ 选中 / 点击 / 右键
    def _on_current(self, cur: QModelIndex, _prev: QModelIndex) -> None:
        item = self.model.row_at(cur.row()) if cur.isValid() else None
        if isinstance(item, FileRow):
            self._sel = (item.parent.commit.oid, item.file.path)
            self.commitSelected.emit(item.parent.commit)
        elif isinstance(item, Row):
            self._sel = (item.commit.oid, None)
            self.commitSelected.emit(item.commit)

    def _on_clicked(self, index: QModelIndex) -> None:
        item = self.model.row_at(index.row())
        if isinstance(item, FileRow):           # 点文件：打开它的 diff（已经开着就切过去）
            self.fileOpenRequested.emit(item.parent.commit, item.file)
            return
        if not isinstance(item, Row):
            return
        oid, now = item.commit.oid, time.monotonic()
        last_oid, last_t = self._last_toggle
        if oid == last_oid and now - last_t < QApplication.doubleClickInterval() / 1000.0:
            return                              # 双击的第二下：不要再切换一次
        self._last_toggle = (oid, now)
        self._toggle(item, 0)

    def _on_expand_requested(self, index: QModelIndex, mode: int) -> None:
        item = self.model.row_at(index.row())
        if isinstance(item, FileRow):
            if mode == -1:                      # 在文件上按 ←：收起所属提交并回到提交行
                self._collapse(item.parent, select_commit=True)
            elif mode == 0:                     # 回车：打开这个文件
                self.fileOpenRequested.emit(item.parent.commit, item.file)
        elif isinstance(item, Row):
            self._toggle(item, mode)

    def _menu(self, pos: QPoint) -> None:
        idx = self.view.indexAt(pos)
        item = self.model.row_at(idx.row()) if idx.isValid() else None
        if item is None:
            return
        menu = QMenu(self)
        clipboard = QGuiApplication.clipboard()
        if isinstance(item, FileRow):
            a_hist = menu.addAction("查看文件历史")
            a_copy = menu.addAction("复制路径")
            chosen = menu.exec(self.view.viewport().mapToGlobal(pos))
            if chosen == a_hist:
                self.historyRequested.emit(item.file.path)
            elif chosen == a_copy:
                clipboard.setText(item.file.path)
        else:
            a_id = menu.addAction("复制提交 ID")
            a_subject = menu.addAction("复制提交说明")
            chosen = menu.exec(self.view.viewport().mapToGlobal(pos))
            if chosen == a_id:
                clipboard.setText(item.commit.oid)
            elif chosen == a_subject:
                clipboard.setText(item.commit.subject)

    # ================================================================ 展开 / 收起提交
    def _toggle(self, row: Row, mode: int) -> None:
        """mode：1 展开，-1 收起，0 切换。"""
        want = (not row.expanded) if mode == 0 else (mode == 1)
        if want == row.expanded:
            return
        if want:
            self._expand(row)
        else:
            self._collapse(row)

    def _expand(self, row: Row) -> None:
        oid = row.commit.oid
        files = self._files.get(oid)
        if files is not None:
            self._apply_expand(row.commit, files)
            return
        if oid in self._files_loading:
            return
        self._files_loading.add(oid)
        self.infoRequested.emit("加载文件列表…")
        repo, commit = self._repo, row.commit
        self._runner.submit(lambda: gitcore.get_commit_files(repo, oid),
                            lambda fs: self._on_files_loaded(commit, fs),
                            lambda e: self._on_files_error(oid, e))

    def _on_files_loaded(self, commit: Commit, files: list[CommitFile]) -> None:
        self._files_loading.discard(commit.oid)
        if not self._alive:
            return
        self._files.put(commit.oid, files, keep=self._expanded)
        self.filesLoaded.emit(commit)
        self._apply_expand(commit, files)

    def _on_files_error(self, oid: str, err: BaseException) -> None:
        self._files_loading.discard(oid)
        if self._alive:
            self.infoRequested.emit(colored(f"出错了：{html.escape(str(err))}", theme.ERROR))

    def _apply_expand(self, commit: Commit, files: list[CommitFile]) -> None:
        oid = commit.oid
        if not self.model.index_of(oid).isValid():
            return
        self.model.expand(oid, files)
        self._expanded.add(oid)
        if not files:
            self.infoRequested.emit(commit_info_html(commit) + "<br>"
                                    + colored("这个提交没有文件改动。", theme.TEXT_DIM))
            return
        last = self.model.index(self.model.index_of(oid).row() + len(files), 0)
        self.view.scrollTo(last, QAbstractItemView.ScrollHint.EnsureVisible)    # 展开后让文件清单露出来

    def _collapse(self, row: Row, select_commit: bool = False) -> None:
        oid = row.commit.oid
        in_files = self._sel is not None and self._sel[0] == oid and self._sel[1] is not None
        self.model.collapse(oid)
        self._expanded.discard(oid)
        if in_files or select_commit:           # 选中的文件行被收起了：选回提交行
            idx = self.model.index_of(oid)
            if idx.isValid():
                self.view.setCurrentIndex(idx)
