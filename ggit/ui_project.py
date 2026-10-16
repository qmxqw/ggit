"""一个项目的完整页面。

每个项目一个 ProjectView，放在主窗口的 QStackedWidget 里。切换项目只是切换页面，
所以每个项目打开的文档标签页、选中的提交、展开的文件、滚动位置都原样保留。

布局：
  左：状态行 / “更改”（未提交的文件）/ “图形”（提交图；点提交就地展开它修改的文件）
  右：说明 / 文档标签页 / diff（上下合并或左右对照）
"""
from __future__ import annotations

import html
import os
import time

from PySide6.QtCore import QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QSizePolicy, QSplitter, QStackedWidget,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import fileutil, gitcore
from .graph import GraphLayout
from .items import FileRow, Row
from .ui_dialogs import PickerDialog
from .ui_diff import DiffDocument
from .ui_docs import DocArea
from .ui_files import FileDocument, FileTreePanel
from .ui_graph import CommitModel, GraphView
from .utils import STATUS_COLORS, ago, fmt_time

PAGE = gitcore.LOG_PAGE


# ------------------------------------------------------------------ 小部件工厂
def _header(text: str):
    """区块标题栏。返回 (框架, 标题标签, 布局)，布局右侧可以再放控件。"""
    frame = QFrame()
    frame.setObjectName("sectionHeader")
    frame.setStyleSheet("QFrame#sectionHeader{background:#2d2d30;}")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(8, 4, 6, 4)
    label = QLabel(text)
    f = label.font()
    f.setBold(True)
    label.setFont(f)
    label.setStyleSheet("color:#bbbbbb;")
    lay.addWidget(label)
    lay.addStretch(1)
    return frame, label, lay


def _make_file_tree() -> QTreeWidget:
    """“更改”列表：文件名 / 所在目录 / 状态字母。"""
    t = QTreeWidget()
    t.setColumnCount(3)
    t.setHeaderHidden(True)
    t.setRootIsDecorated(False)
    t.setUniformRowHeights(True)
    t.setFrameShape(QFrame.Shape.NoFrame)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    h = t.header()
    h.setStretchLastSection(False)
    h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    return t


def _file_item(path: str, letter: str, data, orig: str | None = None) -> QTreeWidgetItem:
    directory, _, name = path.rpartition("/")
    it = QTreeWidgetItem([name, directory, letter])
    it.setData(0, Qt.ItemDataRole.UserRole, data)
    it.setForeground(1, QBrush(QColor("#808080")))
    it.setForeground(2, QBrush(QColor(STATUS_COLORS.get(letter, "#d4d4d4"))))
    it.setToolTip(0, f"{orig} → {path}" if orig else path)
    return it


def commit_info_html(c) -> str:
    parts = [f"<span style='color:#9a9a9a'>{c.short} · {html.escape(c.author)} · {fmt_time(c.time)}</span>"]
    if c.refs:
        parts.append(f"<span style='color:#7aa2f7'>{html.escape('  '.join(r.name for r in c.refs))}</span>")
    if len(c.parents) > 1:
        parts.append("<span style='color:#9a9a9a'>合并提交</span>")
    return f"<b>{html.escape(c.subject)}</b><br>{' · '.join(parts)}"


