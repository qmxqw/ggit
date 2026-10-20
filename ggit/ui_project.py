"""一个项目的完整页面：把各个面板组装起来，负责刷新和它们之间的联动。

每个项目一个 ProjectView，放在主窗口的 QStackedWidget 里。切换项目只是切换页面，
所以每个项目打开的文档标签页、选中的提交、展开的文件、滚动位置都原样保留。

布局（git 模式）：
  左：状态行（ui_status）/ 更改（ui_changes）/ 图形（ui_graph_panel）或文件历史（ui_history）
  右：说明栏 / 文档标签页（ui_docs）/ diff（ui_diff）；最下面是可收起的命令面板（ui_console）
file 模式时左右换成文件树和文件预览（ui_files）。命令面板两种模式共用。
写操作（Fetch / Pull / Push、暂存、提交、分支…）由 ui_ops.GitOps 执行。
"""
from __future__ import annotations

import html
import os
import time
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QMessageBox, QSizePolicy, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from . import fileutil, gitcore, theme
from .async_runner import AsyncRunner
from .gitcore import BranchInfo, Commit, CommitFile, FileChange, FileDiff, HistoryEntry, Snapshot
from .options import ViewOptions
from .ui_branches import BranchMenu
from .ui_changes import ChangesPanel
from .ui_common import colored, commit_info_html
from .ui_console import ConsolePanel
from .ui_dialogs import PickerDialog
from .ui_diff import DiffDocument
from .ui_docs import DocArea
from .ui_files import FileDocument, FileTreePanel
from .ui_graph_panel import GraphPanel
from .ui_history import HistoryPanel
from .ui_ops import GitOps
from .ui_status import ProjectStatusBar

_DIFF_EMPTY = ("在左侧点一个文件，查看它的差异。\n\n每点一个文件就新开一个标签，旧的标签不会被关掉；\n"
               "用标签上的 ×、鼠标中键或 Ctrl+W 关闭。")
_FILE_EMPTY = ("在左侧的文件树里点一个文件，在这里预览。\n\n可以同时打开多个文件；\n"
               "用标签上的 ×、鼠标中键或 Ctrl+W 关闭。")


