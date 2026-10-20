"""项目页面左上角的状态行。

  第 1 行：分支按钮（点开是分支列表）+ 同步徽标（↑ 领先 / ↓ 落后 / 已同步 / 本地独有）
  第 2 行：上游分支、上次 fetch 时间、本地 / 远端分支数
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from . import theme
from .gitcore import Snapshot
from .ui_common import colored, pill
from .utils import ago, fmt_time


class ProjectStatusBar(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectStatusBar")
        sv = QVBoxLayout(self)
        sv.setContentsMargins(10, 6, 10, 6)
        sv.setSpacing(3)

        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(8)
        self.branch_btn = QToolButton()
        self.branch_btn.setObjectName("branchButton")
        self.branch_btn.setText("⎇ … ▾")
        self.branch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.branch_btn.setToolTip("点击展开分支列表切换查看（本地 / 远端）  (Ctrl+B)")
        self.branch_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        r1.addWidget(self.branch_btn)
        self.sync_badge = QLabel()
        r1.addWidget(self.sync_badge)
        r1.addStretch(1)
        sv.addWidget(row1)

        self.meta = QLabel("加载中…")
        self.meta.setObjectName("metaText")
        self.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sv.addWidget(self.meta)

    def set_branch_menu(self, menu: QMenu) -> None:
        self.branch_btn.setMenu(menu)

    def show_branch_menu(self) -> None:
        self.branch_btn.showMenu()

    def show_snapshot(self, snap: Snapshot, name: str, path: str) -> None:
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
            badges.append(pill(f"↑ {s.ahead}", theme.ADDED, theme.ADDED_SOFT))
        if s.behind:
            badges.append(pill(f"↓ {s.behind}", theme.BEHIND, theme.BEHIND_SOFT))
        if not s.ahead and not s.behind and s.upstream:
            badges.append(colored("✔ 已同步", theme.ADDED, bold=True))
        elif not s.upstream and s.branch:
            badges.append(colored("本地独有", theme.TEXT_MUTED))
        self.sync_badge.setText("  ".join(badges))

        n_local = sum(1 for r in snap.refs if r.startswith("refs/heads/"))
        n_remote = sum(1 for r in snap.refs if r.startswith("refs/remotes/") and not r.endswith("/HEAD"))
        meta = []
        if s.upstream:
            meta.append(f"→ {html.escape(s.upstream)}")
        meta.append(f"fetch: {ago(s.last_fetch)}" if s.last_fetch else "未 fetch")
        meta.append(f"本地 {n_local} · 远端 {n_remote}")
        self.meta.setText("   ·   ".join(meta))

        tip = [f"项目：{name}", f"路径：{path}"]
        if s.branch:
            tip.append(f"分支：{s.branch}")
        if s.upstream:
            tip.append(f"上游：{s.upstream} (领先 {s.ahead}，落后 {s.behind})")
        if s.last_fetch:
            tip.append(f"上次 fetch：{fmt_time(s.last_fetch)}")
        tip.append(f"统计：本地 {n_local} 个分支，远端 {n_remote} 个分支")
        tip.append("提示：按 F5 刷新状态；Ctrl+B 查看分支列表")
        self.sync_badge.setToolTip("\n".join(tip))
        self.meta.setToolTip("\n".join(tip))

    def show_error(self, message: str) -> None:
        """读取项目状态失败。"""
        self.sync_badge.setText(colored("读取失败", theme.ERROR, bold=True))
        self.show_problem(message)

    def show_problem(self, message: str) -> None:
        """在第 2 行显示一条错误（比如提交图读取失败）；下次刷新成功时会被正常内容替换。"""
        self.meta.setText(colored(html.escape(message), theme.ERROR))
        self.meta.setToolTip(message)
