"""提交图：每个提交一行，左边画分支线和节点，右边是说明和分支/远端/标签标签。
点一个提交，它修改的文件就地展开在它下面（分支线继续穿过文件行），再点一次收起。"""
from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractItemView, QFrame, QListView, QStyle, QStyledItemDelegate, QToolTip

from . import theme
from .gitcore import Commit
from .graph import lane_metrics
from .items import FileRow, ItemList
from .utils import fmt_time

ROW_H = 26
LANE_W = 14
PAD = 8
GRAPH_MAX_W = 10 * LANE_W   # 分支线区域最宽这么多；线多时压窄列宽
MIN_LANE_W = 5              # 列宽最窄压到这么多，再多的线才挤在最后一列
TOOLTIP_BODY_LINES = 30     # 悬停提示里最多显示多少行提交正文
FILE_INDENT = 22    # 文件行相对提交说明再缩进多少


def _tooltip(item) -> str:
    if isinstance(item, FileRow):
        f = item.file
        return f"<p style='white-space:pre'>{html.escape(f.path)}" + \
               (f"<br>（重命名自 {html.escape(f.old_path)}）" if f.old_path else "") + "</p>"
    c: Commit = item.commit
    s = f"<p style='white-space:pre'><b>{html.escape(c.subject)}</b></p>"
    if c.body:                  # 多行的提交说明：标题下面接着显示正文，最多 TOOLTIP_BODY_LINES 行
        lines = c.body.split("\n")
        shown = "\n".join(lines[:TOOLTIP_BODY_LINES])
        if len(lines) > TOOLTIP_BODY_LINES:
            shown += f"\n…（还有 {len(lines) - TOOLTIP_BODY_LINES} 行）"
        s += f"<p style='white-space:pre-wrap'>{html.escape(shown)}</p>"
    meta = f"{c.short} · {html.escape(c.author)} · {fmt_time(c.time)}"
    refs = ", ".join(r.name for r in c.refs)
    if refs:
        meta += f"<br>{html.escape(refs)}"
    return s + f"<p style='white-space:pre; color:{theme.TEXT_DIM}'>{meta}</p>"


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
        bg = palette.color(QPalette.ColorRole.Highlight) if selected else palette.color(QPalette.ColorRole.Base)

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
                     text_pen=palette.color(QPalette.ColorRole.HighlightedText if selected
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
        pill_font.setBold(True)
        painter.setFont(pill_font)
        pfm = painter.fontMetrics()

        pills = []
        for ref in row.commit.refs:
            if ref.kind == "remote":
                label, color = "☁ " + ref.name, theme.REF_REMOTE
            elif ref.kind == "tag":
                label, color = ref.name, theme.REF_TAG
            elif ref.kind == "head":
                label, color = "HEAD", theme.REF_HEAD
            elif ref.is_head:
                label, color = "◎ " + ref.name, theme.REF_HEAD
            else:
                label, color = ref.name, theme.REF_LOCAL
            label = pfm.elidedText(label, Qt.TextElideMode.ElideMiddle, 150)
            pills.append((label, color, pfm.horizontalAdvance(label) + 12))

        max_w = rect.width() * 0.6
        shown: list[tuple[str, QColor, int]] = []
        used = 0
        for label, color, w in pills:
            if used + w > max_w and shown:
                extra = len(pills) - len(shown)
                ew = pfm.horizontalAdvance(f"+{extra}") + 12
                shown.append((f"+{extra}", theme.REF_MORE, ew))
                used += ew + 4
                break
            shown.append((label, color, w))
            used += w + 4

        x = rect.right() - 6 - used + 4
        ph = pfm.height() + 4
        py = mid - ph / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        for label, color, w in shown:
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(QRectF(x, py, w, ph), 3, 3)
            painter.setPen(QColor(theme.TEXT_BRIGHT))
            painter.drawText(QPointF(x + 6, py + 2 + pfm.ascent()), label)
            painter.setPen(Qt.PenStyle.NoPen)
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
        self._last_hover_row = -1

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        pos = event.position().toPoint()
        idx = self.indexAt(pos)
        row = idx.row() if idx.isValid() else -1
        if row != self._last_hover_row:
            self._last_hover_row = row
            if idx.isValid() and self.model() is not None:
                tip = self.model().data(idx, Qt.ItemDataRole.ToolTipRole)
                if tip:
                    global_pos = self.viewport().mapToGlobal(pos)
                    # 鼠标右下方微距偏移，避免遮挡当前光标所在行且 0 延迟秒弹
                    tip_pos = QPoint(global_pos.x() + 10, global_pos.y() + 16)
                    QToolTip.showText(tip_pos, tip, self.viewport())
                else:
                    QToolTip.hideText()
            else:
                QToolTip.hideText()

    def mousePressEvent(self, event):
        self._last_hover_row = -1
        QToolTip.hideText()
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        self._last_hover_row = -1
        QToolTip.hideText()
        super().wheelEvent(event)

    def leaveEvent(self, event):
        self._last_hover_row = -1
        QToolTip.hideText()
        super().leaveEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            # 屏蔽 Qt 系统默认 ~700ms 滞后 QHelpEvent，避免重复二次触发
            return True
        if event.type() == QEvent.Type.Leave:
            self._last_hover_row = -1
            QToolTip.hideText()
        return super().viewportEvent(event)

    def keyPressEvent(self, event):
        self._last_hover_row = -1
        QToolTip.hideText()
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