# ------------------------------------------------------------------ 项目页面
class ProjectView(QWidget):
    statusChanged = Signal(object)      # 状态（分支 / 是否有改动 / ahead-behind）更新后发出
    modeToggleRequested = Signal()      # diff 区的“切换模式”按钮被点了

    def __init__(self, path: str, name: str, runner, options, parent=None):
        super().__init__(parent)
        self.path = path
        self.name = name
        self.runner = runner
        self.options = options
        self.snap = None                # 最近一次的 gitcore.Snapshot
        self.error = ""

        self._alive = True
        self._gen = 0                   # 刷新序号：只接受最新一次的结果
        self._graph_gen = 0
        self._hist_gen = 0
        self._log_loading = False
        self._layout = GraphLayout()
        self._scope = gitcore.SCOPE_ALL     # 默认显示所有本地 / 远端分支和标签
        self._scope_ref = None              # “只看某个分支”时的完整引用名
        self._fp = None                 # 引用指纹：没变就不重新加载提交图
        self._last_refresh = 0.0
        self._src = None                # 当前选中的来源：("commit", oid) / ("work", path) / ("hist", oid)
        self._sel_file = None           # 选中的是某个提交下的哪个文件
        self._files_cache: dict = {}    # 提交 id -> 它修改的文件列表（提交不可变，缓存永远有效）
        self._expanded: set = set()     # 展开着的提交
        self._files_loading: set = set()
        self._last_toggle = ("", 0.0)
        self._hist_path = None
        self._work_mode = "git"
        self.file_panel = None          # file 模式的文件树（第一次进入 file 模式时才创建）
        self.file_docs = None           # file 模式的文档标签页
        self._snap_task = None
        self._graph_task = None
        self._hist_task = None
        self._branches_cache: list = []
        self._branches_loading: bool = False
        self._branch_task = None
        self._build_ui()

    # ================================================================== 界面
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.splitter)

        # ---------------- 左：git 树 ----------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        # 优化：状态栏重构为高质感双行卡片条，主次分明，与右侧水平对齐
        status_bar = QFrame()
        status_bar.setObjectName("projectStatusBar")
        status_bar.setStyleSheet("""
            QFrame#projectStatusBar {
                background: #252528;
                border-bottom: 1px solid #38383c;
            }
        """)
        sv = QVBoxLayout(status_bar)
        sv.setContentsMargins(10, 6, 10, 6)
        sv.setSpacing(3)

        # 第 1 行：分支胶囊按钮 + 同步状态徽标
        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(8)

        self.branch_btn = QToolButton()
        self.branch_btn.setText("⎇ … ▾")
        self.branch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.branch_btn.setToolTip("点击展开分支列表切换查看（本地 / 远端）  (Ctrl+B)")
        self.branch_btn.setStyleSheet("""
            QToolButton {
                background: #36363c;
                color: #ffffff;
                font-weight: bold;
                border-radius: 4px;
                padding: 3px 8px;
                border: 1px solid #484850;
            }
            QToolButton:hover {
                background: #44444c;
                border-color: #0e7ad1;
            }
            QToolButton::menu-indicator {
                image: none;
            }
        """)
        self.branch_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.branch_menu = QMenu(self)
        self.branch_menu.setObjectName("branchMenu")
        self.branch_menu.aboutToShow.connect(self._rebuild_branch_menu)
        self.branch_btn.setMenu(self.branch_menu)
        r1.addWidget(self.branch_btn)

        self.sync_badge = QLabel()
        r1.addWidget(self.sync_badge)
        r1.addStretch(1)
        sv.addWidget(row1)

        # 第 2 行：上游远端与 fetch 时间（次要元数据，浅灰舒展）
        self.status_meta = QLabel("加载中…")
        self.status_meta.setStyleSheet("color: #8a8a8e;")
        self.status_meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_label = self.status_meta
        sv.addWidget(self.status_meta)

        lv.addWidget(status_bar)

        self.left_split = QSplitter(Qt.Orientation.Vertical)
        lv.addWidget(self.left_split, 1)

        # 更改
        chg_box = QWidget()
        cl = QVBoxLayout(chg_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        hdr, self.changes_title, _ = _header("更改")
        cl.addWidget(hdr)
        self.changes = _make_file_tree()
        cl.addWidget(self.changes)
        self.left_split.addWidget(chg_box)

        # 图形 / 文件历史（二选一）
        self.left_stack = QStackedWidget()
        graph_page = QWidget()
        gl = QVBoxLayout(graph_page)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(0)
        hdr, _, hl = _header("图形")
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["全部分支", "当前分支"])
        self.scope_combo.setToolTip("全部分支：所有本地分支、远端分支和标签；当前分支：当前分支及其远端对应分支；\n也可以点左上角的分支名，选一个分支只看它的历史")
        hl.addWidget(self.scope_combo)
        gl.addWidget(hdr)
        self.model = CommitModel(self)
        self.graph = GraphView()
        self.graph.setModel(self.model)
        gl.addWidget(self.graph)
        self.left_stack.addWidget(graph_page)

        hist_page = QWidget()
        hv = QVBoxLayout(hist_page)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(0)
        hdr, self.hist_title, hhl = _header("文件历史")
        back = QPushButton("← 返回图形")
        back.setToolTip("Esc")
        hhl.addWidget(back)
        hv.addWidget(hdr)
        self.hist_list = QListWidget()
        self.hist_list.setFrameShape(QFrame.Shape.NoFrame)
        self.hist_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        hv.addWidget(self.hist_list)
        self.left_stack.addWidget(hist_page)
        self.left_split.addWidget(self.left_stack)
        self.left_split.setSizes([160, 540])
        self.left_split.setStretchFactor(1, 1)
        self.left_modes = QStackedWidget()      # 0：git 树；1：文件树
        self.left_modes.addWidget(left)
        self.splitter.addWidget(self.left_modes)

        # ---------------- 右：说明 + 文档标签页 + diff ----------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        info_bar = QFrame()
        info_bar.setObjectName("projectInfoBar")
        info_bar.setStyleSheet("""
            QFrame#projectInfoBar {
                background: #252528;
                border-bottom: 1px solid #38383c;
            }
        """)
        iv = QVBoxLayout(info_bar)
        iv.setContentsMargins(12, 6, 12, 6)
        iv.setSpacing(2)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        iv.addWidget(self.info)
        rv.addWidget(info_bar)
        self.docs = DocArea(self.runner, self.options, lambda: DiffDocument(self.options.diff_mode),
                            "在左侧点一个文件，查看它的差异。\n\n每点一个文件就新开一个标签，旧的标签不会被关掉；\n用标签上的 ×、鼠标中键或 Ctrl+W 关闭。")
        rv.addWidget(self.docs, 1)
        self.right_modes = QStackedWidget()     # 0：diff 文档；1：文件预览文档
        self.right_modes.addWidget(right)
        self.splitter.addWidget(self.right_modes)

        self.splitter.setSizes([430, 970])
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, False)

        # ---------------- 信号 ----------------
        self.docs.infoChanged.connect(self.info.setText)
        self.docs.modeToggleRequested.connect(self.modeToggleRequested)
        self.changes.itemClicked.connect(self._on_change_clicked)
        self.changes.itemActivated.connect(self._on_change_clicked)
        self.changes.customContextMenuRequested.connect(self._changes_menu)
        self.graph.selectionModel().currentChanged.connect(self._on_graph_current)
        self.graph.clicked.connect(self._on_graph_clicked)
        self.graph.expandRequested.connect(self._on_expand_requested)
        self.graph.customContextMenuRequested.connect(self._graph_menu)
        self.model.moreRequested.connect(self._load_more)
        self.scope_combo.currentIndexChanged.connect(self._on_scope)
        self.hist_list.itemClicked.connect(self._on_hist_clicked)
        self.hist_list.itemActivated.connect(self._on_hist_clicked)
        back.clicked.connect(self.close_history)
        esc = QShortcut(QKeySequence("Esc"), hist_page)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close_history)

    # ================================================================== 刷新
    def refresh(self, force: bool = False) -> None:
        """重新读取状态。force=True（F5）时同时重新加载提交图。"""
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
            self._snap_task = None
        self._gen += 1
        gen = self._gen
        path = self.path
        self._snap_task = self.runner.submit(
            lambda: gitcore.get_snapshot(path),
            lambda s: self._on_snapshot(gen, s, force),
            lambda e: self._on_error(gen, e),
        )

    def _on_error(self, gen, err):
        if not self._alive or gen != self._gen:
            return
        self.error = str(err)
        self.sync_badge.setText("<span style='color:#f14c4c; font-weight:bold;'>读取失败</span>")
        self.status_meta.setText(f"<span style='color:#f14c4c'>{html.escape(self.error)}</span>")
        self.status_meta.setToolTip(self.error)
        self.statusChanged.emit(self)

    def _on_snapshot(self, gen, snap, force):
        if not self._alive or gen != self._gen:
            return
        first = self.snap is None
        self.snap = snap
        self.error = ""
        self._update_status_label()
        self.model.head_oid = snap.status.head_oid
        self.graph.viewport().update()

        self._fill_changes(snap.status.changes)
        by_path = {c.path: c for c in snap.status.changes}
        for doc in self.docs.docs_of_kind("work"):          # 打开着的“工作区”文档跟着刷新
            ch = by_path.get(doc.data)
            if ch is None:
                doc.fn = None
                doc.sig = None
                doc.view.set_message("该文件已没有未提交的改动。")
            else:
                doc.fn = self._work_fn(ch)
                self.docs.load(doc, quiet=True)

        if force or first or snap.fingerprint != self._fp:
            self._fp = snap.fingerprint
            self._reload_graph()
            if self._hist_path and self.left_stack.currentIndex() == 1:
                self._load_history(self._hist_path, quiet=True)
        self.statusChanged.emit(self)
        self._refresh_branches()

    def _update_status_label(self):
        snap = self.snap
        s = snap.status
        if s.branch:
            head = f"⎇ {s.branch} ▾"
        elif s.head_oid:
            head = f"HEAD 分离 @ {s.head_oid[:7]} ▾"
        else:
            head = "尚无提交 ▾"
        self.branch_btn.setText(head)

        badges = []
        if s.ahead:
            badges.append(f"<span style='background:rgba(115,201,145,0.18); color:#73c991; padding:2px 7px; border-radius:3px; font-weight:bold;'>↑ {s.ahead}</span>")
        if s.behind:
            badges.append(f"<span style='background:rgba(255,158,100,0.18); color:#ff9e64; padding:2px 7px; border-radius:3px; font-weight:bold;'>↓ {s.behind}</span>")
        if not s.ahead and not s.behind and s.upstream:
            badges.append("<span style='color:#73c991; font-weight:bold;'>✔ 已同步</span>")
        elif not s.upstream and s.branch:
            badges.append("<span style='color:#8a8a8e;'>本地独有</span>")
        self.sync_badge.setText("  ".join(badges))

        meta_parts = []
        if s.upstream:
            meta_parts.append(f"→ {html.escape(s.upstream)}")
        meta_parts.append(f"fetch: {ago(s.last_fetch)}" if s.last_fetch else "未 fetch")
        n_local = sum(1 for r in snap.refs if r.startswith("refs/heads/"))
        n_remote = sum(1 for r in snap.refs if r.startswith("refs/remotes/") and not r.endswith("/HEAD"))
        meta_parts.append(f"本地 {n_local} · 远端 {n_remote}")
        self.status_meta.setText("   ·   ".join(meta_parts))

        tip = [f"项目：{self.name}", f"路径：{self.path}"]
        if s.branch:
            tip.append(f"分支：{s.branch}")
        if s.upstream:
            tip.append(f"上游：{s.upstream} (领先 {s.ahead}，落后 {s.behind})")
        if s.last_fetch:
            tip.append(f"上次 fetch：{fmt_time(s.last_fetch)}")
        tip.append(f"统计：本地 {n_local} 个分支，远端 {n_remote} 个分支")
        tip.append("提示：按 F5 刷新状态；Ctrl+B 查看分支列表")
        tip_text = "\n".join(tip)
        self.sync_badge.setToolTip(tip_text)
        self.status_meta.setToolTip(tip_text)

    # ================================================================== 更改列表
    def _fill_changes(self, changes):
        """重建“更改”列表，保持原来选中的文件。"""
        cur_path = self._src[1] if self._src and self._src[0] == "work" else None
        sel = None
        self.changes.blockSignals(True)
        self.changes.clear()
        for ch in changes:
            it = _file_item(ch.path, ch.kind, ch, ch.orig_path)
            self.changes.addTopLevelItem(it)
            if ch.path == cur_path:
                sel = it
        if sel is not None:
            self.changes.setCurrentItem(sel)
        self.changes.blockSignals(False)
        self.changes_title.setText(f"更改  ({len(changes)})" if changes else "更改")

    def _work_fn(self, ch):
        has_head = bool(self.snap.status.head_oid)
        repo = self.path
        return lambda o: gitcore.get_worktree_diff(repo, ch, has_head, ignore_ws=o.ignore_ws,
                                                   ignore_eol=o.ignore_eol, encoding=o.encoding)

    def _open_work(self, ch):
        name = ch.path.rpartition("/")[2]
        info = f"<b>工作区更改</b><br><span style='color:#9a9a9a'>{html.escape(ch.path)}</span>"
        self.docs.open_doc(f"w|{ch.path}", f"{name} · 工作区", f"{ch.path}  （工作区更改）", "work",
                           self._work_fn(ch), info, data=ch.path)

    def _on_change_clicked(self, item, _col=0):
        if item is None:
            return
        ch = item.data(0, Qt.ItemDataRole.UserRole)
        self._src = ("work", ch.path)
        self._sel_file = None
        self._clear_graph_selection()
        self._open_work(ch)

    def _clear_changes_selection(self):
        self.changes.blockSignals(True)
        self.changes.setCurrentIndex(QModelIndex())
        self.changes.clearSelection()
        self.changes.blockSignals(False)

    def _clear_graph_selection(self):
        sm = self.graph.selectionModel()
        sm.blockSignals(True)
        self.graph.setCurrentIndex(QModelIndex())
        self.graph.clearSelection()
        sm.blockSignals(False)
        self.graph.viewport().update()

    def _changes_menu(self, pos):
        it = self.changes.itemAt(pos)
        if it is None:
            return
        ch = it.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        a_hist = menu.addAction("查看文件历史")
        a_hist.setEnabled(ch.kind != "U")             # 未跟踪文件还没有历史
        a_copy = menu.addAction("复制路径")
        chosen = menu.exec(self.changes.viewport().mapToGlobal(pos))
        if chosen == a_hist:
            self.show_history(ch.path)
        elif chosen == a_copy:
            QGuiApplication.clipboard().setText(ch.path)

    # ================================================================== 提交图
    def _on_scope(self, idx):
        if idx == 0:
            self._scope = gitcore.SCOPE_ALL
        elif idx == 1:
            self._scope = gitcore.SCOPE_AUTO
        else:
            self._scope = "ref:" + (self._scope_ref or "")
        if self.snap is not None:
            self._reload_graph()

    # ---------------------------------------------------------------- 分支下拉列表
    def _apply_branch_menu_style(self):
        base = self.font()
        base_size = base.pointSizeF() if base.pointSizeF() > 0 else float(base.pointSize())
        # 大尺寸分支下拉菜单：字号比常规大 25%，行高与内边距宽阔，触控点击更舒适
        font_sz = max(12, int(round(base_size * 1.25)))
        padding_v = max(7, int(round(base_size * 0.7)))
        self.branch_menu.setStyleSheet(f"""
            QMenu#branchMenu {{
                background-color: #252528;
                border: 1px solid #3c3c40;
                border-radius: 6px;
                padding: 6px 4px;
            }}
            QMenu#branchMenu::item {{
                padding: {padding_v}px 18px;
                border-radius: 4px;
                color: #e0e0e0;
                font-size: {font_sz}px;
                margin: 2px 4px;
            }}
            QMenu#branchMenu::item:selected {{
                background-color: #0e7ad1;
                color: #ffffff;
            }}
            QMenu#branchMenu::item:disabled {{
                color: #727278;
                font-weight: bold;
                padding: 6px 14px 2px 14px;
            }}
            QMenu#branchMenu::separator {{
                height: 1px;
                background: #38383c;
                margin: 5px 8px;
            }}
        """)

    def _refresh_branches(self):
        if self._branches_loading or not self._alive:
            return
        self._branches_loading = True
        path = self.path
        if self._branch_task is not None:
            self.runner.cancel(self._branch_task)
            self._branch_task = None
        self._branch_task = self.runner.submit(
            lambda: gitcore.get_branches(path),
            self._on_branches_loaded,
            self._on_branches_error
        )

    def _on_branches_loaded(self, branches):
        self._branches_loading = False
        self._branch_task = None
        if not self._alive:
            return
        self._branches_cache = branches
        if self.branch_menu.isVisible():
            self._rebuild_branch_menu()

    def _on_branches_error(self, err):
        self._branches_loading = False
        self._branch_task = None

    def _rebuild_branch_menu(self):
        self._apply_branch_menu_style()
        self.branch_menu.clear()

        # 1. 显示全部分支（全部本地 + 远端 + 标签）
        act_all = self.branch_menu.addAction("★   显示全部分支（本地 + 远端 + 标签）")
        act_all.setCheckable(True)
        act_all.setChecked(self._scope == gitcore.SCOPE_ALL)
        act_all.triggered.connect(lambda _=False: self._set_scope_ref("all"))

        branches = self._branches_cache
        if not branches:
            if self._branches_loading:
                act_loading = self.branch_menu.addAction("正在加载分支列表…")
                act_loading.setEnabled(False)
            else:
                self._refresh_branches()
            return

        # 2. 本地分支
        local_branches = [b for b in branches if b.kind == "local"]
        if local_branches:
            self.branch_menu.addSeparator()
            title_local = self.branch_menu.addAction("── 本地分支 ──")
            title_local.setEnabled(False)
            for b in local_branches:
                icon = "● " if b.is_current else "⎇  "
                parts = [f"{icon}{b.name}"]
                meta = []
                if b.upstream:
                    meta.append("上游已失效" if b.gone else f"↑{b.ahead} ↓{b.behind} → {b.upstream}")
                if b.time:
                    meta.append(ago(b.time))
                if meta:
                    parts.append(f"  ({' · '.join(meta)})")
                text = "".join(parts)

                act = self.branch_menu.addAction(text)
                act.setCheckable(True)
                is_active = (self._scope == "ref:" + b.ref) or (b.is_current and self._scope == gitcore.SCOPE_AUTO)
                act.setChecked(is_active)
                ref = b.ref
                act.triggered.connect(lambda _=False, r=ref: self._set_scope_ref(r))

        # 3. 远端分支
        remote_branches = [b for b in branches if b.kind == "remote"]
        if remote_branches:
            self.branch_menu.addSeparator()
            title_remote = self.branch_menu.addAction("── 远端分支 ──")
            title_remote.setEnabled(False)
            for b in remote_branches:
                parts = [f"☁  {b.name}"]
                if b.time:
                    parts.append(f"  ({ago(b.time)})")
                text = "".join(parts)
                act = self.branch_menu.addAction(text)
                act.setCheckable(True)
                act.setChecked(self._scope == "ref:" + b.ref)
                ref = b.ref
                act.triggered.connect(lambda _=False, r=ref: self._set_scope_ref(r))

    def open_branch_picker(self):
        """展开大尺寸分支下拉菜单（替代旧模态弹窗，支持快捷键 Ctrl+B 与主菜单调用）。"""
        self._rebuild_branch_menu()
        self.branch_btn.showMenu()

    def _set_scope_ref(self, value: str):
        self.close_history()
        if value == "all":
            self.scope_combo.setCurrentIndex(0)
            return
        self._scope_ref = value
        short = value.replace("refs/heads/", "", 1).replace("refs/remotes/", "", 1)
        label = f"只看：{short}"
        if self.scope_combo.count() < 3:
            self.scope_combo.addItem(label)
        else:
            self.scope_combo.setItemText(2, label)
        if self.scope_combo.currentIndex() == 2:
            self._on_scope(2)               # 已经在“只看”：换了一个分支，手动重新加载
        else:
            self.scope_combo.setCurrentIndex(2)

    def _expanded_files(self) -> dict:
        return {oid: self._files_cache[oid] for oid in self._expanded if oid in self._files_cache}

    def _reload_graph(self):
        snap = self.snap
        if snap is None:
            return
        if self._graph_task is not None:
            self.runner.cancel(self._graph_task)
            self._graph_task = None
        self._graph_gen += 1
        gen = self._graph_gen
        self._log_loading = True
        path, scope = self.path, self._scope
        self._graph_task = self.runner.submit(
            lambda: gitcore.get_log(path, snap, scope, 0, PAGE),
            lambda r: self._on_log(gen, r, True),
            lambda e: self._on_log_error(gen, e),
        )

    def _load_more(self):
        if self._log_loading or self.snap is None or not self.model.more:
            return
        if self._graph_task is not None:
            self.runner.cancel(self._graph_task)
            self._graph_task = None
        self._log_loading = True
        gen = self._graph_gen
        snap, path, scope = self.snap, self.path, self._scope
        skip = len([1 for it in self.model.list.items if isinstance(it, Row)])   # 已加载的提交数（不含文件行）
        self._graph_task = self.runner.submit(
            lambda: gitcore.get_log(path, snap, scope, skip, PAGE),
            lambda r: self._on_log(gen, r, False),
            lambda e: self._on_log_error(gen, e),
        )

    def _on_log(self, gen, commits, reset):
        if not self._alive or gen != self._graph_gen:
            return
        self._log_loading = False
        more = len(commits) >= PAGE
        if reset:
            sel_oid = self._src[1] if self._src and self._src[0] == "commit" else None
            scroll = self.graph.verticalScrollBar().value()
            self._layout = GraphLayout()
            rows = [Row(c, self._layout.add(c.oid, c.parents)) for c in commits]
            self.model.reset(rows, self.snap.status.head_oid, more, self._expanded_files())
            if sel_oid:
                idx = QModelIndex()
                if self._sel_file:
                    idx = self.model.file_index(sel_oid, self._sel_file)
                if not idx.isValid():
                    idx = self.model.index_of(sel_oid)
                if idx.isValid():
                    sm = self.graph.selectionModel()
                    sm.blockSignals(True)
                    self.graph.setCurrentIndex(idx)
                    sm.blockSignals(False)
                    self.graph.viewport().update()
            QTimer.singleShot(0, lambda: self.graph.verticalScrollBar().setValue(scroll))
        else:
            rows = [Row(c, self._layout.add(c.oid, c.parents)) for c in commits]
            self.model.append(rows, more, self._expanded_files())

    def _on_log_error(self, gen, err):
        if not self._alive or gen != self._graph_gen:
            return
        self._log_loading = False
        self.model.more = False
        self.status_label.setText(f"<span style='color:#f14c4c'>{html.escape(str(err))}</span>")

    def _graph_menu(self, pos):
        idx = self.graph.indexAt(pos)
        item = self.model.row_at(idx.row()) if idx.isValid() else None
        if item is None:
            return
        menu = QMenu(self)
        if isinstance(item, FileRow):
            a1 = menu.addAction("查看文件历史")
            a2 = menu.addAction("复制路径")
            chosen = menu.exec(self.graph.viewport().mapToGlobal(pos))
            if chosen == a1:
                self.show_history(item.file.path)
            elif chosen == a2:
                QGuiApplication.clipboard().setText(item.file.path)
        else:
            a1 = menu.addAction("复制提交 ID")
            a2 = menu.addAction("复制提交说明")
            chosen = menu.exec(self.graph.viewport().mapToGlobal(pos))
            if chosen == a1:
                QGuiApplication.clipboard().setText(item.commit.oid)
            elif chosen == a2:
                QGuiApplication.clipboard().setText(item.commit.subject)

    # ================================================================== 选中 / 展开提交
    def _on_graph_current(self, cur, prev):
        if not cur.isValid():
            return
        item = self.model.row_at(cur.row())
        if item is None:
            return
        self._clear_changes_selection()
        if isinstance(item, FileRow):
            c = item.parent.commit
            self._src = ("commit", c.oid)
            self._sel_file = item.file.path
            self.info.setText(commit_info_html(c))
        else:
            c = item.commit
            self._src = ("commit", c.oid)
            self._sel_file = None
            self.info.setText(commit_info_html(c))

    def _commit_fn(self, oid, cf):
        repo = self.path
        paths = [p for p in (cf.old_path, cf.path) if p]
        return lambda o: gitcore.get_commit_diff(repo, oid, paths, ignore_ws=o.ignore_ws,
                                                 ignore_eol=o.ignore_eol, encoding=o.encoding)

    def _open_commit_file(self, commit, cf):
        name = cf.path.rpartition("/")[2]
        self.docs.open_doc(f"c|{commit.oid}|{cf.path}", f"{name} · {commit.short}",
                           f"{cf.path}  @ {commit.short}", "commit",
                           self._commit_fn(commit.oid, cf), commit_info_html(commit),
                           data=cf.path)

    def _on_graph_clicked(self, index):
        item = self.model.row_at(index.row())
        if isinstance(item, FileRow):           # 点文件：新开一个标签（已经开着就切过去）
            self._open_commit_file(item.parent.commit, item.file)
            return
        if not isinstance(item, Row):
            return
        oid, now = item.commit.oid, time.monotonic()
        last_oid, last_t = self._last_toggle
        if oid == last_oid and now - last_t < QApplication.doubleClickInterval() / 1000.0:
            return                      # 双击的第二下：不要再切换一次
        self._last_toggle = (oid, now)
        self._toggle(item, 0)

    def _on_expand_requested(self, index, mode):
        item = self.model.row_at(index.row())
        if item is None:
            return
        if isinstance(item, FileRow):
            if mode == -1:              # 在文件上按 ←：收起所属提交并回到提交行
                self._collapse(item.parent, select_commit=True)
            elif mode == 0:             # 回车：打开这个文件
                self._open_commit_file(item.parent.commit, item.file)
            return
        self._toggle(item, mode)

    def _toggle(self, row, mode: int):
        want = (not row.expanded) if mode == 0 else (mode == 1)
        if want == row.expanded:
            return
        if want:
            self._expand(row)
        else:
            self._collapse(row)

    def _expand(self, row):
        oid = row.commit.oid
        files = self._files_cache.get(oid)
        if files is not None:
            self._apply_expand(oid, files)
            return
        if oid in self._files_loading:
            return
        self._files_loading.add(oid)
        self.info.setText("加载文件列表…")
        path = self.path
        self.runner.submit(lambda: gitcore.get_commit_files(path, oid),
                           lambda fs: self._on_files_loaded(oid, fs),
                           lambda e: self._on_files_error(oid, e))

    def _on_files_loaded(self, oid, files):
        self._files_loading.discard(oid)
        if not self._alive:
            return
        self._files_cache[oid] = files
        doc = self.docs.current_doc()                       # 去掉“加载文件列表…”的提示
        self.info.setText(doc.info if doc is not None else "")
        if self._src == ("commit", oid) and doc is None:
            c = self.model.row_at(self.model.index_of(oid).row())
            if isinstance(c, Row):
                self.info.setText(commit_info_html(c.commit))
        self._apply_expand(oid, files)

    def _on_files_error(self, oid, err):
        self._files_loading.discard(oid)
        if self._alive:
            self.info.setText(f"<span style='color:#f14c4c'>出错了：{html.escape(str(err))}</span>")

    def _apply_expand(self, oid, files):
        idx = self.model.index_of(oid)
        if not idx.isValid():
            return
        self.model.expand(oid, files)
        self._expanded.add(oid)
        if not files:
            self.info.setText(self.info.text() + "<br><span style='color:#9a9a9a'>这个提交没有文件改动。</span>")
            return
        last = self.model.index(self.model.index_of(oid).row() + len(files), 0)
        self.graph.scrollTo(last, QAbstractItemView.ScrollHint.EnsureVisible)    # 展开后让文件清单露出来

    def _collapse(self, row, select_commit: bool = False):
        oid = row.commit.oid
        in_files = self._src == ("commit", oid) and self._sel_file is not None
        self.model.collapse(oid)
        self._expanded.discard(oid)
        if in_files or select_commit:       # 选中的文件行被收起了：选回提交行
            idx = self.model.index_of(oid)
            if idx.isValid():
                self.graph.setCurrentIndex(idx)

    # ================================================================== 文件历史
    def open_file_search(self):
        """Ctrl+F：搜索当前项目里的文件。git 模式：选中后查看它的历史；file 模式：选中后打开预览。"""
        path = self.path
        if self._work_mode == "file":
            self.runner.submit(lambda: fileutil.walk_files(path), self._on_file_list_open, None)
            return
        self.runner.submit(lambda: gitcore.list_files(path), self._on_file_list,
                           lambda e: self.info.setText(f"出错了：{html.escape(str(e))}"))

    def _on_file_list_open(self, files):
        if not self._alive:
            return
        dlg = PickerDialog("打开文件 — 搜索文件", [(f, f) for f in files], self.window())
        if dlg.exec() and dlg.value:
            self.open_file(os.path.join(self.path, dlg.value))

    def _on_file_list(self, files):
        if not self._alive:
            return
        dlg = PickerDialog("查看文件历史 — 搜索文件", [(f, f) for f in files], self.window())
        if dlg.exec() and dlg.value:
            self.show_history(dlg.value)

    def show_history(self, path: str):
        self._hist_path = path
        self.left_stack.setCurrentIndex(1)
        self._load_history(path, quiet=False)

    def _load_history(self, path: str, quiet: bool):
        reselect = self._src[1] if (quiet and self._src and self._src[0] == "hist") else None
        if self._hist_task is not None:
            self.runner.cancel(self._hist_task)
            self._hist_task = None
        self._hist_gen += 1
        gen = self._hist_gen
        self.hist_title.setText(f"文件历史：{path}")
        if not quiet:
            self.hist_list.blockSignals(True)
            self.hist_list.clear()
            self.hist_list.addItem("加载中…")
            self.hist_list.blockSignals(False)
        repo = self.path
        self._hist_task = self.runner.submit(
            lambda: gitcore.get_file_history(repo, path),
            lambda es: self._on_history(gen, es, reselect),
            lambda e: self._on_history_error(gen, e),
        )

    def _on_history(self, gen, entries, reselect):
        if not self._alive or gen != self._hist_gen:
            return
        self.hist_list.blockSignals(True)
        self.hist_list.clear()
        target = None
        for e in entries:
            c = e.commit
            text = f"{c.subject}\n{c.short} · {c.author} · {fmt_time(c.time)}"
            if e.old_path:
                text += f"\n重命名自 {e.old_path}"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, e)
            if e.status == "D":
                it.setForeground(QBrush(QColor(STATUS_COLORS["D"])))
            self.hist_list.addItem(it)
            if reselect and c.oid == reselect:
                target = it
        if not entries:
            self.hist_list.addItem("没有历史记录（文件可能还没有提交过）。")
        elif target is not None:
            self.hist_list.setCurrentItem(target)
        self.hist_list.blockSignals(False)

    def _on_history_error(self, gen, err):
        if not self._alive or gen != self._hist_gen:
            return
        self.hist_list.clear()
        self.hist_list.addItem(f"出错了：{err}")

    def _hist_entry_doc(self, e):
        from .gitcore import CommitFile
        self._open_commit_file(e.commit, CommitFile(e.status, e.path, e.old_path))

    def _on_hist_clicked(self, item):
        if item is None:
            return
        e = item.data(Qt.ItemDataRole.UserRole)
        if e is None:
            return
        self._src = ("hist", e.commit.oid)
        self._sel_file = None
        self._clear_changes_selection()
        self._clear_graph_selection()
        self._hist_entry_doc(e)

    def close_history(self):
        if self._hist_task is not None:
            self.runner.cancel(self._hist_task)
            self._hist_task = None
        self.left_stack.setCurrentIndex(0)
        self._hist_path = None

    # ================================================================== 给主窗口用
    def set_diff_mode(self, mode: str):
        self.docs.set_mode(mode)

    def reload_docs(self):
        self.docs.reload_all()
        if self.file_docs is not None:
            self.file_docs.reload_all()

    # ================================================================== 工作模式：git / file
    def active_docs(self):
        """当前工作模式下的文档标签页（Ctrl+W、关闭、切换标签都作用在它上面）。"""
        if self._work_mode == "file" and self.file_docs is not None:
            return self.file_docs
        return self.docs

    def set_work_mode(self, mode: str):
        self._work_mode = mode
        if mode == "file":
            self._ensure_file_ui()
        idx = 1 if (mode == "file" and self.file_panel is not None) else 0
        self.left_modes.setCurrentIndex(idx)
        self.right_modes.setCurrentIndex(idx)

    def _ensure_file_ui(self):
        if self.file_panel is not None:
            return
        self.file_panel = FileTreePanel(self.path)
        self.file_panel.fileActivated.connect(self.open_file)
        self.left_modes.addWidget(self.file_panel)
        self.file_docs = DocArea(
            self.runner, self.options, FileDocument,
            "在左侧的文件树里点一个文件，在这里预览。\n\n可以同时打开多个文件；\n用标签上的 ×、鼠标中键或 Ctrl+W 关闭。")
        self.right_modes.addWidget(self.file_docs)

    def open_file(self, abs_path: str):
        """file 模式：在新标签里预览文件（已经开着就切过去）。"""
        if self.file_docs is None:
            return
        rel = os.path.relpath(abs_path, self.path).replace("\\", "/")
        self.file_docs.open_doc(f"f|{rel}", os.path.basename(abs_path), rel, "file",
                                lambda o: fileutil.read_text_file(abs_path, o.encoding), "",
                                data=abs_path)

    def dispose(self):
        self._alive = False
        if self._branch_task is not None:
            self.runner.cancel(self._branch_task)
            self._branch_task = None
