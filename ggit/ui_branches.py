"""分支下拉列表（点左上角的分支名或 Ctrl+B）：列出所有本地分支和远端分支，选一个就只看它的历史。

列表在后台读取（每次项目状态刷新后重读一次），打开菜单时用最近一次的结果。
在分支上点右键：切换、合并、变基、新建分支、删除。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import gitcore, theme
from .async_runner import AsyncRunner
from .gitcore import BranchInfo
from .utils import ago


class BranchMenu(QMenu):
    scopeChosen = Signal(str)       # gitcore.SCOPE_ALL（显示全部分支），或要单独查看的分支的完整引用名
    branchActionRequested = Signal(str, object)     # 右键菜单：(操作, BranchInfo)，操作见 branch_actions

    def __init__(self, runner: AsyncRunner, repo: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("branchMenu")
        self._runner = runner
        self._repo = repo
        self._branches: list[BranchInfo] = []
        self._task: int | None = None
        self._loading = False
        self._alive = True
        self._scope = gitcore.SCOPE_ALL       # 提交图当前显示的范围（决定哪一项打勾）
        self._action_branch: dict[QAction, BranchInfo] = {}
        self.aboutToShow.connect(self.rebuild)

    # ---------------------------------------------------------------- 对外接口
    def set_scope(self, scope: str) -> None:
        self._scope = scope

    def branches(self) -> list[BranchInfo]:
        return list(self._branches)

    def set_branches(self, branches: list[BranchInfo]) -> None:
        self._branches = list(branches)
        if self.isVisible():
            self.rebuild()

    @property
    def loading(self) -> bool:
        return self._loading

    def refresh(self) -> None:
        """后台重新读取分支列表（还在读上一次的：作废它，按最新状态重读）。"""
        if not self._alive:
            return
        if self._task is not None:
            self._runner.cancel(self._task)
        self._loading = True
        repo = self._repo
        self._task = self._runner.submit(lambda: gitcore.get_branches(repo), self._on_loaded, self._on_error)

    def dispose(self) -> None:
        self._alive = False
        if self._task is not None:
            self._runner.cancel(self._task)
            self._task = None

    # ---------------------------------------------------------------- 内部
    def _on_loaded(self, branches: list[BranchInfo]) -> None:
        self._loading = False
        self._task = None
        if self._alive:
            self.set_branches(branches)

    def _on_error(self, _err: BaseException) -> None:
        self._loading = False
        self._task = None

    def _apply_style(self) -> None:
        base = QApplication.font()
        self.setFont(theme.with_icon_font(base))    # 远端分支前面的云朵图标
        base_size = base.pointSizeF() if base.pointSizeF() > 0 else float(base.pointSize())
        # 字号比常规大 25%，行高与内边距宽阔，方便点选
        self.setStyleSheet(theme.branch_menu_css(max(12, round(base_size * 1.25)),
                                                 max(7, round(base_size * 0.7))))

    def _add_choice(self, text: str, value: str, checked: bool, branch: BranchInfo | None = None) -> None:
        act = self.addAction(text)
        act.setCheckable(True)
        act.setChecked(checked)
        act.triggered.connect(lambda _=False, v=value: self.scopeChosen.emit(v))
        if branch is not None:
            self._action_branch[act] = branch

    @staticmethod
    def branch_actions(b: BranchInfo) -> list[tuple[str, str]]:
        """一个分支的右键菜单：[(操作, 显示文字)]。"""
        if b.kind == "local" and b.is_current:
            return [("branch", "基于此新建分支…")]
        if b.kind == "local":
            return [("switch", f"切换到 {b.name}"), ("merge", "合并到当前分支…"), ("rebase", "把当前分支变基到此…"),
                    ("branch", "基于此新建分支…"), ("delete", "删除分支…")]
        return [("switch", "检出为本地分支并切换"), ("merge", "合并到当前分支…"), ("rebase", "把当前分支变基到此…"),
                ("branch", "基于此新建分支…"), ("delete", "删除远端分支…")]

    def contextMenuEvent(self, e: QContextMenuEvent) -> None:
        b = self._action_branch.get(self.actionAt(e.pos()))
        if b is None:
            return
        menu = QMenu(self)
        acts = {menu.addAction(text): op for op, text in self.branch_actions(b)}
        chosen = menu.exec(e.globalPos())
        if chosen in acts:
            self.close()
            self.branchActionRequested.emit(acts[chosen], b)

    def _add_title(self, text: str) -> None:
        self.addSeparator()
        self.addAction(text).setEnabled(False)

    def rebuild(self) -> None:
        self._apply_style()
        self.clear()
        self._action_branch.clear()
        self._add_choice("★   显示全部分支（本地 + 远端 + 标签）", gitcore.SCOPE_ALL,
                         self._scope == gitcore.SCOPE_ALL)

        if not self._branches:
            if self._loading:
                self.addAction("正在加载分支列表…").setEnabled(False)
            else:
                self.refresh()
            return

        local = [b for b in self._branches if b.kind == "local"]
        if local:
            self._add_title("── 本地分支 ──")
            for b in local:
                meta = []
                if b.upstream:
                    meta.append("上游已失效" if b.gone else f"↑{b.ahead} ↓{b.behind} → {b.upstream}")
                if b.time:
                    meta.append(ago(b.time))
                text = f"{'● ' if b.is_current else '⎇  '}{b.name}" + (f"  ({' · '.join(meta)})" if meta else "")
                active = self._scope == "ref:" + b.ref or (b.is_current and self._scope == gitcore.SCOPE_AUTO)
                self._add_choice(text, b.ref, active, b)

        remote = [b for b in self._branches if b.kind == "remote"]
        if remote:
            self._add_title("── 远端分支 ──")
            for b in remote:
                text = f"{theme.CLOUD}  {b.name}" + (f"  ({ago(b.time)})" if b.time else "")
                self._add_choice(text, b.ref, self._scope == "ref:" + b.ref, b)
