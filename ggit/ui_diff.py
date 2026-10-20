"""diff 显示，两种模式（Alt+D 切换）：

  unified：上下合并——只看改动处，红底 = 删除行，绿底 = 新增行
  split  ：左右对照——左边修改前、右边修改后，显示整个文件，两侧行对齐，短的一侧补灰色空行

两种模式都用加宽的垂直滚动条：条上用红 / 绿色块标出每一处改动的位置，点色块直接跳过去；
也可以用 F3 / F4（或标题栏的 ↑ ↓）逐处跳转。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QRect, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QPainter, QTextBlockFormat,
                           QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QScrollBar, QSizePolicy, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from . import theme
from .difftools import build_split, change_groups, trim_context
from .options import FONT_DEFAULT

MAX_ROWS = 30000            # 超过就截断，避免一次塞太多行卡住界面
MODE_UNIFIED = "unified"
MODE_SPLIT = "split"

FILL_BG = QColor("#262628")          # 对照模式里补出来的空行
DEL_MARK = QColor(241, 76, 76, 220)
ADD_MARK = QColor(78, 201, 123, 220)

_SB_CSS = """
QScrollBar:vertical { background: transparent; width: 18px; margin: 0px; border: none; }
QScrollBar::handle:vertical { background: rgba(190,190,190,70); min-height: 28px;
                              border: 1px solid rgba(255,255,255,90); margin: 0px; }
