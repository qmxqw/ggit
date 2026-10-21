"""提交图：每个提交一行，左边画分支线和节点，右边是说明和分支/远端/标签标签。
点一个提交，它修改的文件就地展开在它下面（分支线继续穿过文件行），再点一次收起。"""
from __future__ import annotations

import html
import itertools
import re
from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QListView,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QToolTip,
    QWidget,
)

from . import theme
from .gitcore import Commit, Ref
from .graph import lane_metrics
from .items import FileRow, ItemList
from .ui_common import colored
from .utils import ago, fmt_time

ROW_H = 26
LANE_W = 14
PAD = 8
GRAPH_MAX_W = 10 * LANE_W   # 分支线区域最宽这么多；线多时压窄列宽
MIN_LANE_W = 5              # 列宽最窄压到这么多，再多的线才挤在最后一列
TOOLTIP_BODY_LINES = 30     # 悬停提示里最多显示多少行提交正文
TOOLTIP_MAX_W = 560         # 悬停提示最宽多少（逻辑像素），更长的行在这个宽度内折行；行太长读着累
TOOLTIP_GAP = 8             # 悬停提示里段落之间的间距
TOOLTIP_ITEM_GAP = 3        # 列表项之间的间距（比折行的行距稍大，一眼分得清是新的一项）
TOOLTIP_LINE_HEIGHT = 140   # 悬停提示的行高（%），比默认的松一点
TOOLTIP_INDENT = 16         # 列表的悬挂缩进：项目符号那一列的宽度
TIP_GAP = 4                 # 悬停提示和正文区左边的间隔
FILE_INDENT = 22    # 文件行相对提交说明再缩进多少

_LIST_ITEM = re.compile(r"^(\s*)([-*•]|\d{1,3}[.)])\s+(\S.*)$")       # “- 文字”“* 文字”“1. 文字”
_TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+:\s")   # Co-Authored-By: / Signed-off-by: 这类尾注
_CODE = re.compile(r"`([^`]+)`")
_EMAIL = re.compile(r"&lt;([^\s&]+@[^\s&]+)&gt;")                     # 转义过的 <邮箱>


