"""主窗口：顶部项目导航（前 10 个项目）+ 项目页面区；常驻后台（托盘图标 + 全局热键 Alt+G）。

切换项目：鼠标点击顶部大标签；Ctrl+1 … Ctrl+9、Ctrl+0 对应前 10 个；
Ctrl+Tab / Ctrl+Shift+Tab 循环切换；Ctrl+P 搜索切换（含第 10 个之后的项目）。
窗口的 × 只是隐藏到后台；Alt+G（全局）显示到最前面 / 隐藏；托盘菜单或 Ctrl+Q 才是真正退出。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QStackedWidget, QSystemTrayIcon,
                               QToolButton, QVBoxLayout, QWidget)

from . import __version__, config, gitcore, winhotkey
from .async_runner import AsyncRunner
from .icon import app_icon
from .options import ViewOptions
from .ui_dialogs import PickerDialog
from .ui_diff import MODE_SPLIT, MODE_UNIFIED
from .ui_nav import ProjectNav
from .ui_project import ProjectView

MAX_TABS = 10


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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ggit")
        self.setWindowIcon(app_icon())
        self.resize(1440, 880)
        self.runner = AsyncRunner(self)
        self.options = ViewOptions()
        self.projects: list = []
        self.views: dict = {}          # 路径 -> ProjectView
        self.badges: dict = {}         # 路径 -> Badge
        self.current = -1
        self._last_badge_refresh = 0.0
        self._quitting = False
        self._tip_shown = False

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
            if isinstance(opts.get("font_size"), int) and 6 <= opts["font_size"] <= 28:
                self.options.font_size = opts["font_size"]

        if self.options.font_size != 9:
            self._apply_font_size(self.options.font_size)

        self._build_ui()
        self._build_menu()
        self._build_shortcuts()
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
        QApplication.instance().applicationStateChanged.connect(self._on_app_state)

        # 全局热键 Alt+G：显示到最前面 / 隐藏到后台
        self.hotkey = winhotkey.GlobalHotkey(self.toggle_visibility)
        if not self.hotkey.register() and winhotkey.IS_WIN:
            self.statusBar().showMessage("全局热键 Alt+G 注册失败（可能被其它程序占用）", 8000)

    # ================================================================== 搭界面
    def _build_ui(self):
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setStyleSheet("QFrame#topBar{background:#2d2d30;} QFrame#topBar QToolButton::menu-indicator{image:none;width:0px;}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 8, 0)
        h.setSpacing(6)
        self.nav = ProjectNav()
        self.nav.selected.connect(self.switch_to)
        self.nav.reordered.connect(self.move_project)
        h.addWidget(self.nav, 1)

        self.more_menu = QMenu(self)
        self.more_btn = QToolButton()
        self.more_btn.setText("全部项目 ▾")
        self.more_btn.setToolTip("所有项目（也可以按 Ctrl+P 搜索切换）")
        self.more_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
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
        self.empty_page.setStyleSheet("color:#8a8a8a; font-size:14px;")
        self.stack.addWidget(self.empty_page)
        v.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ctrl+1…0 切换项目 · F5 刷新 · Alt+F 切换 git / 文件模式 · Alt+D 切换 diff 模式 · Alt+G 显示/隐藏")

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

        hm = mb.addMenu("帮助(&H)")
        self._act(hm, "关于 ggit", None, self._about)

    def _build_shortcuts(self):
        for i in range(MAX_TABS):
            sc = QShortcut(QKeySequence("Ctrl+%d" % ((i + 1) % 10)), self)
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
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        self._tray_menu = menu
        menu.addAction("显示 / 隐藏   (Alt+G)").triggered.connect(lambda _=False: self.toggle_visibility())
        menu.addAction("刷新当前项目").triggered.connect(lambda _=False: self.refresh_current())
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
    def _update_button(self, i: int):
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
            view = ProjectView(entry.path, entry.name, self.runner, self.options)
            view.statusChanged.connect(self._on_view_status)
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
        try:
            top = gitcore.toplevel(d)
        except gitcore.GitError as e:
            QMessageBox.warning(self, "不是 Git 仓库", f"{d}\n\n{e}")
            return
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
            path = e.path
            self.runner.submit(lambda p=path: gitcore.get_status(p),
                               lambda st, p=path: self._on_badge(p, badge_from_status(st)),
                               lambda err, p=path: self._on_badge(p, Badge(error=True)))

    def _on_badge(self, path, badge):
        self.badges[path] = badge
        self._update_button(self._index_of(path))

    def _on_app_state(self, state):
        """窗口重新被激活（比如你在别的程序里操作完切回来）：自动刷新。"""
        if state == Qt.ApplicationState.ApplicationActive:
            v = self.current_view()
            if v is not None:
                v.refresh()
            self._refresh_badges()

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
            f"<b>ggit {__version__}</b><br>多项目 Git 只读查看工具。<br><br>"
            "显示 / 隐藏（全局）：Alt+G<br>切换 git / 文件工作模式：Alt+F<br>"
            "切换项目：Ctrl+1…0 / Ctrl+Tab / Ctrl+P<br>刷新：F5<br>分支列表：Ctrl+B（或点左上角的分支名）<br>文件历史：Ctrl+F<br>"
            "切换 diff 模式：Alt+D<br>上一处 / 下一处改动：F3 / F4<br>"
            "关闭文档：Ctrl+W")

    # ================================================================== 字体缩放
    def change_font_size(self, delta: int) -> None:
        new_size = max(7, min(24, self.options.font_size + delta))
        if new_size == self.options.font_size:
            return
        self.options.font_size = new_size
        self._apply_font_size(new_size)
        self._save_config()
        self.statusBar().showMessage(f"全局字体大小已调整为: {new_size} pt (按 Ctrl +/- 调节)", 2500)

    def reset_font_size(self) -> None:
        if self.options.font_size == 9:
            return
        self.options.font_size = 9
        self._apply_font_size(9)
        self._save_config()
        self.statusBar().showMessage("全局字体大小已恢复为默认 (9 pt)", 2500)

    def _apply_font_size(self, size: int) -> None:
        app = QApplication.instance()
        if app is not None:
            f = app.font()
            f.setPointSize(size)
            app.setFont(f)

        # 1. 全局与全部子控件级联应用新字号
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)
        for w in self.findChildren(QWidget):
            from PySide6.QtWidgets import QPlainTextEdit
            if isinstance(w, QPlainTextEdit):
                continue
            w.setFont(font)
            w.update()

        # 2. 顶部导航
        if hasattr(self, "nav"):
            self.nav.setFont(font)
            self.nav.update()

        # 3. 项目视图与提交树、更改列表
        for view in self.views.values():
            view.setFont(font)
            if hasattr(view, "model"):
                view.model.layoutChanged.emit()
            if hasattr(view, "graph"):
                view.graph.viewport().update()
            if hasattr(view, "changes"):
                view.changes.viewport().update()

            # 4. 当前已打开的文档实时重新排版正文与行号
            for doc_area in (getattr(view, "docs", None), getattr(view, "file_docs", None)):
                if doc_area is not None:
                    for doc in list(doc_area._docs.values()):
                        if hasattr(doc.view, "apply_font_size"):
                            doc.view.apply_font_size(size)

    # ================================================================== 保存
    def _save_config(self):
        cur = self.projects[self.current].path if 0 <= self.current < len(self.projects) else None
        config.save({
            "projects": [{"path": p.path, "name": p.name} for p in self.projects],
            "current": cur,
            "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
            "options": {"ignore_ws": self.options.ignore_ws,
                        "ignore_eol": self.options.ignore_eol,
                        "encoding": self.options.encoding,
                        "diff_mode": self.options.diff_mode,
                        "work_mode": self.options.work_mode,
                        "font_size": self.options.font_size},
        })
