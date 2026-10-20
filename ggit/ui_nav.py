"""顶部项目导航：每个项目一个 200 宽的大标签（快捷键序号 + 项目名 + 状态圆点）。

  * 放不下时可以用鼠标滚轮横向滚动（也可用 Ctrl+1…0 / Ctrl+P / “全部项目”菜单切换）
  * 当前项目的标签可以用鼠标拖动换位置：拖动时标签跟着鼠标走，原来的位置显示一个虚线占位槽
  * 鼠标移到标签上立即显示提示（路径、分支、同步状态），移开立即隐藏

ProjectButton 只管自己画什么、什么时候算开始拖动，通过信号告诉 ProjectNav；
排序、占位槽和提示由 ProjectNav 负责。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QEnterEvent, QFont, QMouseEvent, QPainter, QPaintEvent, QWheelEvent
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QScrollArea, QToolTip, QWidget

from . import theme

DRAG_START_DISTANCE = 5     # 按住后移动超过这么多像素才算开始拖动


class ProjectButton(QAbstractButton):
    W, H = 200, 50

    hovered = Signal(bool)      # 鼠标移入 (True) / 移出 (False)
    dragStarted = Signal()
    dragMoved = Signal(int)     # 拖动中：按钮中心在父控件里的 x 坐标
    dragFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.key = ""
        self.name = ""
        self.branch = ""
        self.dirty = False
        self.ahead = 0
        self.behind = 0
        self.error = False
        self.loaded = False
        self.tip = ""
        self._press_pos: QPoint | None = None
        self._dragging = False

    def sizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    @property
    def dragging(self) -> bool:
        return self._dragging

    def set_info(self, key: str, name: str, branch: str, dirty: bool, ahead: int, behind: int,
                 error: bool, loaded: bool, tip: str) -> None:
        self.key, self.name, self.branch = key, name, branch
        self.dirty, self.ahead, self.behind = dirty, ahead, behind
        self.error, self.loaded = error, loaded
        self.tip = tip
        self.update()

    # ---------------------------------------------------------------- 鼠标
    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self.hovered.emit(True)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.hovered.emit(False)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # 只有当前打开的项目可以拖动；别的项目点一下只是切换过去
        left = event.button() == Qt.MouseButton.LeftButton
        self._press_pos = event.position().toPoint() if left and self.isChecked() else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if not self._dragging:
            if (event.position().toPoint() - self._press_pos).manhattanLength() < DRAG_START_DISTANCE:
                super().mouseMoveEvent(event)
                return
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.dragStarted.emit()
            self.update()
        parent = self.parentWidget()
        if parent is None:
            return
        # 跟着鼠标平滑移动（限制在父控件范围内），以按钮中心判断落到哪个位置
        x = parent.mapFromGlobal(event.globalPosition().toPoint()).x() - self._press_pos.x()
        x = max(0, min(max(0, parent.width() - self.width()), x))
        self.move(x, self.y())
        self.raise_()
        self.dragMoved.emit(x + self.width() // 2)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press_pos = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._dragging:                  # 拖动结束：不算一次点击
            self._dragging = False
            self.setDown(False)
            self.update()
            self.dragFinished.emit()
            return
        super().mouseReleaseEvent(event)

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        checked, hover = self.isChecked(), self.underMouse()
        if self._dragging:
            bg = theme.NAV_BG_DRAGGING
        elif checked:
            bg = theme.NAV_BG_CHECKED
        else:
            bg = theme.NAV_BG_HOVER if hover else theme.NAV_BG
        p.fillRect(r, QColor(bg))
        p.setPen(QColor(theme.ACCENT if self._dragging else theme.NAV_BORDER))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        if checked or self._dragging:
            p.fillRect(0, 0, r.width(), 3, QColor(theme.ACCENT))

        base = self.font()
        base_size = base.pointSizeF() if base.pointSizeF() > 0 else float(base.pointSize())
        title = QFont(base)                 # 项目名比常规字体大约 30%
        title.setBold(True)
        title.setPointSizeF(max(10.5, min(14.0, base_size * 1.3)))
        p.setFont(title)
        fm = p.fontMetrics()
        name_y = int((self.H - fm.height()) / 2 + fm.ascent())

        pad_left = 14
        name_x = pad_left
        if self.key:                        # 快捷键序号徽标
            key_font = QFont(base)
            key_font.setBold(True)
            key_font.setPointSizeF(max(8.0, min(10.0, base_size * 0.95)))
            p.setFont(key_font)
            kfm = p.fontMetrics()
            bw, bh = max(20, kfm.horizontalAdvance(self.key) + 8), 20
            by = int((self.H - bh) / 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT if checked else theme.NAV_KEY_BG))
            p.drawRoundedRect(QRectF(pad_left, by, bw, bh), 4, 4)
            p.setPen(QColor(theme.TEXT_BRIGHT))
            ky = by + int((bh - kfm.height()) / 2 + kfm.ascent())
            p.drawText(QPointF(pad_left + (bw - kfm.horizontalAdvance(self.key)) / 2, ky), self.key)
            name_x = pad_left + bw + 10

        right_margin = 16
        dot = theme.MODIFIED if self.dirty else theme.ERROR if self.error else None
        if dot is not None:                 # 黄点：有未提交改动；红点：读取失败
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(dot))
            p.drawEllipse(QPointF(self.W - 14, self.H / 2.0), 3.5, 3.5)
            right_margin = 26

        p.setFont(title)
        p.setPen(QColor(theme.TEXT_BRIGHT if checked else theme.TEXT))
        avail = max(20, int(self.W - name_x - right_margin))
        p.drawText(QPointF(name_x, name_y), fm.elidedText(self.name, Qt.TextElideMode.ElideRight, avail))
        p.end()


class ProjectNav(QScrollArea):
    selected = Signal(int)
    reordered = Signal(int, int)        # (原位置, 新位置)
    dragFinished = Signal()             # 拖动结束（不管位置有没有变）

    MARGIN_LEFT = 4
    SPACING = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(ProjectButton.H + 6)
        self._box = QWidget()
        self._lay = QHBoxLayout(self._box)
        self._lay.setContentsMargins(self.MARGIN_LEFT, 4, 4, 0)
        self._lay.setSpacing(self.SPACING)
        self.setWidget(self._box)
        for w in (self.viewport(), self._box):
            w.setAutoFillBackground(True)
            pal = w.palette()
            pal.setColor(w.backgroundRole(), QColor(theme.NAV_BG))
            w.setPalette(pal)
        self._buttons: list[ProjectButton] = []
        self._drag_from = -1                # 正在拖动的标签原来的位置
        self._tip_btn: ProjectButton | None = None     # 鼠标正停在哪个标签上

        # 拖动时原位置显示的虚线占位槽（样式见 theme.py）
        self._placeholder = QWidget(self._box)
        self._placeholder.setObjectName("dragPlaceholder")
        self._placeholder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._placeholder.setFixedSize(ProjectButton.W, ProjectButton.H)
        self._placeholder.setVisible(False)

    # ---------------------------------------------------------------- 对外接口
    def rebuild(self, count: int) -> None:
        """调整到 count 个标签（内容由调用方用 set_info 填）。"""
        if self.dragging:
            self._lay.removeWidget(self._placeholder)
            self._placeholder.hide()
        while len(self._buttons) > count:
            b = self._buttons.pop()
            self._lay.removeWidget(b)
            b.deleteLater()
        while len(self._buttons) < count:
            b = ProjectButton(self._box)
            b.clicked.connect(lambda _=False, btn=b: self._on_clicked(btn))
            b.pressed.connect(self._hide_tip)
            b.hovered.connect(lambda entered, btn=b: self._on_hover(btn, entered))
            b.dragStarted.connect(lambda btn=b: self._begin_drag(btn))
            b.dragMoved.connect(lambda x, btn=b: self._drag_move(btn, x))
            b.dragFinished.connect(lambda btn=b: self._end_drag(btn))
            self._lay.addWidget(b)
            self._buttons.append(b)
        self._box.setFixedSize(max(1, 2 * self.MARGIN_LEFT + count * (ProjectButton.W + self.SPACING)),
                               ProjectButton.H + 6)

    def button(self, i: int) -> ProjectButton | None:
        return self._buttons[i] if 0 <= i < len(self._buttons) else None

    def buttons(self) -> list[ProjectButton]:
        return list(self._buttons)

    def button_index(self, btn: ProjectButton) -> int:
        return self._buttons.index(btn) if btn in self._buttons else -1

    def set_current(self, i: int) -> None:
        for j, b in enumerate(self._buttons):
            b.setChecked(j == i)
        cur = self.button(i)
        if cur is not None:
            self.ensureWidgetVisible(cur, 20, 0)

    @property
    def dragging(self) -> bool:
        """正在拖动某个标签：按钮顺序和项目列表暂时对不上，别去更新按钮上的内容。"""
        return not self._placeholder.isHidden()

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._hide_tip()
        bar = self.horizontalScrollBar()
        d = event.angleDelta()
        bar.setValue(bar.value() - (d.y() or d.x()))
        event.accept()

    # ---------------------------------------------------------------- 点击 / 提示
    def _on_clicked(self, btn: ProjectButton) -> None:
        idx = self.button_index(btn)
        if idx >= 0:
            self.selected.emit(idx)

    def _on_hover(self, btn: ProjectButton, entered: bool) -> None:
        if entered:
            self._tip_btn = btn
            if btn.tip and not self.dragging:
                # 提示框和标签左对齐，紧贴标签底边（抵消 QToolTip 默认的 +2 / +16 光标偏移）
                top_left = btn.mapToGlobal(QPoint(0, 0))
                QToolTip.showText(QPoint(top_left.x() - 2, top_left.y() + btn.height() - 15), btn.tip, btn)
        elif self._tip_btn is btn:          # 只在离开的正是显示提示的那个标签时才隐藏
            self._tip_btn = None
            self._hide_tip()

    @staticmethod
    def _hide_tip() -> None:
        QToolTip.hideText()

    # ---------------------------------------------------------------- 拖动排序
    def _begin_drag(self, btn: ProjectButton) -> None:
        idx = self.button_index(btn)
        if idx < 0:
            return
        self._hide_tip()
        self._drag_from = idx
        self._lay.removeWidget(btn)                 # 按钮脱离布局，跟着鼠标走
        self._lay.insertWidget(idx, self._placeholder)
        self._placeholder.show()
        btn.raise_()

    def _drag_move(self, btn: ProjectButton, center_x: int) -> None:
        cur = self.button_index(btn)
        if cur < 0 or not self.dragging or len(self._buttons) <= 1:
            return
        pitch = ProjectButton.W + self.SPACING
        target = max(0, min(len(self._buttons) - 1, (center_x - self.MARGIN_LEFT) // pitch))
        if target != cur:                           # 占位槽实时让到新位置
            self._buttons.remove(btn)
            self._buttons.insert(target, btn)
            self._lay.removeWidget(self._placeholder)
            self._lay.insertWidget(target, self._placeholder)
            self.ensureWidgetVisible(self._placeholder, 20, 0)

    def _end_drag(self, btn: ProjectButton) -> None:
        cur = self.button_index(btn)
        if self.dragging:
            self._lay.removeWidget(self._placeholder)
            self._placeholder.hide()
            self._lay.insertWidget(max(cur, 0), btn)
        btn.raise_()
        if cur >= 0 and self._drag_from >= 0 and cur != self._drag_from:
            self.reordered.emit(self._drag_from, cur)
        self._drag_from = -1
        self.dragFinished.emit()