def _tooltip(item) -> str:
    if isinstance(item, FileRow):
        f = item.file
        return f"<p style='white-space:pre'>{html.escape(f.path)}" + \
               (f"<br>（重命名自 {html.escape(f.old_path)}）" if f.old_path else "") + "</p>"
    # 照 VS Code 的提交悬停框：作者和时间 / 标题 / 正文（列表悬挂缩进，尾注变暗）/ 分隔线 / 引用标签和提交 id。
    # 全部常规字重，层次靠颜色和留白
    c: Commit = item.commit
    fm = QFontMetrics(QToolTip.font())
    measure: list[tuple[int, str]] = []         # 显示出来的每一行：(缩进, 文字)，用来定宽

    when, stamp = ago(c.time), fmt_time(c.time)
    stamp = "" if when == stamp else f"（{stamp}）"     # 两个月以前的提交，ago() 给的就是日期
    s = _para(colored(html.escape(c.author), theme.TIP_LINK) + colored(f" · {when}", theme.TIP_TEXT)
              + colored(stamp, theme.TIP_TEXT_DIM), 0)
    s += _para(_inline(c.subject), TOOLTIP_GAP // 2, theme.TIP_TITLE)
    measure += [(0, f"{c.author} · {when}{stamp}"), (0, c.subject)]

    body = c.body.split("\n") if c.body else []     # 多行的提交说明：标题下面接着显示正文，最多 TOOLTIP_BODY_LINES 行
    s += _body_html(body[:TOOLTIP_BODY_LINES], fm, measure)
    if len(body) > TOOLTIP_BODY_LINES:
        s += _para(f"…（还有 {len(body) - TOOLTIP_BODY_LINES} 行）", TOOLTIP_GAP, theme.TIP_TEXT_DIM)

    # 分隔线下面：左边引用标签，右边提交 id
    line = f"border-top:1px solid {theme.TIP_BORDER}; padding-top:{TOOLTIP_GAP - 2}px"
    commit_id = colored(c.short, theme.TIP_LINK)
    if c.refs:
        chips = "&nbsp; ".join(_ref_chip_html(r) for r in c.refs)
        cells = f"<td style='{line}'>{chips}</td><td style='{line}' align=right>{commit_id}</td>"
        labels = "  ".join(f" {theme.CLOUD + ' ' if r.kind == 'remote' else ''}{_ref_style(r)[0]} " for r in c.refs)
        measure.append((24, labels + c.short))      # 24：标签和 id 的间隔
    else:
        cells = f"<td style='{line}'>{commit_id}</td>"
    # 上一段的行高已经在底下留了空，分隔线上方只再空一点，和下方差不多
    footer = f"cellspacing=0 cellpadding=0 style='margin-top:{TOOLTIP_GAP // 2}px'"
    s += f"<table width=100% {footer}><tr>{cells}</tr></table>"
    # Qt 给富文本提示框定宽是往 5:3 的宽高比上凑，长行会被压窄、在奇怪的地方折行；
    # 套一个定宽的表格，宽度按内容来定：最长一行的宽度，但不超过 TOOLTIP_MAX_W
    w = max(indent + fm.horizontalAdvance(text) for indent, text in measure)
    width = min(w + 2, TOOLTIP_MAX_W)           # +2：度量取整的余量，免得刚好放得下的一行被折开
    return f"<table width={width} cellspacing=0 cellpadding=0><tr><td>{s}</td></tr></table>"


def _para(content: str, gap: int, color: str = "") -> str:
    """悬停提示里的一段（content 是转义过的 HTML）：保留换行和空格，行高放松一点。
    gap：和上一块的间距。间距一律放在上面：Qt 在表格前面、表格格子里会把段落的下边距丢掉。"""
    style = (f"white-space:pre-wrap; margin-top:{gap}px; margin-bottom:0; line-height:{TOOLTIP_LINE_HEIGHT}%"
             + (f"; color:{color}" if color else ""))
    return f"<p style='{style}'>{content}</p>"


def _inline(text: str) -> str:
    """转义一行文字，`反引号` 里的代码换个颜色（去掉反引号）。"""
    return _CODE.sub(lambda m: colored(m.group(1), theme.TIP_CODE), html.escape(text))


def _body_html(lines: list[str], fm: QFontMetrics, measure: list[tuple[int, str]]) -> str:
    """提交正文：空行分段；“- ”“* ”“1. ”开头的行排成悬挂缩进的列表（缩进的续行跟着上一项）；
    最后一段如果全是尾注（Co-Authored-By: … 这类）就变暗，邮箱用链接色。"""
    paras: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            paras[-1].append(line.rstrip())
        elif paras[-1]:
            paras.append([])
    paras = [p for p in paras if p]
    out = []
    for i, para in enumerate(paras):
        if i == len(paras) - 1 and all(_TRAILER.match(t) for t in para):
            measure.extend((0, t) for t in para)
            trailers = "\n".join(_EMAIL.sub(lambda m: colored(m.group(1), theme.TIP_LINK), html.escape(t))
                                 for t in para)
            out.append(_para(trailers, TOOLTIP_GAP, theme.TIP_TEXT_DIM))
            continue
        # 一段里可能是普通文字和列表交替：[("text", 行)] / [("list", [(层级, 符号, 行)])]
        segs: list[tuple[str, list]] = []
        for t in para:
            m = _LIST_ITEM.match(t)
            if m:
                if not segs or segs[-1][0] != "list":
                    segs.append(("list", []))
                segs[-1][1].append((min(len(m.group(1).expandtabs(4)) // 2, 3), m.group(2), [m.group(3)]))
            elif segs and segs[-1][0] == "list" and t[0].isspace():
                segs[-1][1][-1][2].append(t.strip())        # 列表项缩进的续行
            elif segs and segs[-1][0] == "text":
                segs[-1][1].append(t)
            else:
                segs.append(("text", [t]))
        for j, (kind, content) in enumerate(segs):
            gap = TOOLTIP_GAP if j == 0 else TOOLTIP_ITEM_GAP
            if kind == "text":
                measure.extend((0, t) for t in content)
                out.append(_para("\n".join(_inline(t) for t in content), gap))
            else:
                out.append(_list_html(content, gap, fm, measure))
    return "".join(out)


def _list_html(items: list, gap: int, fm: QFontMetrics, measure: list[tuple[int, str]]) -> str:
    """列表：项目符号单独一列，折行的文字和第一行对齐（悬挂缩进）。嵌套的列表多缩进一格。
    gap：和上一块的间距（表格的上边距）；列表项之间的间距用格子的上内边距。"""
    out = []
    groups = [(level, list(run)) for level, run in itertools.groupby(items, key=lambda it: it[0])]
    for k, (level, run) in enumerate(groups):
        marks = [mark if mark[0].isdigit() else ("•" if level == 0 else "◦") for _, mark, _ in run]
        mark_w = max(TOOLTIP_INDENT, max(fm.horizontalAdvance(mk) for mk in marks) + 6)
        indent = level * TOOLTIP_INDENT
        rows = []
        for n, ((_, _, texts), mark) in enumerate(zip(run, marks, strict=True)):
            measure.extend((indent + mark_w, t) for t in texts)
            text = "\n".join(_inline(t) for t in texts)
            pad = f" style='padding-top:{TOOLTIP_ITEM_GAP}px'" if n else ""
            spacer = f"<td width={indent}{pad}></td>" if indent else ""
            rows.append(f"<tr>{spacer}<td width={mark_w}{pad}>{_para(mark, 0, theme.TIP_TEXT_DIM)}</td>"
                        f"<td{pad}>{_para(text, 0)}</td></tr>")
        top = gap if k == 0 else TOOLTIP_ITEM_GAP
        out.append(f"<table cellspacing=0 cellpadding=0 style='margin-top:{top}px'>{''.join(rows)}</table>")
    return "".join(out)


def _ref_style(ref: Ref) -> tuple[str, QColor, bool]:
    """引用标签的 (文字, 颜色, 是否当前分支)：当前分支前面加 ◎；远端分支是紫色（云朵图标另外画）。"""
    if ref.kind == "remote":
        return ref.name, theme.REF_REMOTE, False
    if ref.kind == "tag":
        return ref.name, theme.REF_TAG, False
    if ref.kind == "head":
        return "HEAD", theme.REF_BRANCH, True
    if ref.is_head:
        return "◎ " + ref.name, theme.REF_BRANCH, True
    return ref.name, theme.REF_BRANCH, False


def _ref_chip_html(ref: Ref) -> str:
    """悬停提示里的引用标签（富文本没有半透明，底色直接混到提示框的底色上）。"""
    label, color, current = _ref_style(ref)
    fg, fill, _ = theme.ref_chip(color, current)
    bg = theme.mix(QColor(theme.TIP_BG), color, fill.alphaF())
    text = html.escape(label).replace(" ", "&nbsp;")
    if ref.kind == "remote":
        text = f"<span style=\"font-family:'{theme.ICON_FONT}'\">{theme.CLOUD}</span>&nbsp;{text}"
    return f"<span style='color:{fg.name()}; background-color:{bg.name()}'>&nbsp;{text}&nbsp;</span>"


def row_refs(refs: list[Ref]) -> list[tuple[str, QColor, bool, bool]]:
    """提交行右侧要画的引用标签 (文字, 颜色, 是否当前分支, 是否画云朵图标)。
    远端分支和同名的本地分支在同一个提交上（已同步）时只画一朵云，全名看悬停提示。"""
    local = {r.name for r in refs if r.kind == "local"}
    out = []
    for r in refs:
        label, color, current = _ref_style(r)
        remote = r.kind == "remote"
        if remote and r.name.partition("/")[2] in local:
            label = ""
        out.append((label, color, current, remote))
    return out


class CommitModel(QAbstractListModel):
    moreRequested = Signal()        # 滚动到底部、还有更多提交时发出

    def __init__(self, parent=None):
        super().__init__(parent)
        self.list = ItemList()
        self.more = False
        self.head_oid = ""

    @property
    def lanes(self) -> int:
        return self.list.lanes

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.list.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.list.items)):
            return None
        item = self.list.items[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return item.file.path if isinstance(item, FileRow) else item.commit.subject
        if role == Qt.ItemDataRole.ToolTipRole:
            return _tooltip(item)
        return None

    def canFetchMore(self, parent=QModelIndex()):
        return (not parent.isValid()) and self.more

    def fetchMore(self, parent=QModelIndex()):
        self.moreRequested.emit()

    # ---- 查询 ----
    def row_at(self, i: int):
        return self.list.items[i] if 0 <= i < len(self.list.items) else None

    def index_of(self, oid: str) -> QModelIndex:
        i = self.list.pos_of(oid)
        return self.index(i, 0) if i >= 0 else QModelIndex()

    def file_index(self, oid: str, path: str) -> QModelIndex:
        i = self.list.file_pos(oid, path)
        return self.index(i, 0) if i >= 0 else QModelIndex()

    # ---- 修改 ----
    def reset(self, rows: list, head_oid: str, more: bool, files_by_oid=None) -> None:
        self.beginResetModel()
        self.list.reset(rows, files_by_oid)
        self.head_oid = head_oid
        self.more = more
        self.endResetModel()

    def append(self, rows: list, more: bool, files_by_oid=None) -> None:
        """追加一页提交（已展开的提交会带上它的文件行）。"""
        self.more = more
        if not rows:
            return
        count = len(rows)
        if files_by_oid:
            count += sum(len(files_by_oid[r.commit.oid]) for r in rows if r.commit.oid in files_by_oid)
        start = len(self.list.items)
        self.beginInsertRows(QModelIndex(), start, start + count - 1)
        self.list.extend(rows, files_by_oid)
        self.endInsertRows()

    def expand(self, oid: str, files: list) -> None:
        pos = self.list.pos_of(oid)
        if pos < 0:
            return
        a, b = self._peek_expand(oid, files)
        if b >= a:
            self.beginInsertRows(QModelIndex(), a, b)
            self.list.expand(oid, files)
            self.endInsertRows()
        else:
            self.list.expand(oid, files)
        i = self.index(pos, 0)
        self.dataChanged.emit(i, i)

    def collapse(self, oid: str) -> None:
        pos = self.list.pos_of(oid)
        if pos < 0:
            return
        a, b = self._peek_collapse(oid)
        if b >= a:
            self.beginRemoveRows(QModelIndex(), a, b)
            self.list.collapse(oid)
            self.endRemoveRows()
        else:
            self.list.collapse(oid)
        i = self.index(pos, 0)
        self.dataChanged.emit(i, i)

    # 先“预演”要插入 / 删除的范围，再调用 begin*Rows（Qt 要求先通知再改数据）
    def _peek_expand(self, oid: str, files: list) -> tuple:
        pos = self.list.pos_of(oid)
        if pos < 0 or self.list.items[pos].expanded or not files:
            return (0, -1)
        return (pos + 1, pos + len(files))

    def _peek_collapse(self, oid: str) -> tuple:
        pos = self.list.pos_of(oid)
        if pos < 0:
            return (0, -1)
        j = pos + 1
        items = self.list.items
        while j < len(items) and isinstance(items[j], FileRow) and items[j].parent is items[pos]:
            j += 1
        return (pos + 1, j - 1) if j > pos + 1 else (0, -1)


@dataclass
class _RowGeom:
    """一行里绘制要用到的尺寸和颜色。"""
    rect: QRect
    top: float
    mid: float
    bot: float
    lane_w: float       # 每列宽度（线多时会被压窄）
    cols: int           # 能画出的列数
    text_x: float       # 说明文字从哪里开始
    text_pen: QColor
    bg: QColor
    selected: bool

    def lx(self, col: int) -> float:
        """第 col 列分支线的 x 坐标（放不下的挤在最后一列）。"""
        return self.rect.left() + PAD + min(col, self.cols - 1) * self.lane_w + self.lane_w / 2.0

    @property
    def pen_w(self) -> float:
        return 2.0 if self.lane_w >= 8 else 1.5


class CommitDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        fm = option.fontMetrics
        h = max(24, fm.height() + 8)
        return QSize(0, h)

    def paint(self, painter, option, index):
        item = index.data(Qt.ItemDataRole.UserRole)
        if item is None:
            return super().paint(painter, option, index)
        model = index.model()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        palette = option.palette
        # 窗口不在前台时选中行用灰色（和其它列表一样；QListView 交给代理的调色板总是“前台”那一组）
        active = bool(option.state & QStyle.StateFlag.State_Active)
        cg = QPalette.ColorGroup.Active if active else QPalette.ColorGroup.Inactive
        bg = palette.color(cg, QPalette.ColorRole.Highlight) if selected else palette.color(QPalette.ColorRole.Base)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(rect)
        if selected:
            painter.fillRect(rect, bg)
        elif hovered:
            painter.fillRect(rect, theme.ROW_HOVER)

        lane_w, cols = lane_metrics(model.lanes, LANE_W, GRAPH_MAX_W, MIN_LANE_W)
        top = float(rect.top())
        g = _RowGeom(rect=rect, top=top, mid=top + rect.height() / 2.0, bot=float(rect.bottom() + 1),
                     lane_w=lane_w, cols=cols, text_x=rect.left() + PAD * 2 + cols * lane_w + 2,
                     text_pen=palette.color(cg, QPalette.ColorRole.HighlightedText if selected
                                            else QPalette.ColorRole.Text),
                     bg=bg, selected=selected)
        if isinstance(item, FileRow):
            self._paint_file(painter, option, item, g)
        else:
            self._paint_commit(painter, option, item, model, g)
        painter.restore()

    # ------------------------------------------------------------ 文件行
    def _paint_file(self, painter, option, item, g: _RowGeom):
        rect = g.rect
        # 提交的分支线继续穿过文件行（只画“下半格”各条线的终点那一列，竖直线）
        seen = set()
        for _a, b, color in item.parent.graph.bottom:
            if b in seen:
                continue
            seen.add(b)
            painter.setPen(QPen(theme.lane_color(color), g.pen_w))
            painter.drawLine(QPointF(g.lx(b), g.top), QPointF(g.lx(b), g.bot))

        font = QFont(option.font)
        painter.setFont(font)
        fm = painter.fontMetrics()
        f = item.file
        x = g.text_x + FILE_INDENT
        base = g.mid + (fm.ascent() - fm.descent()) / 2.0

        # 小文件图标
        painter.setPen(QPen(QColor(theme.FILE_ICON_SELECTED if g.selected else theme.FILE_ICON), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(x - 17, g.mid - 6.5, 10, 13), 1.5, 1.5)
        painter.drawLine(QPointF(x - 15, g.mid - 1.5), QPointF(x - 9, g.mid - 1.5))
        painter.drawLine(QPointF(x - 15, g.mid + 2.5), QPointF(x - 9, g.mid + 2.5))

        letter = f.status
        letter_w = fm.horizontalAdvance(letter)
        right_limit = rect.right() - 14 - letter_w - 8
        directory, _, name = f.path.rpartition("/")
        avail = max(0, int(right_limit - x))
        name_el = fm.elidedText(name, Qt.TextElideMode.ElideMiddle, avail)
        painter.setPen(g.text_pen)
        painter.drawText(QPointF(x, base), name_el)
        used = fm.horizontalAdvance(name_el)
        if directory and avail - used > 40:
            small = QFont(option.font)
            if option.font.pointSizeF() > 0:
                small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
            painter.setFont(small)
            sfm = painter.fontMetrics()
            painter.setPen(QColor(theme.TEXT_HEADER if g.selected else theme.TEXT_FAINT))
            painter.drawText(QPointF(x + used + 8, base),
                             sfm.elidedText(directory, Qt.TextElideMode.ElideMiddle, avail - used - 8))
            painter.setFont(font)
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QColor(theme.status_color(letter)))
        painter.drawText(QPointF(rect.right() - 14 - letter_w, base), letter)

    # ------------------------------------------------------------ 提交行
    def _paint_commit(self, painter, option, row, model, g: _RowGeom):
        rect, mid = g.rect, g.mid
        gr = row.graph
        for a, b, color in gr.top:
            painter.setPen(QPen(theme.lane_color(color), g.pen_w))
            painter.drawLine(QPointF(g.lx(a), g.top), QPointF(g.lx(b), mid))
        for a, b, color in gr.bottom:
            painter.setPen(QPen(theme.lane_color(color), g.pen_w))
            painter.drawLine(QPointF(g.lx(a), mid), QPointF(g.lx(b), g.bot))

        cx = g.lx(gr.col)
        node_color = theme.lane_color(gr.color)
        is_head = row.commit.oid == model.head_oid
        if is_head:        # 当前位置：空心圆
            painter.setPen(QPen(node_color, 2))
            painter.setBrush(QBrush(g.bg))
            r = min(5.5, g.lane_w / 2.0 + 0.5)
        else:
            painter.setPen(QPen(g.bg, 1))
            painter.setBrush(QBrush(node_color))
            r = min(4.5, g.lane_w / 2.0 - 0.5)
        painter.drawEllipse(QPointF(cx, mid), r, r)

        # ---------- 右侧：引用标签 ----------
        pill_font = QFont(option.font)
        ps = option.font.pointSizeF()
        if ps > 0:
            pill_font.setPointSizeF(max(7.0, ps - 1))
        painter.setFont(pill_font)
        pfm = painter.fontMetrics()

        icon_font = QFont(theme.ICON_FONT)              # 远端分支的云朵图标，比标签文字稍大
        icon_font.setPixelSize(max(8, round(pfm.height() * 0.85)))
        cloud = QFontMetrics(icon_font).tightBoundingRect(theme.CLOUD)     # 按字形的实际轮廓对齐
        icon_w = cloud.width()
        pills = []
        for label, color, current, remote in row_refs(row.commit.refs):
            icon = (icon_w + (4 if label else 0)) if remote else 0    # 图标和文字之间空 4
            if label:
                label = pfm.elidedText(label, Qt.TextElideMode.ElideMiddle, 150 - icon)
            pills.append((label, color, current, icon, icon + pfm.horizontalAdvance(label) + 14))

        max_w = rect.width() * 0.6
        shown: list[tuple[str, QColor, bool, int, int]] = []
        used = 0
        for label, color, current, icon, w in pills:
            if used + w > max_w and shown:
                extra = len(pills) - len(shown)
                ew = pfm.horizontalAdvance(f"+{extra}") + 14
                shown.append((f"+{extra}", theme.REF_MORE, False, 0, ew))
                used += ew + 4
                break
            shown.append((label, color, current, icon, w))
            used += w + 4

        x = rect.right() - 6 - used + 4
        ph = pfm.height() + 2
        py = mid - ph / 2.0
        for label, color, current, icon, w in shown:    # 圆头的淡色标签：半透明底 + 细边框 + 同色字
            fg, fill, border = theme.ref_chip(color, current)
            painter.setPen(QPen(border, 1))
            painter.setBrush(QBrush(fill))
            painter.drawRoundedRect(QRectF(x + 0.5, py + 0.5, w - 1, ph - 1), (ph - 1) / 2, (ph - 1) / 2)
            painter.setPen(fg)
            if icon:
                painter.setFont(icon_font)
                painter.drawText(QPointF(x + 7 - cloud.left(), mid - cloud.top() - cloud.height() / 2.0), theme.CLOUD)
                painter.setFont(pill_font)
            painter.drawText(QPointF(x + 7 + icon, py + 1 + pfm.ascent()), label)
            x += w + 4
        pills_left = rect.right() - 6 - used

        # ---------- 提交说明 ----------
        font = QFont(option.font)
        font.setBold(is_head)
        painter.setFont(font)
        fm = painter.fontMetrics()
        avail = max(0, int(pills_left - g.text_x - 6))
        text = fm.elidedText(row.commit.subject, Qt.TextElideMode.ElideRight, avail)
        painter.setPen(g.text_pen)
        painter.drawText(QPointF(g.text_x, mid + (fm.ascent() - fm.descent()) / 2.0), text)


class CommitTip(QLabel):
    """提交图的悬停提示。QToolTip 只会出现在鼠标旁边、挡住提交图，所以自己做一个提示框：
    贴在 git 树右边那一栏（正文区）的左侧，顶边和鼠标所在的行对齐。样式见 theme.py（#commitTip）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setObjectName("commitTip")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)

    def show_at(self, text: str, x: int, top: int) -> None:
        """左上角放在 (x, top)（全局坐标）；屏幕放不下就往左 / 往上挪。"""
        self.setText(text)
        self.adjustSize()
        screen = QGuiApplication.screenAt(QPoint(x, top)) or self.screen()
        area = screen.availableGeometry()
        self.move(max(area.left(), min(x, area.right() + 1 - self.width())),
                  max(area.top(), min(top, area.bottom() + 1 - self.height())))
        self.show()


class GraphView(QListView):
    # 键盘 / 点击请求展开或收起：mode = 1 展开，-1 收起，0 切换
    expandRequested = Signal(QModelIndex, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._delegate = CommitDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(ROW_H)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tip = CommitTip(self)
        self._last_hover_row = -1

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        idx = self.indexAt(event.position().toPoint())
        row = idx.row() if idx.isValid() else -1
        if row == self._last_hover_row:
            return
        self._last_hover_row = row
        tip = self.model().data(idx, Qt.ItemDataRole.ToolTipRole) if idx.isValid() and self.model() else None
        if tip:     # 0 延迟弹出，不挡提交图：贴在正文区左侧，和这一行顶边对齐
            top = self.viewport().mapToGlobal(self.visualRect(idx).topLeft()).y()
            self.tip.show_at(tip, self._content_left() + TIP_GAP, top)
        else:
            self.tip.hide()

    def _content_left(self) -> int:
        """正文区左边的全局 x：沿父控件找到左右分栏，取 git 树右边那一栏的左边；不在分栏里就取列表的右边。"""
        w: QWidget = self
        while (p := w.parentWidget()) is not None:
            if isinstance(p, QSplitter) and p.orientation() == Qt.Orientation.Horizontal:
                right = p.widget(p.indexOf(w) + 1)
                if right is not None:
                    return right.mapToGlobal(QPoint(0, 0)).x()
            w = p
        return self.mapToGlobal(QPoint(self.width(), 0)).x()

    def hide_tip(self) -> None:
        self._last_hover_row = -1
        self.tip.hide()

    def mousePressEvent(self, event):
        self.hide_tip()
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        self.hide_tip()
        super().wheelEvent(event)

    def leaveEvent(self, event):
        self.hide_tip()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.hide_tip()                 # 切换项目 / 工作模式、窗口隐藏到后台
        super().hideEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide_tip()             # 切到别的程序：提示框是置顶的小窗口，不能留在别人上面
        super().changeEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            # 屏蔽 Qt 系统默认 ~700ms 滞后 QHelpEvent，避免重复二次触发
            return True
        if event.type() == QEvent.Type.Leave:
            self.hide_tip()
        return super().viewportEvent(event)

    def keyPressEvent(self, event):
        self.hide_tip()
        idx = self.currentIndex()
        if idx.isValid():
            key = event.key()
            if key == Qt.Key.Key_Right:
                self.expandRequested.emit(idx, 1)
                return
            if key == Qt.Key.Key_Left:
                self.expandRequested.emit(idx, -1)
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.expandRequested.emit(idx, 0)
                return
        super().keyPressEvent(event)
