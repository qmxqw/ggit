"""各个面板共用的小部件和 HTML 片段。"""
from __future__ import annotations

import html

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from . import theme
from .gitcore import Commit
from .utils import fmt_time


def section_header(text: str) -> tuple[QFrame, QLabel, QHBoxLayout]:
    """区块标题栏（加粗的标题）。返回 (框架, 标题标签, 布局)，布局右侧可以再放控件。"""
    frame = QFrame()
    frame.setObjectName("sectionHeader")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(8, 4, 6, 4)
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    f = label.font()
    f.setBold(True)
    label.setFont(f)
    lay.addWidget(label)
    lay.addStretch(1)
    return frame, label, lay


def colored(text: str, color: str, *, bold: bool = False) -> str:
    """一段带颜色的 HTML 文字。text 必须是已经转义过的 HTML。"""
    weight = " font-weight:bold;" if bold else ""
    return f"<span style='color:{color};{weight}'>{text}</span>"


def pill(text: str, color: str, background: str) -> str:
    """带底色的小徽标（状态行里的 ↑n / ↓n）。"""
    return (f"<span style='background:{background}; color:{color}; padding:2px 7px; "
            f"border-radius:3px; font-weight:bold;'>{text}</span>")


def commit_info_html(c: Commit) -> str:
    """说明栏里一个提交的介绍：标题 + 短 id、作者、时间、引用、是否合并提交。"""
    parts = [colored(f"{c.short} · {html.escape(c.author)} · {fmt_time(c.time)}", theme.TEXT_DIM)]
    if c.refs:
        parts.append(colored(html.escape("  ".join(r.name for r in c.refs)), theme.REF_TEXT))
    if len(c.parents) > 1:
        parts.append(colored("合并提交", theme.TEXT_DIM))
    return f"<b>{html.escape(c.subject)}</b><br>{' · '.join(parts)}"
