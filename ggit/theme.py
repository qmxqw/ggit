"""深色主题与各处用到的颜色。

界面各部分的样式表统一写在这里的 _STYLE 里，按控件的 objectName 匹配；
各模块只给控件起名字（setObjectName），不再各自 setStyleSheet。
"""
import sys
from string import Template

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# 界面底色 / 文字颜色（样式表和代码里共用）
BAR_BG = "#2d2d30"          # 标题栏、工具条
PANEL_BG = "#252528"        # 状态行、说明栏
BORDER = "#38383c"
EDITOR_BORDER = "#3c3c3c"
TEXT_HEADER = "#bbbbbb"     # 区块标题
TEXT_MUTED = "#8a8a8a"      # 提示文字、次要信息
TEXT_DIM = "#9a9a9a"
ACCENT = "#0e7ad1"
ERROR = "#f14c4c"

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

_STYLE = Template("""
QToolTip { color: #d4d4d4; background: #3c3c3c; border: 1px solid #555; }
QTabBar::tab {
    padding: 6px 14px; margin-right: 2px;
    background: $BAR_BG; color: #b0b0b0;
    border: 1px solid $EDITOR_BORDER; border-bottom: none;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected { background: #1e1e1e; color: #ffffff; border-top: 2px solid $ACCENT; }
QTabBar::tab:hover:!selected { background: #37373d; color: #e0e0e0; }
QSplitter::handle { background: #3a3a3d; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }
QTreeWidget, QListWidget, QListView { outline: 0; }
QToolButton { padding: 3px 8px; }

/* 标题栏 / 工具条 */
QFrame#topBar, QFrame#sectionHeader, QFrame#docBar, QFrame#diffBar,
QFrame#fileBar, QFrame#fileTreeHeader { background: $BAR_BG; }
QFrame#topBar QToolButton::menu-indicator { image: none; width: 0px; }
QFrame#projectStatusBar, QFrame#projectInfoBar { background: $PANEL_BG; border-bottom: 1px solid $BORDER; }

/* 文字 */
QLabel#sectionTitle { color: $TEXT_HEADER; }
QLabel#placeholder { color: $TEXT_MUTED; font-size: 13px; }
QLabel#emptyPage { color: $TEXT_MUTED; font-size: 14px; }
QLabel#metaText { color: $TEXT_MUTED; }
QLabel#fileInfo { color: $TEXT_DIM; }
QLabel#splitCaption { color: $TEXT_MUTED; background: #252526; }

/* 左右对照：左边编辑器右侧一条分隔线 */
QPlainTextEdit#splitLeft { border: none; border-right: 1px solid $EDITOR_BORDER; }

/* 左上角的分支按钮 */
QToolButton#branchButton {
    background: #36363c; color: #ffffff; font-weight: bold;
    border-radius: 4px; padding: 3px 8px; border: 1px solid #484850;
}
QToolButton#branchButton:hover { background: #44444c; border-color: $ACCENT; }
QToolButton#branchButton::menu-indicator { image: none; }

/* 拖动项目标签时的占位槽 */
QWidget#dragPlaceholder {
    background: rgba(255, 255, 255, 0.05); border: 1px dashed $ACCENT; border-radius: 2px;
}
""").substitute(BAR_BG=BAR_BG, PANEL_BG=PANEL_BG, BORDER=BORDER, EDITOR_BORDER=EDITOR_BORDER,
                TEXT_HEADER=TEXT_HEADER, TEXT_MUTED=TEXT_MUTED, TEXT_DIM=TEXT_DIM, ACCENT=ACCENT)


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