class ProjectView(QWidget):
    statusChanged = Signal(object)      # 状态（分支 / 是否有改动 / ahead-behind）更新后发出：self
    modeToggleRequested = Signal()      # diff 区的“切换模式”按钮被点了
    opFinished = Signal(object, str, bool, str)     # 写操作结束：self, 操作名称, 是否成功, 一句话说明

    def __init__(self, path: str, name: str, runner: AsyncRunner, options: ViewOptions,
                 parent: QWidget | None = None, *, history: list[str] | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.name = name
        self.runner = runner
        self.options = options
        self.snap: Snapshot | None = None   # 最近一次读到的状态
        self.error = ""                     # 最近一次读取状态失败的原因（成功则为空）
        self._alive = True
        self._gen = 0                       # 刷新序号：只接受最新一次的结果
        self._snap_task: int | None = None
        self._last_refresh = 0.0
        self._work_mode = "git"
        self.file_panel: FileTreePanel | None = None    # file 模式的文件树（第一次进入 file 模式时才创建）
        self.file_docs: DocArea | None = None           # file 模式的文档标签页
        self._build_ui()
        self.console.input.set_history(history or [])

    # ================================================================== 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.splitter)

        # ---------------- 左：状态行 + 更改 + 图形 / 文件历史 ----------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        self.status = ProjectStatusBar()
        self.branch_menu = BranchMenu(self.runner, self.path, self)
        self.status.set_branch_menu(self.branch_menu)
        lv.addWidget(self.status)

        self.left_split = QSplitter(Qt.Orientation.Vertical)
        lv.addWidget(self.left_split, 1)
        self.changes = ChangesPanel()
        self.left_split.addWidget(self.changes)
        self.left_stack = QStackedWidget()          # 0：图形；1：文件历史
        self.graph_panel = GraphPanel(self.runner, self.path)
        self.history = HistoryPanel(self.runner, self.path)
        self.left_stack.addWidget(self.graph_panel)
        self.left_stack.addWidget(self.history)
        self.left_split.addWidget(self.left_stack)
        self.left_split.setSizes([160, 540])
        self.left_split.setStretchFactor(1, 1)
        self.left_modes = QStackedWidget()          # 0：git 树；1：文件树
        self.left_modes.addWidget(left)
        self.splitter.addWidget(self.left_modes)

        # ---------------- 右：说明栏 + 文档标签页 ----------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        info_bar = QFrame()
        info_bar.setObjectName("projectInfoBar")
        iv = QVBoxLayout(info_bar)
        iv.setContentsMargins(12, 6, 12, 6)
        iv.setSpacing(2)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        iv.addWidget(self.info)
        rv.addWidget(info_bar)
        self.docs = DocArea(self.runner, self.options, lambda: DiffDocument(self.options.diff_mode), _DIFF_EMPTY)
        rv.addWidget(self.docs, 1)
        self.right_modes = QStackedWidget()         # 0：diff 文档；1：文件预览文档
        self.right_modes.addWidget(right)
        self.right_split = QSplitter(Qt.Orientation.Vertical)
        self.right_split.addWidget(self.right_modes)
        self.console = ConsolePanel()
        self.console.setVisible(False)
        self.right_split.addWidget(self.console)
        self.right_split.setStretchFactor(0, 1)
        self.right_split.setCollapsible(0, False)
        self.splitter.addWidget(self.right_split)

        self.splitter.setSizes([430, 970])
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, False)

        # ---------------- 面板之间的联动 ----------------
        self.docs.infoChanged.connect(self.info.setText)
        self.docs.modeToggleRequested.connect(self.modeToggleRequested)
        self.changes.fileActivated.connect(self._on_change_activated)
        self.changes.historyRequested.connect(self.show_history)
        self.graph_panel.commitSelected.connect(self._on_commit_selected)
        self.graph_panel.fileOpenRequested.connect(self._open_commit_file)
        self.graph_panel.historyRequested.connect(self.show_history)
        self.graph_panel.infoRequested.connect(self.info.setText)
        self.graph_panel.filesLoaded.connect(self._on_commit_files_loaded)
        self.graph_panel.loadFailed.connect(self.status.show_problem)
        self.graph_panel.scopeChanged.connect(self.branch_menu.set_scope)
        self.branch_menu.scopeChosen.connect(self._on_branch_chosen)
        self.history.entryActivated.connect(self._on_history_entry)
        self.history.closeRequested.connect(self.close_history)

        # ---------------- 写操作 ----------------
        self.ops = GitOps(self.runner, self.path, self.console, self, lambda: self.snap,
                          lambda: self.refresh(force=True), lambda: self.options.pull_mode,
                          self.branch_menu.branches)
        self.console.input.branch_source = lambda: [b.name for b in self.branch_menu.branches()]
        self.console.closeRequested.connect(lambda: self.console.setVisible(False))
        self.ops.consoleRequested.connect(self.show_console)
        self.ops.busyChanged.connect(self.status.set_busy)
        self.ops.busyChanged.connect(self.changes.set_busy)
        self.ops.commitDone.connect(self.changes.clear_message)
        self.ops.opFinished.connect(lambda label, ok, detail: self.opFinished.emit(self, label, ok, detail))
        self.status.fetchRequested.connect(self.ops.fetch)
        self.status.pullRequested.connect(lambda: self.ops.pull())
        self.status.pushRequested.connect(lambda: self.ops.push())
        self.status.continueRequested.connect(self.ops.continue_operation)
        self.status.abortRequested.connect(self.ops.abort_operation)
        self.changes.stageRequested.connect(self.ops.stage)
        self.changes.unstageRequested.connect(self.ops.unstage)
        self.changes.discardRequested.connect(self.ops.discard)
        self.changes.commitRequested.connect(self.ops.commit)
        self.changes.amendToggled.connect(self._on_amend_toggled)
        self.branch_menu.branchActionRequested.connect(self.branch_action)
        self.graph_panel.commitActionRequested.connect(self.ops.commit_action)

    # ================================================================== 刷新
    def refresh(self, force: bool = False) -> None:
        """重新读取状态。force=True（F5）时同时重新加载提交图。1 秒内的重复请求（非 force）被忽略。"""
        if not self._alive:
            return
        now = time.time()
        if not force and now - self._last_refresh < 1.0:
            return
        self._last_refresh = now
        if self.file_docs is not None:
            self.file_docs.reload_all()         # 别的程序改了文件，这里的预览跟着更新
        if self._snap_task is not None:
            self.runner.cancel(self._snap_task)
        self._gen += 1
        gen, path = self._gen, self.path
        self._snap_task = self.runner.submit(
            lambda: gitcore.get_snapshot(path),
            lambda s: self._on_snapshot(gen, s, force),
            lambda e: self._on_error(gen, e),
        )

    def _on_error(self, gen: int, err: BaseException) -> None:
        if not self._alive or gen != self._gen:
            return
        self._snap_task = None
        self.error = str(err)
        self.status.show_error(self.error)
        self.statusChanged.emit(self)

    def _on_snapshot(self, gen: int, snap: Snapshot, force: bool) -> None:
        if not self._alive or gen != self._gen:
            return
        self._snap_task = None
        self.snap = snap
        self.error = ""
        self.status.show_snapshot(snap, self.name, self.path)
        self.changes.set_changes(snap.status.changes, snap.status.total_changes)
        self._refresh_work_docs(snap.status.changes)
        if self.graph_panel.set_snapshot(snap, force) and self.history.path and self.left_stack.currentIndex() == 1:
            self.history.load(self.history.path, quiet=True)
        self.statusChanged.emit(self)
        self.branch_menu.refresh()

    def _refresh_work_docs(self, changes: list[FileChange]) -> None:
        """打开着的“工作区”文档跟着刷新；文件已经没有改动的，显示一句说明。"""
        by_path = {c.path: c for c in changes}
        for doc in self.docs.docs_of_kind("work"):
            ch = by_path.get(doc.data) if isinstance(doc.data, str) else None
            if ch is None:
                doc.fn = None
                doc.sig = None
                doc.view.set_message("该文件已没有未提交的改动。")
            else:
                doc.fn = self._work_fn(ch)
                self.docs.load(doc, quiet=True)

    # ================================================================== 选中 / 打开文档
    def _select_source(self, kind: str) -> None:
        """三个列表（更改 / 提交图 / 文件历史）同一时刻只有一个有选中项。"""
        if kind != "work":
            self.changes.clear_selection()
        if kind != "commit":
            self.graph_panel.clear_selection()
        if kind != "hist":
            self.history.clear_selection()

    def _work_fn(self, ch: FileChange) -> Callable[[ViewOptions], FileDiff]:
        has_head = bool(self.snap and self.snap.status.head_oid)
        repo = self.path
        return lambda o: gitcore.get_worktree_diff(repo, ch, has_head, ignore_ws=o.ignore_ws,
                                                   ignore_eol=o.ignore_eol, encoding=o.encoding)

    def _on_change_activated(self, ch: FileChange) -> None:
        self._select_source("work")
        name = ch.path.rpartition("/")[2]
        info = f"<b>工作区更改</b><br>{colored(html.escape(ch.path), theme.TEXT_DIM)}"
        self.docs.open_doc(f"w|{ch.path}", f"{name} · 工作区", f"{ch.path}  （工作区更改）", "work",
                           self._work_fn(ch), info, data=ch.path)

    def _on_commit_selected(self, c: Commit) -> None:
        self._select_source("commit")
        self.info.setText(commit_info_html(c))

    def _on_commit_files_loaded(self, c: Commit) -> None:
        """提交的文件列表读完了：去掉“加载文件列表…”的提示，恢复说明栏。"""
        doc = self.docs.current_doc()
        if doc is not None:
            self.info.setText(doc.info)
        elif self.graph_panel.selected_commit_oid() == c.oid:
            self.info.setText(commit_info_html(c))
        else:
            self.info.setText("")

    def _open_commit_file(self, commit: Commit, cf: CommitFile) -> None:
        repo, oid = self.path, commit.oid
        paths = [p for p in (cf.old_path, cf.path) if p]
        name = cf.path.rpartition("/")[2]
        self.docs.open_doc(f"c|{oid}|{cf.path}", f"{name} · {commit.short}", f"{cf.path}  @ {commit.short}",
                           "commit",
                           lambda o: gitcore.get_commit_diff(repo, oid, paths, ignore_ws=o.ignore_ws,
                                                             ignore_eol=o.ignore_eol, encoding=o.encoding),
                           commit_info_html(commit), data=cf.path)

    def _on_history_entry(self, e: HistoryEntry) -> None:
        self._select_source("hist")
        self._open_commit_file(e.commit, CommitFile(e.status, e.path, e.old_path))

    # ================================================================== 分支 / 文件历史 / 搜索文件
    def open_branch_picker(self) -> None:
        """展开分支下拉列表（Ctrl+B）。"""
        self.status.show_branch_menu()

    def _on_branch_chosen(self, value: str) -> None:
        self.close_history()
        self.graph_panel.choose_branch(value)

    def show_history(self, path: str) -> None:
        self.left_stack.setCurrentIndex(1)
        self.history.load(path)

    def close_history(self) -> None:
        self.history.stop()
        self.left_stack.setCurrentIndex(0)

    def open_file_search(self) -> None:
        """Ctrl+F：搜索当前项目里的文件。git 模式：选中后查看它的历史；file 模式：选中后打开预览。"""
        path = self.path

        def on_err(e: BaseException) -> None:
            QMessageBox.warning(self, "搜索文件", f"读取文件列表失败：\n{e}")

        if self._work_mode == "file":
            # 已跟踪 + 未被忽略的未跟踪文件：node_modules、build 这类被忽略的大目录不会挤满列表
            self.runner.submit(lambda: gitcore.list_files(path, untracked=True), self._pick_file_to_open, on_err)
        else:
            self.runner.submit(lambda: gitcore.list_files(path), self._pick_file_for_history, on_err)

    def _pick_file_to_open(self, files: list[str]) -> None:
        if not self._alive:
            return
        dlg = PickerDialog("打开文件 — 搜索文件", [(f, f) for f in files], self.window())
        if dlg.exec() and dlg.value:
            self.open_file(os.path.join(self.path, dlg.value))

    def _pick_file_for_history(self, files: list[str]) -> None:
        if not self._alive:
            return
        dlg = PickerDialog("查看文件历史 — 搜索文件", [(f, f) for f in files], self.window())
        if dlg.exec() and dlg.value:
            self.show_history(dlg.value)

    # ================================================================== 写操作 / 命令面板
    def show_console(self, focus: bool = False) -> None:
        if not self.console.isVisible():
            self.console.setVisible(True)
            total = max(self.right_split.height(), 400)
            self.right_split.setSizes([total - total // 3, total // 3])
        if focus:
            self.console.focus_input()

    def toggle_console(self) -> None:
        """Ctrl+`：展开命令面板并把焦点放到输入框；已经展开且焦点在里面就收起。"""
        if self.console.isVisible() and self.console.input.hasFocus():
            self.console.setVisible(False)
        else:
            self.show_console(focus=True)

    def command_history(self) -> list[str]:
        return list(self.console.input.history)

    def branch_action(self, op: str, b: BranchInfo) -> None:
        """分支列表右键菜单里选的操作。"""
        if op == "switch":
            self.ops.switch_branch(b)
        elif op == "merge":
            self.ops.merge_branch(b)
        elif op == "rebase":
            self.ops.rebase_onto(b)
        elif op == "branch":
            self.ops.create_branch(b.name, b.name)
        elif op == "delete":
            self.ops.delete_branch(b)

    def focus_commit(self) -> None:
        """Ctrl+K：回到 git 模式，把焦点放到提交说明框。"""
        self.changes.focus_message()

    def _on_amend_toggled(self, on: bool) -> None:
        if on:          # 勾上“修改上一次提交”：说明框是空的就填上上一次的说明
            self.ops.load_last_message(self.changes.set_message_if_empty)

    # ================================================================== 给主窗口用
    def set_diff_mode(self, mode: str) -> None:
        self.docs.set_mode(mode)

    def apply_font_size(self, size: int) -> None:
        """全局字号变了：提交图按新行高重新排版，已打开的文档换代码字号。"""
        self.graph_panel.apply_font_size()
        self.changes.refresh_view()
        self.console.apply_font_size(size)
        for area in (self.docs, self.file_docs):
            if area is not None:
                for doc in area.all_docs():
                    doc.view.apply_font_size(size)

    def reload_docs(self) -> None:
        self.docs.reload_all()
        if self.file_docs is not None:
            self.file_docs.reload_all()

    def dispose(self) -> None:
        """项目被移除 / 程序退出：取消这个项目所有还在跑的 git 任务。"""
        self._alive = False
        self.ops.dispose()
        if self._snap_task is not None:
            self.runner.cancel(self._snap_task)
            self._snap_task = None
        self.graph_panel.dispose()
        self.history.dispose()
        self.branch_menu.dispose()
        self.docs.cancel_all()
        if self.file_docs is not None:
            self.file_docs.cancel_all()

    # ================================================================== 工作模式：git / file
    def active_docs(self) -> DocArea:
        """当前工作模式下的文档标签页（Ctrl+W、关闭、切换标签都作用在它上面）。"""
        if self._work_mode == "file" and self.file_docs is not None:
            return self.file_docs
        return self.docs

    def set_work_mode(self, mode: str) -> None:
        self._work_mode = mode
        if mode == "file":
            self._ensure_file_ui()
        idx = 1 if (mode == "file" and self.file_panel is not None) else 0
        self.left_modes.setCurrentIndex(idx)
        self.right_modes.setCurrentIndex(idx)

    def _ensure_file_ui(self) -> None:
        if self.file_panel is not None:
            return
        self.file_panel = FileTreePanel(self.path)
        self.file_panel.fileActivated.connect(self.open_file)
        self.left_modes.addWidget(self.file_panel)
        self.file_docs = DocArea(self.runner, self.options, FileDocument, _FILE_EMPTY)
        self.right_modes.addWidget(self.file_docs)

    def open_file(self, abs_path: str) -> None:
        """file 模式：在新标签里预览文件（已经开着就切过去）。"""
        if self.file_docs is None:
            return
        rel = os.path.relpath(abs_path, self.path).replace("\\", "/")
        self.file_docs.open_doc(f"f|{rel}", os.path.basename(abs_path), rel, "file",
                                lambda o: fileutil.read_text_file(abs_path, o.encoding), "",
                                data=abs_path)
