"""深色主题：界面上用到的全部颜色和样式表都在这里。

  * 颜色常量：样式表、自绘控件（提交图、顶部标签、diff 滚动条）、HTML 片段共用
  * stylesheet(font_pt)：整个程序的样式表，按控件的 objectName 匹配；各模块只给控件起名字，
    不再各自 setStyleSheet。提示文字的字号跟着全局字号走，所以字号变了要重新生成。
"""
from __future__ import annotations

import sys
from string import Template

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from .options import FONT_DEFAULT

# ---------------------------------------------------------------- 底色 / 边框 / 文字
WINDOW_BG = "#252526"       # 窗口底色
EDITOR_BG = "#1e1e1e"       # 编辑器、列表的底色
BAR_BG = "#2d2d30"          # 标题栏、工具条
PANEL_BG = "#252528"        # 状态行、说明栏
BORDER = "#38383c"
EDITOR_BORDER = "#3c3c3c"
TEXT = "#d4d4d4"
TEXT_BRIGHT = "#ffffff"
TEXT_HEADER = "#bbbbbb"     # 区块标题
TEXT_MUTED = "#8a8a8a"      # 提示文字、次要信息
TEXT_DIM = "#9a9a9a"
TEXT_FAINT = "#808080"
ACCENT = "#0e7ad1"

# ---------------------------------------------------------------- 语义色
ADDED = "#73c991"           # 新增 / 领先 / 已同步
DELETED = "#f14c4c"         # 删除
MODIFIED = "#e2c08d"        # 修改 / 有未提交改动 / 警告
RENAMED = "#4fc1ff"
CONFLICT = "#ff8800"
BEHIND = "#ff9e64"          # 落后远端
ADDED_SOFT = "rgba(115,201,145,0.18)"    # 徽标底色：领先
BEHIND_SOFT = "rgba(255,158,100,0.18)"   # 徽标底色：落后
ERROR = "#f14c4c"
REF_TEXT = "#7aa2f7"        # 说明栏里的分支 / 标签名

# 文件状态字母的颜色
STATUS_COLORS = {"M": MODIFIED, "A": ADDED, "D": DELETED, "R": RENAMED,
                 "U": ADDED, "C": CONFLICT, "T": MODIFIED}


def status_color(letter: str) -> str:
    return STATUS_COLORS.get(letter, TEXT)


# ---------------------------------------------------------------- 顶部项目标签
NAV_BG = BAR_BG
NAV_BG_HOVER = "#3a3a40"
NAV_BG_CHECKED = EDITOR_BG
NAV_BG_DRAGGING = "#333338"
NAV_BORDER = EDITOR_BORDER
NAV_KEY_BG = "#4a4a50"      # 快捷键序号的底色（当前项目用 ACCENT）

# ---------------------------------------------------------------- 提交图
LANE_COLORS = ["#4fc1ff", "#c586c0", "#dcdcaa", "#4ec9b0",
               "#ce9178", "#9cdcfe", "#b5cea8", "#d7ba7d"]
ROW_HOVER = QColor(255, 255, 255, 20)
FILE_ICON = TEXT_DIM
FILE_ICON_SELECTED = "#dddddd"
REF_HEAD = QColor("#1f6feb")
REF_LOCAL = QColor("#0e639c")
REF_REMOTE = QColor("#6f42c1")
REF_TAG = QColor("#7d6b1e")
REF_MORE = QColor("#555555")    # “+3” 这种放不下的引用

# ---------------------------------------------------------------- diff
DEL_BG = QColor(94, 33, 33)
ADD_BG = QColor(31, 82, 46)
HUNK_BG = QColor(37, 49, 66)
HUNK_FG = QColor("#8fb4e8")
GUTTER_BG = QColor("#252526")
GUTTER_FG = QColor("#7d7d7d")
GUTTER_FG_CHANGED = QColor("#c8c8c8")
FILL_BG = QColor("#262628")             # 左右对照里补出来的空行
MARKER_BAR_BG = QColor("#1b1b1c")       # 加宽滚动条的底色
DEL_MARK = QColor(241, 76, 76, 220)     # 滚动条上的改动色块
ADD_MARK = QColor(78, 201, 123, 220)

MARKER_SCROLLBAR_CSS = """
QScrollBar:vertical { background: transparent; width: 18px; margin: 0px; border: none; }
QScrollBar::handle:vertical { background: rgba(190,190,190,70); min-height: 28px;
                              border: 1px solid rgba(255,255,255,90); margin: 0px; }
QScrollBar::handle:vertical:hover { background: rgba(210,210,210,110); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; border: none; background: none; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


def lane_color(i: int) -> QColor:
    return QColor(LANE_COLORS[i % len(LANE_COLORS)])


# ---------------------------------------------------------------- 样式表
_STYLE = Template("""
QToolTip { color: $TEXT; background: #3c3c3c; border: 1px solid #555; }
QTabBar::tab {
    padding: 6px 14px; margin-right: 2px;
    background: $BAR_BG; color: #b0b0b0;
    border: 1px solid $EDITOR_BORDER; border-bottom: none;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected { background: $EDITOR_BG; color: $TEXT_BRIGHT; border-top: 2px solid $ACCENT; }
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

/* 文字：提示文字的字号跟着全局字号走 */
QLabel#sectionTitle { color: $TEXT_HEADER; }
QLabel#placeholder { color: $TEXT_MUTED; font-size: ${PLACEHOLDER_PT}pt; }
QLabel#emptyPage { color: $TEXT_MUTED; font-size: ${EMPTY_PAGE_PT}pt; }
QLabel#metaText { color: $TEXT_MUTED; }
QLabel#fileInfo { color: $TEXT_DIM; }
QLabel#splitCaption { color: $TEXT_MUTED; background: $WINDOW_BG; }
QLabel#pickerHint { color: $TEXT_MUTED; }

/* 左右对照：左边编辑器右侧一条分隔线 */
QPlainTextEdit#splitLeft { border: none; border-right: 1px solid $EDITOR_BORDER; }

/* 左上角的分支按钮 */
QToolButton#branchButton {
    background: #36363c; color: $TEXT_BRIGHT; font-weight: bold;
    border-radius: 4px; padding: 3px 8px; border: 1px solid #484850;
}
QToolButton#branchButton:hover { background: #44444c; border-color: $ACCENT; }
QToolButton#branchButton::menu-indicator { image: none; }

/* 状态行的 Fetch / Pull / Push、继续 / 中止 */
QToolButton#syncButton {
    background: #333338; color: $TEXT; border: 1px solid #45454c; border-radius: 4px; padding: 2px 8px;
}
QToolButton#syncButton:hover { border-color: $ACCENT; background: #3c3c44; }
QToolButton#syncButton:disabled { color: #6e6e6e; border-color: #3a3a3f; }