QScrollBar::handle:vertical:hover { background: rgba(210,210,210,110); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; border: none; background: none; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


def code_font(ui_pt: int) -> QFont:
    """代码视图用的等宽字体：比界面字体大 1 号，最小 8 pt。"""
    pt = max(8, ui_pt + 1)
    if sys.platform == "win32":
        return QFont("Consolas", pt)
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(pt)
    return font


def _n(x) -> str:
    return "" if x is None else str(x)


class Blk:
    """一行要显示的内容：文字、底色、前景色、行号栏的数字、是否改动行。"""
    __slots__ = ("text", "bg", "fg", "nums", "changed")

    def __init__(self, text, bg=None, fg=None, nums=(), changed=False):
        self.text, self.bg, self.fg, self.nums, self.changed = text, bg, fg, nums, changed


# ---------------------------------------------------------------- 滚动条与改动导航
class MarkerScrollBar(QScrollBar):
    """加宽的垂直滚动条：条上画红 / 绿色块标出改动位置；点色块跳到那一处，点空白处跳到对应位置。"""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setFixedWidth(18)
        self.setStyleSheet(_SB_CSS)
        self._markers: list = []       # [(起始行, 结束行, 含删除, 含新增)]

    def set_markers(self, groups: list) -> None:
        self._markers = groups
        self.update()

    def _total(self) -> int:
        return max(1, self.maximum() + self.pageStep())

    def paintEvent(self, event):
        p = QPainter(self)
        w, h, total = self.width(), self.height(), self._total()
        p.fillRect(0, 0, w, h, QColor("#1b1b1c"))
        for s, e, has_del, has_add in self._markers:
            y0 = int(s / total * h)
            hh = max(4, int((e + 1) / total * h) - y0)
            x0, x1 = 3, w - 3
            if has_del and has_add:
                mid = (x0 + x1) // 2
                p.fillRect(x0, y0, mid - x0, hh, DEL_MARK)
                p.fillRect(mid, y0, x1 - mid, hh, ADD_MARK)
            else:
                p.fillRect(x0, y0, x1 - x0, hh, DEL_MARK if has_del else ADD_MARK)
        p.end()
        super().paintEvent(event)      # 滑块（半透明）画在色块上面

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            h, total = max(1, self.height()), self._total()
            y = event.position().y()
            top = self.value() / total * h
            if not (top <= y <= top + max(28.0, self.pageStep() / total * h)):    # 没点在滑块上：直接跳转
                row = None
                for s, e, _d, _a in self._markers:                                 # 点在色块附近：对准这一处改动
                    if int(s / total * h) - 4 <= y <= int((e + 1) / total * h) + 4:
                        row = s
                        break
                if row is None:
                    self.setValue(int(y / h * total - self.pageStep() / 2))
                else:
                    self.setValue(max(0, row - self.pageStep() // 3))
                event.accept()
                return
        super().mousePressEvent(event)


class ChangeNavigator:
    """“下一处 / 上一处改动”：在每段改动的起始行之间跳转（到头后回绕）。"""

    def __init__(self, bar: QScrollBar):
        self.bar = bar
        self.starts: list = []
        self.cur = None
        self._busy = False
        bar.valueChanged.connect(self._on_scroll)

    def _on_scroll(self, _v):
        if not self._busy:
            self.cur = None            # 用户自己滚动过了，下次从当前位置重新找

    def go(self, forward: bool) -> None:
        if not self.starts:
            return
        if self.cur is None:
            center = self.bar.value() + self.bar.pageStep() // 2
            if forward:
                idx = next((i for i, s in enumerate(self.starts) if s > center), 0)
            else:
                idx = next((i for i in range(len(self.starts) - 1, -1, -1) if self.starts[i] < center),
                           len(self.starts) - 1)
        else:
            idx = (self.cur + (1 if forward else -1)) % len(self.starts)
        self.cur = idx
        self._busy = True
        self.bar.setValue(max(0, self.starts[idx] - self.bar.pageStep() // 3))
        self._busy = False


# ---------------------------------------------------------------- 代码视图
class _Gutter(QWidget):
    def __init__(self, view):
        super().__init__(view)
        self._view = view

    def paintEvent(self, event):
        self._view.paint_gutter(event)


class _CodeView(QPlainTextEdit):
    """只读代码视图：整行底色 + 左侧行号栏（1 列或 2 列）。"""

    def __init__(self, cols: int, own_bar: bool, parent=None):
        super().__init__(parent)
        self._cols = cols
        self._nums: list = []
        self._chg: list = []
        self._digits = 3
        if own_bar:
            self.setVerticalScrollBar(MarkerScrollBar())
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._on_update_request)
        app = QApplication.instance()
        ui_pt = app.font().pointSize() if app and app.font().pointSize() > 0 else FONT_DEFAULT
        self.apply_ui_size(ui_pt)

    def apply_ui_size(self, ui_pt: int) -> None:
        """界面字号变了：代码字体（等宽，比界面字体大 1 号）跟着变。"""
        self.setFont(code_font(ui_pt))
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._update_margin()

    def set_blocks(self, blocks: list) -> None:
        self._nums = [b.nums for b in blocks]
        self._chg = [b.changed for b in blocks]
        mx = max([len(s) for n in self._nums for s in n] + [1])
        self._digits = max(3, mx)
        self.setUpdatesEnabled(False)
        self.clear()
        cur = QTextCursor(self.document())
        cur.beginEditBlock()
        for i, b in enumerate(blocks):
            bf = QTextBlockFormat()
            cf = QTextCharFormat()
            if b.bg is not None:
                bf.setBackground(QBrush(b.bg))
            if b.fg is not None:
                cf.setForeground(QBrush(b.fg))
            if i == 0:
                cur.setBlockFormat(bf)
                cur.setBlockCharFormat(cf)
            else:
                cur.insertBlock(bf, cf)
            cur.insertText(b.text, cf)
        cur.endEditBlock()
        self.setUpdatesEnabled(True)
        self._update_margin()
        self.moveCursor(QTextCursor.MoveOperation.Start)

    # ---- 行号栏 ----
    def gutter_width(self) -> int:
        if not self._nums:
            return 0
        col_w = self.fontMetrics().horizontalAdvance("9") * self._digits
        return 8 + self._cols * col_w + (self._cols - 1) * 8 + 8

    def _update_margin(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self._place_gutter()

    def _place_gutter(self):
        cr = self.contentsRect()
        w = self.gutter_width()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), w, cr.height()))
        self._gutter.setVisible(w > 0)

    def _on_update_request(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_gutter()

    def paint_gutter(self, event):
        p = QPainter(self._gutter)
        p.fillRect(event.rect(), theme.GUTTER_BG)
        fm = self.fontMetrics()
        col_w = fm.horizontalAdvance("9") * self._digits
        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top() and n < len(self._nums):
                p.setPen(theme.GUTTER_FG_CHANGED if self._chg[n] else theme.GUTTER_FG)
                base = top + fm.ascent() + (round(self.blockBoundingRect(block).height()) - fm.height()) // 2
                for k, s in enumerate(self._nums[n]):
                    if s:
                        right = 8 + col_w + k * (col_w + 8)
                        p.drawText(QPointF(right - fm.horizontalAdvance(s), base), s)
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            n += 1
        p.end()


class UnifiedView(_CodeView):
    """上下合并模式。"""

    def __init__(self, parent=None):
        super().__init__(2, True, parent)
        self.bar: MarkerScrollBar = self.verticalScrollBar()
        self.nav = ChangeNavigator(self.bar)

    def set_lines(self, lines: list) -> None:
        extra = 0
        if len(lines) > MAX_ROWS:
            extra = len(lines) - MAX_ROWS
            lines = lines[:MAX_ROWS]
        blocks, flags = [], []
        for dl in lines:
            if dl.kind == "add":
                blocks.append(Blk("+ " + dl.text, theme.ADD_BG, None, ("", _n(dl.new_no)), True))
            elif dl.kind == "del":
                blocks.append(Blk("- " + dl.text, theme.DEL_BG, None, (_n(dl.old_no), ""), True))
            elif dl.kind in ("hunk", "note"):
                blocks.append(Blk(dl.text, theme.HUNK_BG, theme.HUNK_FG, ("", ""), False))
            else:
                blocks.append(Blk("  " + dl.text, None, None, (_n(dl.old_no), _n(dl.new_no)), False))
            flags.append((dl.kind == "del", dl.kind == "add"))
        if extra:
            blocks.append(Blk(f"… 已省略后面 {extra} 行（改动太多，只显示前 {MAX_ROWS} 行）",
                              None, theme.HUNK_FG, ("", ""), False))
            flags.append((False, False))
        self.set_blocks(blocks)
        groups = change_groups(flags)
        self.bar.set_markers(groups)
        self.nav.starts = [g[0] for g in groups]
        self.nav.cur = None


class SplitView(QWidget):
    """左右对照模式：两个编辑器、一根共用的垂直滚动条，行号和滚动位置始终对齐。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self.left = _CodeView(1, False)
        self.right = _CodeView(1, False)
        for v in (self.left, self.right):
            v.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left.setObjectName("splitLeft")
        self.bar = MarkerScrollBar()
        self.nav = ChangeNavigator(self.bar)
        for text, col in (("修改前", 0), ("修改后", 1)):
            lab = QLabel(text)
            lab.setContentsMargins(10, 2, 0, 2)
            lab.setObjectName("splitCaption")
            grid.addWidget(lab, 0, col)
        grid.addWidget(self.left, 1, 0)
        grid.addWidget(self.right, 1, 1)
        grid.addWidget(self.bar, 0, 2, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._syncing = False
        lsb, rsb = self.left.verticalScrollBar(), self.right.verticalScrollBar()
        for sb in (lsb, rsb, self.bar):
            sb.valueChanged.connect(self._on_value)
        rsb.rangeChanged.connect(self._sync_range)

    def _on_value(self, v):
        if self._syncing:
            return
        self._syncing = True
        for sb in (self.left.verticalScrollBar(), self.right.verticalScrollBar(), self.bar):
            if sb.value() != v:
                sb.setValue(v)
        self._syncing = False

    def _sync_range(self, *_):
        rsb = self.right.verticalScrollBar()
        self.bar.setRange(rsb.minimum(), rsb.maximum())
        self.bar.setPageStep(rsb.pageStep())
        self.bar.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_range()

    def set_rows(self, rows: list) -> None:
        extra = 0
        if len(rows) > MAX_ROWS:
            extra = len(rows) - MAX_ROWS
            rows = rows[:MAX_ROWS]
        lb, rb, flags = [], [], []
        for r in rows:
            a, b = r.left, r.right
            if a is not None and a.kind == "note":
                lb.append(Blk(a.text, theme.HUNK_BG, theme.HUNK_FG, ("",), False))
                rb.append(Blk(a.text, theme.HUNK_BG, theme.HUNK_FG, ("",), False))
                flags.append((False, False))
                continue
            if a is None:
                lb.append(Blk("", FILL_BG, None, ("",), False))
            elif a.kind == "del":
                lb.append(Blk(a.text, theme.DEL_BG, None, (_n(a.old_no),), True))
            else:
                lb.append(Blk(a.text, None, None, (_n(a.old_no),), False))
            if b is None:
                rb.append(Blk("", FILL_BG, None, ("",), False))
            elif b.kind == "add":
                rb.append(Blk(b.text, theme.ADD_BG, None, (_n(b.new_no),), True))
            else:
                rb.append(Blk(b.text, None, None, (_n(b.new_no),), False))
            flags.append((a is not None and a.kind == "del", b is not None and b.kind == "add"))
        if extra:
            msg = f"… 已省略后面 {extra} 行"
            lb.append(Blk(msg, None, theme.HUNK_FG, ("",), False))
            rb.append(Blk(msg, None, theme.HUNK_FG, ("",), False))
            flags.append((False, False))
        self.left.set_blocks(lb)
        self.right.set_blocks(rb)
        groups = change_groups(flags)
        self.bar.set_markers(groups)
        self.nav.starts = [g[0] for g in groups]
        self.nav.cur = None
        self._sync_range()


# ---------------------------------------------------------------- 一个“文档”（一个标签页里显示的内容）
class DiffDocument(QWidget):
    modeToggleRequested = Signal()

    def __init__(self, mode: str = MODE_UNIFIED, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.fd = None
        self.msg = ""
        self._dirty = True
        self._keep = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("diffBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 4, 6, 4)
        self.title = QLabel()
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.stats = QLabel()
        self.mode_btn = QToolButton()
        self.mode_btn.setToolTip("切换 diff 模式：上下合并 / 左右对照 (Alt+D)")
        self.prev_btn = QToolButton()
        self.prev_btn.setText("↑")
        self.prev_btn.setToolTip("上一处改动 (F3)")
        self.next_btn = QToolButton()
        self.next_btn.setText("↓")
        self.next_btn.setToolTip("下一处改动 (F4)")
        for w in (self.title,):
            bl.addWidget(w, 1)
        for w in (self.stats, self.mode_btn, self.prev_btn, self.next_btn):
            bl.addWidget(w)
        lay.addWidget(bar)

        self.stack = QStackedWidget()
        self.msg_label = QLabel()
        self.msg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.msg_label.setWordWrap(True)
        self.msg_label.setObjectName("placeholder")
        self.unified = UnifiedView()
        self.split = SplitView()
        self.stack.addWidget(self.msg_label)    # 0
        self.stack.addWidget(self.unified)      # 1
        self.stack.addWidget(self.split)        # 2
        lay.addWidget(self.stack, 1)

        self.mode_btn.clicked.connect(self.modeToggleRequested)
        self.prev_btn.clicked.connect(lambda: self.goto_change(False))
        self.next_btn.clicked.connect(lambda: self.goto_change(True))
        self._update_mode_button()

    # ---- 对外接口 ----
    def set_title(self, text: str) -> None:
        self.title.setText(text)
        self.title.setToolTip(text)

    def set_message(self, text: str) -> None:
        self.fd = None
        self.msg = text
        self.stats.setText("")
        self._render(False)

    def set_diff(self, fd, keep_scroll: bool = False) -> None:
        if fd.is_binary:
            return self.set_message("二进制文件已变化，不显示内容。")
        if fd.message:
            return self.set_message(fd.message)
        if not fd.added and not fd.deleted:
            return self.set_message("没有可显示的差异。\n（内容相同，或差异只是被忽略的空白 / 换行符。）")
        self.fd = fd
        self.stats.setText(f"<span style='color:#73c991'>+{fd.added}</span>&nbsp;&nbsp;"
                           f"<span style='color:#f14c4c'>−{fd.deleted}</span>"
                           + ("&nbsp;&nbsp;<span style='color:#e2c08d'>（已截断）</span>"
                              if fd.truncated else ""))
        self._render(keep_scroll)

    def show_result(self, fd, keep_scroll: bool = False) -> None:
        self.set_diff(fd, keep_scroll)

    def apply_font_size(self, size: int) -> None:
        for v in (self.unified, self.split.left, self.split.right):
            v.apply_ui_size(size)
        if self.fd is not None:
            self._render(True)

    @staticmethod
    def result_signature(fd):
        return (fd.added, fd.deleted, len(fd.lines), fd.is_binary, fd.message,
                hash(tuple((l.kind, l.text) for l in fd.lines)))

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._update_mode_button()
            if self.fd is not None:
                self._render(False)

    def goto_change(self, forward: bool = True) -> None:
        if self.fd is None:
            return
        (self.split if self.mode == MODE_SPLIT else self.unified).nav.go(forward)

    # ---- 内部 ----
    def _update_mode_button(self):
        self.mode_btn.setText("⇄ 对照" if self.mode == MODE_UNIFIED else "☰ 合并")

    def _active_bar(self):
        return (self.split if self.mode == MODE_SPLIT else self.unified).bar

    def _render(self, keep_scroll: bool) -> None:
        if not self.isVisible():                # 没显示出来的标签页先不渲染，切过去时再画
            self._dirty = True
            self._keep = keep_scroll
            return
        self._dirty = False
        if self.fd is None:
            self.msg_label.setText(self.msg)
            self.stack.setCurrentIndex(0)
            return
        saved = self._active_bar().value() if keep_scroll else 0
        if self.mode == MODE_SPLIT:
            self.split.set_rows(build_split(self.fd.lines))
            self.stack.setCurrentIndex(2)
        else:
            self.unified.set_lines(trim_context(self.fd.lines))
            self.stack.setCurrentIndex(1)
        if keep_scroll and saved:
            bar = self._active_bar()
            QTimer.singleShot(0, lambda: bar.setValue(saved))

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            self._render(self._keep)
