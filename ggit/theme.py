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
TEXT = "#cccccc"            # 和 VS Code 一样不用接近纯白的字，看久了不刺眼
TEXT_BRIGHT = "#ffffff"
TEXT_HEADER = "#bbbbbb"     # 区块标题
TEXT_MUTED = "#8a8a8a"      # 提示文字、次要信息
TEXT_DIM = "#9a9a9a"
TEXT_FAINT = "#808080"
ACCENT = "#0e7ad1"
SELECTION = "#04395e"           # 选中行：深一点的蓝，不像亮蓝那样一直晃眼
SELECTION_INACTIVE = "#37373d"  # 窗口不在前台时的选中行：灰色

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


# ---------------------------------------------------------------- 悬停提示
# 照 VS Code 的悬停框：深底细边框；全部常规字重，层次靠颜色和留白，不加粗、不用纯白
TIP_BG = "#252526"
TIP_BORDER = "#454545"      # 边框，也是提示框里的分隔线
TIP_TEXT = TEXT             # 正文
TIP_TITLE = "#e6e6e6"       # 提交标题：只比正文亮一点
TIP_TEXT_DIM = "#9d9d9d"    # 次要信息：提交时间、尾注（Co-Authored-By 等）、项目符号、截断提示
TIP_LINK = "#4daafc"        # 作者、提交 id、邮箱
TIP_CODE = "#d7ba7d"        # 正文里 `反引号` 括起来的代码


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """两种颜色按比例混合：t=0 是 a，t=1 是 b。"""
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


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

# 引用标签（提交行右侧、悬停提示底部）：提亮的同色字 + 淡淡的同色底；
# 不用饱和的实心底配加粗白字，每一行都在抢眼睛
REF_BRANCH = QColor("#59a4f9")  # 本地分支、分离的 HEAD（当前分支底色更实一点）
REF_REMOTE = QColor("#b180d7")
REF_TAG = QColor("#d7ba7d")
REF_MORE = QColor("#9d9d9d")    # “+3” 这种放不下的引用


# 远端分支的图标：Windows 10 起自带的线条图标字体里的云朵（提交图、悬停提示、分支菜单、分支选择框共用）。
# 不用 ☁ 字符：它是从符号字体借来的实心大团，和细字放在一起很难看
ICON_FONT = "Segoe MDL2 Assets"
CLOUD = ""


def with_icon_font(font: QFont) -> QFont:
    """把图标字体补在字体后面当后备：图标（私用区字符）用它画，其它字符不受影响。"""
    f = QFont(font)
    f.setFamilies([*font.families(), ICON_FONT])
    return f


def ref_chip(color: QColor, current: bool = False) -> tuple[QColor, QColor, QColor]:
    """引用标签的 (文字, 底色, 边框)：底色和边框是半透明的同色；current：当前分支。"""
    fill, border = QColor(color), QColor(color)
    fill.setAlphaF(0.3 if current else 0.16)
    border.setAlphaF(0.6 if current else 0.35)
    return mix(color, QColor("#ffffff"), 0.35), fill, border

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
/* 悬停提示。Qt 还会把“边框 + 左内边距”再加一遍当作文字四周的留白，所以实际留白比这里写的大 */
QToolTip { color: $TIP_TEXT; background: $TIP_BG; border: 1px solid $TIP_BORDER; padding: 2px 4px; }
/* 提交图的悬停提示（自己做的提示框，贴在正文区左侧） */
QLabel#commitTip { color: $TIP_TEXT; background: $TIP_BG; border: 1px solid $TIP_BORDER; padding: 8px 10px; }
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
        TIP_BG=TIP_BG, TIP_BORDER=TIP_BORDER, TIP_TEXT=TIP_TEXT,
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
    p.setColor(R.Highlight, QColor(SELECTION))
    p.setColor(R.HighlightedText, QColor(TEXT_BRIGHT))
    p.setColor(G.Inactive, R.Highlight, QColor(SELECTION_INACTIVE))
    p.setColor(G.Inactive, R.HighlightedText, QColor(TEXT))
    p.setColor(R.ToolTipBase, QColor(TIP_BG))
    p.setColor(R.ToolTipText, QColor(TIP_TEXT))
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