/* “更改”下面的提交框 */
QFrame#commitBox { background: $PANEL_BG; border-top: 1px solid $BORDER; }
QPushButton#commitButton {
    background: $ACCENT; color: $TEXT_BRIGHT; border: none; border-radius: 4px; padding: 4px 18px;
}
QPushButton#commitButton:hover { background: #1c8ae0; }
QPushButton#commitButton:disabled { background: #3a3a3f; color: #6e6e6e; }

/* 命令面板 */
QPlainTextEdit#consoleOutput { border: none; border-top: 1px solid $BORDER; }
QWidget#consoleInputRow { background: $BAR_BG; }
QLabel#consolePrompt { color: $REF_TEXT; font-weight: bold; }
QLineEdit#consoleInput { border: 1px solid $EDITOR_BORDER; background: $EDITOR_BG; padding: 2px 4px; }
QLineEdit#consoleInput:focus { border-color: $ACCENT; }

/* 拖动项目标签时的占位槽 */
QWidget#dragPlaceholder {
    background: rgba(255, 255, 255, 0.05); border: 1px dashed $ACCENT; border-radius: 2px;
}
""")


def stylesheet(font_pt: int = FONT_DEFAULT) -> str:
    """整个程序的样式表。font_pt：当前的全局字号（提示文字比它大一点）。"""
    return _STYLE.substitute(
        TEXT=TEXT, TEXT_BRIGHT=TEXT_BRIGHT, TEXT_HEADER=TEXT_HEADER, TEXT_MUTED=TEXT_MUTED,
        TEXT_DIM=TEXT_DIM, BAR_BG=BAR_BG, PANEL_BG=PANEL_BG, WINDOW_BG=WINDOW_BG, EDITOR_BG=EDITOR_BG,
        BORDER=BORDER, EDITOR_BORDER=EDITOR_BORDER, ACCENT=ACCENT, REF_TEXT=REF_TEXT,
        PLACEHOLDER_PT=font_pt + 1, EMPTY_PAGE_PT=font_pt + 2)


def branch_menu_css(font_px: int, padding_v: int) -> str:
    """分支下拉菜单的样式（字号、行高按当前字体算）。"""
    return f"""
        QMenu#branchMenu {{
            background-color: {PANEL_BG}; border: 1px solid #3c3c40;
            border-radius: 6px; padding: 6px 4px;
        }}
        QMenu#branchMenu::item {{
            padding: {padding_v}px 18px; border-radius: 4px; color: #e0e0e0;
            font-size: {font_px}px; margin: 2px 4px;
        }}
        QMenu#branchMenu::item:selected {{ background-color: {ACCENT}; color: {TEXT_BRIGHT}; }}
        QMenu#branchMenu::item:disabled {{
            color: #727278; font-weight: bold; padding: 6px 14px 2px 14px;
        }}
        QMenu#branchMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
    """


# ---------------------------------------------------------------- 应用到程序
def apply_dark_theme(app: QApplication, font_pt: int = FONT_DEFAULT) -> None:
    app.setStyle("Fusion")
    if sys.platform == "win32":
        app.setFont(QFont("Microsoft YaHei UI", font_pt))
    p = QPalette()
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    p.setColor(R.Window, QColor(WINDOW_BG))
    p.setColor(R.WindowText, QColor(TEXT))
    p.setColor(R.Base, QColor(EDITOR_BG))
    p.setColor(R.AlternateBase, QColor(WINDOW_BG))
    p.setColor(R.Text, QColor(TEXT))
    p.setColor(R.Button, QColor("#333337"))
    p.setColor(R.ButtonText, QColor(TEXT))
    p.setColor(R.Highlight, QColor("#094771"))
    p.setColor(R.HighlightedText, QColor(TEXT_BRIGHT))
    p.setColor(R.ToolTipBase, QColor("#3c3c3c"))
    p.setColor(R.ToolTipText, QColor(TEXT))
    p.setColor(R.PlaceholderText, QColor(TEXT_FAINT))
    p.setColor(R.Link, QColor(RENAMED))
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(G.Disabled, role, QColor("#6e6e6e"))
    app.setPalette(p)
    app.setStyleSheet(stylesheet(font_pt))


def apply_font_size(app: QApplication, font_pt: int) -> None:
    """全局字号变了：改程序字体，并按新字号重新生成样式表。"""
    f = app.font()
    f.setPointSize(font_pt)
    app.setFont(f)
    app.setStyleSheet(stylesheet(font_pt))
