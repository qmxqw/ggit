"""主窗口：顶部项目导航（前 10 个项目）+ 项目页面区；常驻后台（托盘图标 + 全局热键 Alt+G）。

切换项目：鼠标点击顶部大标签；Ctrl+1 … Ctrl+9、Ctrl+0 对应前 10 个；
Ctrl+Tab / Ctrl+Shift+Tab 循环切换；Ctrl+P 搜索切换（含第 10 个之后的项目）。
窗口的 × 只是隐藏到后台；Alt+G（全局）显示到最前面 / 隐藏；托盘菜单或 Ctrl+Q 才是真正退出。
“Git”菜单：Fetch / Pull / Push、分支、提交、储藏、命令面板（Ctrl+`）；窗口隐藏时写操作的结果用托盘通知。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, config, gitcore, gitops, theme, winhotkey
from .async_runner import AsyncRunner
from .icon import app_icon
from .options import FONT_DEFAULT, FONT_MAX, FONT_MIN, ViewOptions
from .ui_dialogs import PickerDialog
from .ui_diff import MODE_SPLIT, MODE_UNIFIED
from .ui_nav import ProjectNav
from .ui_project import ProjectView

MAX_TABS = 10


def _inside_code_view(w: QWidget) -> bool:
    node: QWidget | None = w
    while node is not None:
        if isinstance(node, QPlainTextEdit):
            return True
        node = node.parentWidget()
    return False


class ProjectEntry:
    def __init__(self, path: str, name: str | None = None):
        self.path = path
        self.name = name or os.path.basename(path.rstrip("\\/")) or path


class Badge:
    """顶部标签第二行要显示的内容。"""

    def __init__(self, branch="", dirty=False, ahead=0, behind=0, error=False):
        self.branch, self.dirty, self.ahead, self.behind, self.error = branch, dirty, ahead, behind, error


def badge_from_status(s) -> Badge:
    if s.branch:
        branch = s.branch
    elif s.head_oid:
        branch = f"HEAD {s.head_oid[:7]}"
    else:
        branch = "尚无提交"
    return Badge(branch, s.dirty, s.ahead, s.behind)


class MainWindow(QMainWindow):
    def __init__(self, *, system_integration: bool = True):
        """system_integration=False：不建托盘图标、不注册全局热键（测试用，免得影响真实桌面）。"""
        super().__init__()
        self.setWindowTitle("ggit")
        self.setWindowIcon(app_icon())
        self.resize(1440, 880)
        self.runner = AsyncRunner(self)
        self.options = ViewOptions()
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)
        QApplication.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, False)
        self.projects: list = []
        self.views: dict = {}          # 路径 -> ProjectView
        self.badges: dict = {}         # 路径 -> Badge
        self._badge_gen: dict = {}     # 路径 -> 最近一次标签状态读取的序号（丢弃过期结果）
        self.current = -1
        self._last_badge_refresh = 0.0
        self._quitting = False
        self._nav_stale = False        # 拖动顶部标签期间有被推迟的标签更新
        self._save_error = ""          # 上一次保存配置失败的原因（同一个错误只提示一次）
        self._tip_shown = False
        self._cmd_history: dict = {}   # 路径 -> 命令面板的历史命令（还没打开过的项目也要原样保存）
        self._fetch_all: dict | None = None    # “Fetch 全部项目”进行中：{"left": 剩几个, "failed": [项目名]}

        cfg = config.load()
        for p in cfg.get("projects", []):
            if isinstance(p, dict) and p.get("path"):
                self.projects.append(ProjectEntry(p["path"], p.get("name")))
        opts = cfg.get("options", {})
        if isinstance(opts, dict):
            self.options.ignore_ws = bool(opts.get("ignore_ws", False))
            self.options.ignore_eol = bool(opts.get("ignore_eol", False))
            if opts.get("encoding") in (gitcore.ENC_AUTO, gitcore.ENC_UTF8, gitcore.ENC_GBK):
                self.options.encoding = opts["encoding"]
            if opts.get("diff_mode") in (MODE_UNIFIED, MODE_SPLIT):
                self.options.diff_mode = opts["diff_mode"]
            if opts.get("work_mode") in ("git", "file"):
                self.options.work_mode = opts["work_mode"]
            if isinstance(opts.get("font_size"), int):
                self.options.font_size = max(FONT_MIN, min(FONT_MAX, opts["font_size"]))
            if opts.get("pull_mode") in gitops.PULL_MODES:
                self.options.pull_mode = opts["pull_mode"]
        hist = cfg.get("cmd_history")
        if isinstance(hist, dict):
            self._cmd_history = {k: v for k, v in hist.items() if isinstance(k, str) and isinstance(v, list)}

        if self.options.font_size != FONT_DEFAULT:
            self._apply_font_size(self.options.font_size)

        self._build_ui()
        self._build_menu()
        self._build_shortcuts()
        self.tray: QSystemTrayIcon | None = None
        if system_integration:
            self._build_tray()

        geo = cfg.get("geometry")
        if isinstance(geo, str):
            self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii", "ignore")))

        cur_path = cfg.get("current")
        self.current = next((i for i, p in enumerate(self.projects) if p.path == cur_path),
                            0 if self.projects else -1)
        self.rebuild_nav()
        if self.current >= 0:
            self.switch_to(self.current)
        self._refresh_badges(force=True)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.applicationStateChanged.connect(self._on_app_state)

        # 全局热键 Alt+G：显示到最前面 / 隐藏到后台
        self.hotkey = winhotkey.GlobalHotkey(self.toggle_visibility)
        if system_integration and not self.hotkey.register() and winhotkey.IS_WIN:
            self.statusBar().showMessage("全局热键 Alt+G 注册失败（可能被其它程序占用）", 8000)

    # ================================================================== 搭界面
    def _build_ui(self):
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("topBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 8, 0)
        h.setSpacing(6)
        self.nav = ProjectNav()
        self.nav.selected.connect(self.switch_to)
        self.nav.reordered.connect(self.move_project)
        self.nav.dragFinished.connect(self._on_nav_drag_finished)
        h.addWidget(self.nav, 1)

        self.more_menu = QMenu(self)
        self.more_btn = QToolButton()
        self.more_btn.setText("全部项目 ▾")
        self.more_btn.setToolTip("所有项目（也可以按 Ctrl+P 搜索切换）")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_btn.setMenu(self.more_menu)
        h.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        add_btn = QToolButton()
        add_btn.setText("＋ 添加项目")
        add_btn.setToolTip("添加项目 (Ctrl+O)")
        add_btn.clicked.connect(self.add_project)
        h.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addWidget(bar)

        self.stack = QStackedWidget()
        self.empty_page = QLabel("还没有项目。\n\n点击右上角“＋ 添加项目”，或按 Ctrl+O，选择一个 Git 项目目录。")
        self.empty_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_page.setObjectName("emptyPage")
        self.stack.addWidget(self.empty_page)
        v.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ctrl+1…0 切换项目 · F5 刷新 · Alt+F 切换 git / 文件模式 · "
                                     "Alt+D 切换 diff 模式 · Alt+G 显示/隐藏")

    def _act(self, menu, text, shortcut, slot, checkable=False, checked=False):
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        if checkable:
            act.setCheckable(True)
            act.setChecked(checked)
            act.toggled.connect(slot)
        else:
            act.triggered.connect(lambda _=False: slot())
        menu.addAction(act)
        return act

    def _build_menu(self):
        mb = self.menuBar()
        m = mb.addMenu("项目(&P)")
        self._act(m, "添加项目…", "Ctrl+O", self.add_project)
        self._act(m, "移除当前项目", None, self.remove_current)
        m.addSeparator()
        self._act(m, "快速切换项目…", "Ctrl+P", self.quick_switch)
        self._act(m, "当前项目左移", "Alt+Shift+Left", lambda: self.move_current(-1))
        self._act(m, "当前项目右移", "Alt+Shift+Right", lambda: self.move_current(1))
        m.addSeparator()
        self._act(m, "刷新当前项目", "F5", self.refresh_current)
        self._act(m, "刷新全部项目状态", "Ctrl+Shift+R", lambda: self._refresh_badges(force=True))
        m.addSeparator()
        self._act(m, "隐藏到后台（Alt+G 再显示）", None, self.hide)
        self._act(m, "退出 ggit", "Ctrl+Q", self.quit_app)

        v = mb.addMenu("查看(&V)")
        self._act(v, "切换工作模式（git / 文件）", "Alt+F", self.toggle_work_mode)
        self._act(v, "切换 diff 模式（上下合并 / 左右对照）", "Alt+D", self.toggle_diff_mode)
        self._act(v, "上一处改动", "F3", lambda: self._goto_change(False))
        self._act(v, "下一处改动", "F4", lambda: self._goto_change(True))
        v.addSeparator()
        self._act(v, "分支列表…（所有本地 / 远端分支）", "Ctrl+B", self.branch_picker)
        self._act(v, "查看文件历史…（搜索文件）", "Ctrl+F", self.file_search)
        v.addSeparator()
        self._act(v, "关闭当前文档", "Ctrl+W", lambda: self._docs(lambda d: d.close_current()))
        self._act(v, "关闭全部文档", "Ctrl+Shift+W", lambda: self._docs(lambda d: d.close_all()))
        self._act(v, "上一个文档", "Ctrl+PgUp", lambda: self._docs(lambda d: d.step(-1)))
        self._act(v, "下一个文档", "Ctrl+PgDown", lambda: self._docs(lambda d: d.step(1)))
        v.addSeparator()
        self._act(v, "忽略空白差异", None, self._set_ws, True, self.options.ignore_ws)
        self._act(v, "忽略换行符差异（CRLF / LF）", None, self._set_eol, True, self.options.ignore_eol)
        enc = v.addMenu("文件编码")
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for label, val in (("自动检测（先 UTF-8，再 GBK）", gitcore.ENC_AUTO),
                           ("UTF-8", gitcore.ENC_UTF8), ("GBK", gitcore.ENC_GBK)):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(self.options.encoding == val)
            a.triggered.connect(lambda _=False, x=val: self._set_encoding(x))
            grp.addAction(a)
            enc.addAction(a)

        v.addSeparator()
        self._act(v, "放大字体", "Ctrl+=", lambda: self.change_font_size(1))
        self._act(v, "缩小字体", "Ctrl+-", lambda: self.change_font_size(-1))
        self._act(v, "重置字体大小", "Ctrl+Shift+0", self.reset_font_size)

        g = mb.addMenu("Git(&G)")
        self._act(g, "Fetch（获取所有远端）", "Ctrl+Shift+F", lambda: self._ops(lambda o: o.fetch()))
        self._act(g, "Pull（拉取）", "Ctrl+Shift+L", lambda: self._ops(lambda o: o.pull()))
        self._act(g, "Pull（rebase 方式）", None, lambda: self._ops(lambda o: o.pull(gitops.PULL_REBASE)))
        self._act(g, "Pull（merge 方式）", None, lambda: self._ops(lambda o: o.pull(gitops.PULL_MERGE)))
        self._act(g, "Push（推送）", "Ctrl+Shift+K", lambda: self._ops(lambda o: o.push()))
        self._act(g, "强制推送（--force-with-lease）…", None, lambda: self._ops(lambda o: o.push(force=True)))
        self._act(g, "Fetch 全部项目", None, self.fetch_all)
        g.addSeparator()
        self._act(g, "切换分支…", None, lambda: self._ops(
            lambda o: o.pick_branch("切换分支", o.switch_branch)))
        self._act(g, "新建分支…", None, lambda: self._ops(lambda o: o.create_branch("HEAD", "当前提交")))
        self._act(g, "合并分支到当前分支…", None, lambda: self._ops(
            lambda o: o.pick_branch("合并分支到当前分支", o.merge_branch)))
        self._act(g, "删除分支…", None, lambda: self._ops(
            lambda o: o.pick_branch("删除分支", o.delete_branch)))
        g.addSeparator()
        self._act(g, "提交…（填写提交说明）", "Ctrl+K", self.focus_commit)
        self._act(g, "暂存全部改动", None, lambda: self._ops(lambda o: o.run(["add", "-A"], "暂存全部")))
        g.addSeparator()
        self._act(g, "储藏改动（stash）…", None, lambda: self._ops(lambda o: o.stash_push()))
        self._act(g, "恢复最近的储藏（stash pop）", None, lambda: self._ops(lambda o: o.stash_pop()))
        self._act(g, "储藏列表…", None, lambda: self._ops(lambda o: o.stash_browse()))
        g.addSeparator()
        self._act(g, "继续合并 / 变基", None, lambda: self._ops(lambda o: o.continue_operation()))
        self._act(g, "中止合并 / 变基…", None, lambda: self._ops(lambda o: o.abort_operation()))
        g.addSeparator()
        pm = g.addMenu("Pull 的默认方式")
        pgrp = QActionGroup(self)
        pgrp.setExclusive(True)
        for label, val in (("只快进（--ff-only，有分叉时报错，推荐）", gitops.PULL_FF_ONLY),
                           ("变基（--rebase）", gitops.PULL_REBASE), ("合并（--no-rebase）", gitops.PULL_MERGE)):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(self.options.pull_mode == val)
            a.triggered.connect(lambda _=False, x=val: self._set_pull_mode(x))
            pgrp.addAction(a)
            pm.addAction(a)
        g.addSeparator()
        self._act(g, "命令面板（输入 git 命令）", "Ctrl+`", self.toggle_console)

        hm = mb.addMenu("帮助(&H)")
        self._act(hm, "关于 ggit", None, self._about)

    def _build_shortcuts(self):
        for i in range(MAX_TABS):
            sc = QShortcut(QKeySequence(f"Ctrl+{(i + 1) % 10}"), self)
            sc.activated.connect(lambda i=i: self.switch_to(i))
        nxt = QShortcut(QKeySequence("Ctrl+Tab"), self)
        nxt.activated.connect(lambda: self._cycle(1))
        prv = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        prv.activated.connect(lambda: self._cycle(-1))
        # 放大字体额外支持 Ctrl++（含数字小键盘与按住 Shift 的情况）
        sc_plus = QShortcut(QKeySequence("Ctrl++"), self)
        sc_plus.activated.connect(lambda: self.change_font_size(1))

    # ================================================================== 托盘 / 后台
    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        self._tray_menu = menu
        menu.addAction("显示 / 隐藏   (Alt+G)").triggered.connect(lambda _=False: self.toggle_visibility())
        menu.addAction("刷新当前项目").triggered.connect(lambda _=False: self.refresh_current())
        menu.addAction("Fetch 全部项目").triggered.connect(lambda _=False: self.fetch_all())
        menu.addSeparator()
        menu.addAction("退出 ggit").triggered.connect(lambda _=False: self.quit_app())
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("ggit — Alt+G 显示 / 隐藏")
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_visibility()

    def toggle_visibility(self):
        """Alt+G：窗口在前台就隐藏到后台；否则显示到所有窗口最前面。"""
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.hide()
        else:
            self.bring_to_front()

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        winhotkey.bring_to_front(int(self.winId()))

    def quit_app(self):
        self._quitting = True
        self.close()

    def closeEvent(self, event):
        if self.tray is not None and not self._quitting:     # 点 ×：只是隐藏到后台
            event.ignore()
            self.hide()
            self._save_config()
            if not self._tip_shown:
                self._tip_shown = True
                self.tray.showMessage("ggit 仍在后台运行", "按 Alt+G 随时显示 / 隐藏；右键托盘图标可退出。",
                                      QSystemTrayIcon.MessageIcon.Information, 4000)
            return
        self._save_config()
        self.hotkey.unregister()
        for v in self.views.values():
            v.dispose()
        self.runner.shutdown()
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(event)
        QApplication.quit()

    # ================================================================== 顶部导航
    def _on_nav_drag_finished(self):
        if self._nav_stale:             # 拖动期间有状态更新被推迟了：现在补上
            self._nav_stale = False
            self.rebuild_nav()

    def _update_button(self, i: int):
        if self.nav.dragging:           # 拖动中按钮顺序和项目列表对不上：等拖完再更新
            self._nav_stale = True
            return
        b = self.nav.button(i)
        if b is None or i >= len(self.projects):
            return
        e = self.projects[i]
        bd = self.badges.get(e.path)
        key = str((i + 1) % 10)
        tip_lines = [f"项目：{e.name}", f"路径：{e.path}"]
        if bd is not None:
            if bd.error:
                tip_lines.append("状态：无法读取项目状态")
            else:
                branch_str = bd.branch or "HEAD 分离"
                status_parts = []
                if bd.dirty:
                    status_parts.append("● 有未提交改动")
                if bd.ahead:
                    status_parts.append(f"↑ 领先远端 {bd.ahead} 个提交")
                if bd.behind:
                    status_parts.append(f"↓ 落后远端 {bd.behind} 个提交")
                if not status_parts:
                    status_parts.append("✔ 工作区干净，与远端同步")
                tip_lines.append(f"分支：{branch_str}")
                tip_lines.append(f"状态：{' · '.join(status_parts)}")
        tip = "\n".join(tip_lines)

        if bd is None:
            b.set_info(key, e.name, "", False, 0, 0, False, False, tip)
        else:
            b.set_info(key, e.name, bd.branch, bd.dirty, bd.ahead, bd.behind, bd.error, True, tip)

    def rebuild_nav(self):
        n = min(MAX_TABS, len(self.projects))
        self.nav.rebuild(n)
        for i in range(n):
            self._update_button(i)
        self.more_menu.clear()
        for j, e in enumerate(self.projects):
            act = self.more_menu.addAction(f"{(j + 1) % 10 if j < MAX_TABS else '·'}   {e.name}")
            act.setCheckable(True)
            act.setChecked(j == self.current)
            act.triggered.connect(lambda _=False, k=j: self.switch_to(k))
        self._sync_nav()

    def _sync_nav(self):
        self.nav.set_current(self.current if 0 <= self.current < MAX_TABS else -1)
        if MAX_TABS <= self.current < len(self.projects):
            self.more_btn.setText(f"{self.projects[self.current].name} ▾")
        else:
            self.more_btn.setText("全部项目 ▾")
        for j, act in enumerate(self.more_menu.actions()):
            act.setChecked(j == self.current)

    def _update_title(self):
        if 0 <= self.current < len(self.projects):
            self.setWindowTitle(f"ggit - {self.projects[self.current].name}")
        else:
            self.setWindowTitle("ggit")

    # ================================================================== 切换项目
    def current_view(self):
        w = self.stack.currentWidget()
        return w if isinstance(w, ProjectView) else None

    def _docs(self, fn):
        v = self.current_view()
        if v is not None:
            fn(v.active_docs())          # 当前工作模式（git / file）下的标签页

    def switch_to(self, index: int):
        if not (0 <= index < len(self.projects)):
            self._sync_nav()
            return
        self.current = index
        entry = self.projects[index]
        view = self.views.get(entry.path)
        if view is None:
            view = ProjectView(entry.path, entry.name, self.runner, self.options,
                               history=self._cmd_history.get(entry.path))
            view.statusChanged.connect(self._on_view_status)
            view.opFinished.connect(self._on_op_finished)
            view.modeToggleRequested.connect(self.toggle_diff_mode)
            view.set_work_mode(self.options.work_mode)
            self.stack.addWidget(view)
            self.views[entry.path] = view
            view.refresh(force=True)
        else:
            view.refresh()              # 切回来时顺手刷新一次（1 秒内重复请求会被忽略）
        self.stack.setCurrentWidget(view)
        self._sync_nav()
        self._update_title()
        self.statusBar().showMessage(entry.path)
        self._save_config()

    def _cycle(self, step: int):
        if self.projects:
            self.switch_to((self.current + step) % len(self.projects))

    def quick_switch(self):
        if not self.projects:
            return
        items = [(f"{e.name}    {e.path}", e.path) for e in self.projects]
        dlg = PickerDialog("快速切换项目", items, self, "输入项目名或路径…")
        if dlg.exec() and dlg.value:
            for i, e in enumerate(self.projects):
                if e.path == dlg.value:
                    self.switch_to(i)
                    return

    # ================================================================== 项目增删移
    def add_project(self):
        self.bring_to_front()
        start = self.projects[self.current].path if 0 <= self.current < len(self.projects) else ""
        d = QFileDialog.getExistingDirectory(self, "选择 Git 项目目录", start)
        if not d:
            return
        # 放到后台：目录在慢速网络盘上时，git 可能要好几秒才返回，不能卡住界面
        self.statusBar().showMessage(f"正在检查：{d} …")
        self.runner.submit(lambda: gitcore.toplevel(d),
                           self.add_project_path,
                           lambda e: self._add_project_failed(d, e))

    def _add_project_failed(self, d: str, err) -> None:
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "不是 Git 仓库", f"{d}\n\n{err}")

    def add_project_path(self, top: str) -> None:
        """添加一个项目（top：仓库根目录）；已经在列表里就切过去。"""
        self.statusBar().clearMessage()
        for i, p in enumerate(self.projects):
            if os.path.normcase(p.path) == os.path.normcase(top):
                self.switch_to(i)
                return
        self.projects.append(ProjectEntry(top))
        self.current = len(self.projects) - 1
        self.rebuild_nav()
        self.switch_to(self.current)
        self._refresh_badges(force=True)

    def remove_current(self):
        if not (0 <= self.current < len(self.projects)):
            return
        e = self.projects[self.current]
        ok = QMessageBox.question(
            self, "移除项目", f"从 ggit 中移除“{e.name}”？\n（不会删除磁盘上的任何文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._fetch_all_one(e.path, e.name, False)     # 正在“Fetch 全部项目”：不再等这个项目
        self._cmd_history.pop(e.path, None)
        view = self.views.pop(e.path, None)
        if view is not None:
            view.dispose()
            self.stack.removeWidget(view)
            view.deleteLater()
        self.badges.pop(e.path, None)
        self.projects.pop(self.current)
        self.current = min(self.current, len(self.projects) - 1)
        self.rebuild_nav()
        if self.current >= 0:
            self.switch_to(self.current)
        else:
            self.stack.setCurrentWidget(self.empty_page)
            self._update_title()
            self._save_config()

    def move_project(self, from_idx: int, to_idx: int) -> None:
        if not (0 <= from_idx < len(self.projects) and 0 <= to_idx < len(self.projects)):
            return
        if from_idx == to_idx:
            return
        item = self.projects.pop(from_idx)
        self.projects.insert(to_idx, item)
        if self.current == from_idx:
            self.current = to_idx
        elif from_idx < self.current <= to_idx:
            self.current -= 1
        elif to_idx <= self.current < from_idx:
            self.current += 1
        self.rebuild_nav()
        self._save_config()

    def move_current(self, step: int):
        self.move_project(self.current, self.current + step)

    # ================================================================== 刷新
    def refresh_current(self):
        v = self.current_view()
        if v is not None:
            v.refresh(force=True)
            self.statusBar().showMessage("已刷新：" + v.path, 3000)

    def _index_of(self, path: str) -> int:
        return next((i for i, p in enumerate(self.projects) if p.path == path), -1)

    def _on_view_status(self, view):
        if view.error:
            self.badges[view.path] = Badge(error=True)
        elif view.snap is not None:
            self.badges[view.path] = badge_from_status(view.snap.status)
        else:
            return
        self._badge_gen[view.path] = self._badge_gen.get(view.path, 0) + 1    # 作废还没回来的旧标签读取
        self._update_button(self._index_of(view.path))

    def _refresh_badges(self, force: bool = False):
        """后台读取顶部各项目的状态（分支、未提交改动、ahead/behind），更新标签上的显示。"""
        now = time.time()
        if not force and now - self._last_badge_refresh < 5:
            return
        self._last_badge_refresh = now
        cur = self.projects[self.current].path if 0 <= self.current < len(self.projects) else None
        for e in self.projects[:MAX_TABS]:
            if e.path == cur and e.path in self.views:
                continue                # 当前项目由它自己的页面刷新
            self._read_badge(e.path)

    def _read_badge(self, path: str) -> None:
        gen = self._badge_gen[path] = self._badge_gen.get(path, 0) + 1
        # 标签上只需要知道“有没有改动”，未跟踪目录不必逐个文件展开
        self.runner.submit(lambda: gitcore.get_status(path, untracked="normal"),
                           lambda st: self._on_badge(path, gen, badge_from_status(st)),
                           lambda _err: self._on_badge(path, gen, Badge(error=True)))

    def _on_badge(self, path, gen, badge):
        if self._badge_gen.get(path) != gen:
            return                      # 已经有更新的结果（更晚的一次读取，或项目页面自己刷新过）
        self.badges[path] = badge
        self._update_button(self._index_of(path))

    def _on_app_state(self, state):
        """窗口重新被激活（比如你在别的程序里操作完切回来）：自动刷新。"""
        if state == Qt.ApplicationState.ApplicationActive:
            v = self.current_view()
            if v is not None:
                v.refresh()
            self._refresh_badges()

    # ================================================================== Git 写操作
    def _ops(self, fn):
        v = self.current_view()
        if v is not None:
            fn(v.ops)

    def toggle_console(self):
        v = self.current_view()
        if v is not None:
            v.toggle_console()

    def focus_commit(self):
        v = self.current_view()
        if v is None:
            return
        if self.options.work_mode != "git":
            self.toggle_work_mode()
        v.focus_commit()

    def _set_pull_mode(self, mode: str):
        self.options.pull_mode = mode
        self._save_config()

    def _on_op_finished(self, view, label: str, ok: bool, detail: str):
        if self._fetch_all is not None and label == "Fetch" and view.path in self._fetch_all["pending"]:
            self._fetch_all_one(view.path, view.name, ok)
            return
        text = f"{view.name}：{label} {'完成' if ok else '失败'}" + ("" if ok else f" — {detail}")
        self.statusBar().showMessage(text, 8000)
        if self.tray is not None and not self.isVisible():     # 窗口在后台：用托盘通知结果
            icon = QSystemTrayIcon.MessageIcon.Information if ok else QSystemTrayIcon.MessageIcon.Warning
            self.tray.showMessage(f"ggit · {view.name}", f"{label} {'完成' if ok else '失败'}\n{detail}", icon, 5000)

    def fetch_all(self):
        """对所有项目执行 fetch（同时最多 4 个）；打开过的项目输出写到各自的命令面板。"""
        if self._fetch_all is not None:
            self.statusBar().showMessage("“Fetch 全部项目”还在进行中…", 4000)
            return
        if not self.projects:
            return
        self._fetch_all = {"pending": set(), "failed": [], "total": len(self.projects)}
        for e in self.projects:
            self._fetch_all["pending"].add(e.path)
        for e in list(self.projects):
            view = self.views.get(e.path)
            if view is not None:
                if not view.ops.fetch():                    # 这个项目正在执行别的操作
                    self._fetch_all_one(e.path, e.name, False)
                continue
            self._fetch_in_background(e.path, e.name)
        if self._fetch_all is not None:
            self.statusBar().showMessage(f"正在 fetch 全部 {len(self.projects)} 个项目…")

    def _fetch_in_background(self, path: str, name: str) -> None:
        """还没打开过的项目：不经过命令面板，直接在后台 fetch。"""
        def work(_emit) -> gitops.OpResult:
            return gitops.run_git_stream(path, gitops.fetch_args())

        def done(res: gitops.OpResult) -> None:
            self._fetch_all_one(path, name, res.ok)

        def failed(_err: BaseException) -> None:
            self._fetch_all_one(path, name, False)

        self.runner.submit_stream(work, None, done, failed)

    def _fetch_all_one(self, path: str, name: str, ok: bool):
        st = self._fetch_all
        if st is None or path not in st["pending"]:
            return
        st["pending"].discard(path)
        if not ok:
            st["failed"].append(name)
        if path not in self.views:
            self._read_badge(path)
        if st["pending"]:
            return
        self._fetch_all = None
        failed = st["failed"]
        if failed:
            msg = f"Fetch 全部项目：{st['total'] - len(failed)} 个成功，{len(failed)} 个失败（{'、'.join(failed)}）"
        else:
            msg = f"Fetch 全部项目：{st['total']} 个项目都已完成"
        self.statusBar().showMessage(msg, 10000)
        if self.tray is not None and not self.isVisible():
            self.tray.showMessage("ggit", msg, QSystemTrayIcon.MessageIcon.Information, 5000)

    # ================================================================== 菜单动作
    def branch_picker(self):
        v = self.current_view()
        if v is not None:
            v.open_branch_picker()

    def file_search(self):
        v = self.current_view()
        if v is not None:
            v.open_file_search()

    def _goto_change(self, forward: bool):
        self._docs(lambda d: d.goto_change(forward))

    def toggle_work_mode(self):
        """Alt+F：在 git 模式（提交图 + diff）和 file 模式（文件树 + 文件预览）之间切换。"""
        self.options.work_mode = "file" if self.options.work_mode == "git" else "git"
        for v in self.views.values():
            v.set_work_mode(self.options.work_mode)
        name = "文件（文件树 + 预览）" if self.options.work_mode == "file" else "git（提交图 + diff）"
        self.statusBar().showMessage(f"工作模式：{name}（Alt+F 切换）", 4000)
        self._save_config()

    def toggle_diff_mode(self):
        self.options.diff_mode = MODE_SPLIT if self.options.diff_mode == MODE_UNIFIED else MODE_UNIFIED
        for v in self.views.values():
            v.set_diff_mode(self.options.diff_mode)
        name = "左右对照" if self.options.diff_mode == MODE_SPLIT else "上下合并"
        self.statusBar().showMessage(f"Diff 模式：{name}（Alt+D 切换）", 4000)
        self._save_config()

    def _options_changed(self):
        for v in self.views.values():
            v.reload_docs()
        self._save_config()

    def _set_ws(self, checked: bool):
        self.options.ignore_ws = checked
        self._options_changed()

    def _set_eol(self, checked: bool):
        self.options.ignore_eol = checked
        self._options_changed()

    def _set_encoding(self, enc: str):
        self.options.encoding = enc
        self._options_changed()

    def _about(self):
        QMessageBox.about(
            self, "关于 ggit",
            f"<b>ggit {__version__}</b><br>多项目 Git 查看与操作工具。<br><br>"
            "显示 / 隐藏（全局）：Alt+G<br>切换 git / 文件工作模式：Alt+F<br>"
            "切换项目：Ctrl+1…0 / Ctrl+Tab / Ctrl+P<br>刷新：F5<br>"
            "分支列表：Ctrl+B（或点左上角的分支名）<br>文件历史：Ctrl+F<br>"
            "切换 diff 模式：Alt+D<br>上一处 / 下一处改动：F3 / F4<br>"
            "关闭文档：Ctrl+W<br><br>Fetch / Pull / Push：Ctrl+Shift+F / L / K<br>"
            "提交：Ctrl+K（说明框里 Ctrl+Enter 提交）<br>命令面板：Ctrl+`")

    # ================================================================== 字体缩放
    def change_font_size(self, delta: int) -> None:
        new_size = max(FONT_MIN, min(FONT_MAX, self.options.font_size + delta))
        if new_size == self.options.font_size:
            return
        self.options.font_size = new_size
        self._apply_font_size(new_size)
        self._save_config()
        self.statusBar().showMessage(f"全局字体大小已调整为: {new_size} pt (按 Ctrl +/- 调节)", 2500)

    def reset_font_size(self) -> None:
        if self.options.font_size == FONT_DEFAULT:
            return
        self.options.font_size = FONT_DEFAULT
        self._apply_font_size(FONT_DEFAULT)
        self._save_config()
        self.statusBar().showMessage(f"全局字体大小已恢复为默认 ({FONT_DEFAULT} pt)", 2500)

    def _apply_font_size(self, size: int) -> None:
        # 1. 改全局字体（没有单独设置过字体的控件会自动跟着变），并按新字号重新生成样式表
        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme.apply_font_size(app, size)

        # 2. 单独设置过字体的控件（比如加粗的区块标题）：只改字号，保留粗体等其它属性。
        #    代码视图（及其内部控件）用等宽字体，由下面的 apply_font_size 单独处理。
        for w in self.findChildren(QWidget):
            if not w.testAttribute(Qt.WidgetAttribute.WA_SetFont) or _inside_code_view(w):
                continue
            wf = w.font()
            wf.setPointSize(size)
            w.setFont(wf)

        # 3. 各项目页面：提交图重新排版，已打开的文档换代码字号
        for view in self.views.values():
            view.apply_font_size(size)

    # ================================================================== 保存
    def _save_config(self):
        cur = self.projects[self.current].path if 0 <= self.current < len(self.projects) else None
        for path, view in self.views.items():
            self._cmd_history[path] = view.command_history()
        known = {p.path for p in self.projects}
        history = {p: h for p, h in self._cmd_history.items() if p in known and h}
        err = config.save({
            "projects": [{"path": p.path, "name": p.name} for p in self.projects],
            "current": cur,
            "geometry": bytes(self.saveGeometry().toBase64().data()).decode("ascii"),
            "options": {"ignore_ws": self.options.ignore_ws,
                        "ignore_eol": self.options.ignore_eol,
                        "encoding": self.options.encoding,
                        "diff_mode": self.options.diff_mode,
                        "work_mode": self.options.work_mode,
                        "font_size": self.options.font_size,
                        "pull_mode": self.options.pull_mode},
            "cmd_history": history,
        })
        if err and err != self._save_error:      # 同一个错误只提示一次，免得每次切换项目都弹
            self.statusBar().showMessage(f"配置保存失败（{config.config_path()}）：{err}", 10000)
            if self.tray is not None and not self.isVisible():
                self.tray.showMessage("ggit 配置保存失败", err, QSystemTrayIcon.MessageIcon.Warning, 6000)
        self._save_error = err
