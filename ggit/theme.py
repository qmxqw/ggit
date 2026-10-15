"""深色主题与各处用到的颜色。"""
import sys

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# 提交图：分支线颜色（按颜色编号循环）
LANE_COLORS = ["#4fc1ff", "#c586c0", "#dcdcaa", "#4ec9b0",
               "#ce9178", "#9cdcfe", "#b5cea8", "#d7ba7d"]

# 引用标签（分支/远端/标签）
REF_HEAD = QColor("#1f6feb")
REF_LOCAL = QColor("#0e639c")
REF_REMOTE = QColor("#6f42c1")
REF_TAG = QColor("#7d6b1e")

# diff：红底 = 删除，绿底 = 新增
DEL_BG = QColor(94, 33, 33)
ADD_BG = QColor(31, 82, 46)
HUNK_BG = QColor(37, 49, 66)
HUNK_FG = QColor("#8fb4e8")
GUTTER_BG = QColor("#252526")
GUTTER_FG = QColor("#7d7d7d")
GUTTER_FG_CHANGED = QColor("#c8c8c8")

_STYLE = """
QToolTip { color: #d4d4d4; background: #3c3c3c; border: 1px solid #555; }
QTabBar::tab {
    padding: 6px 14px; margin-right: 2px;
    background: #2d2d30; color: #b0b0b0;
    border: 1px solid #3c3c3c; border-bottom: none;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected { background: #1e1e1e; color: #ffffff; border-top: 2px solid #0e7ad1; }
QTabBar::tab:hover:!selected { background: #37373d; color: #e0e0e0; }
QSplitter::handle { background: #3a3a3d; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }
QTreeWidget, QListWidget, QListView { outline: 0; }
QToolButton { padding: 3px 8px; }
"""


def lane_color(i: int) -> QColor:
    return QColor(LANE_COLORS[i % len(LANE_COLORS)])


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    if sys.platform == "win32":
        app.setFont(QFont("Microsoft YaHei UI", 9))
    p = QPalette()
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    p.setColor(R.Window, QColor("#252526"))
    p.setColor(R.WindowText, QColor("#d4d4d4"))
    p.setColor(R.Base, QColor("#1e1e1e"))
    p.setColor(R.AlternateBase, QColor("#252526"))
    p.setColor(R.Text, QColor("#d4d4d4"))
    p.setColor(R.Button, QColor("#333337"))
    p.setColor(R.ButtonText, QColor("#d4d4d4"))
    p.setColor(R.Highlight, QColor("#094771"))
    p.setColor(R.HighlightedText, QColor("#ffffff"))
    p.setColor(R.ToolTipBase, QColor("#3c3c3c"))
    p.setColor(R.ToolTipText, QColor("#d4d4d4"))
    p.setColor(R.PlaceholderText, QColor("#808080"))
    p.setColor(R.Link, QColor("#4fc1ff"))
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(G.Disabled, role, QColor("#6e6e6e"))
    app.setPalette(p)
    app.setStyleSheet(_STYLE)
