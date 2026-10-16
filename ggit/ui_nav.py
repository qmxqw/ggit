"""顶部项目导航：每个项目一个 200 宽的大标签。

单行大字居中排版：热键序号 + 项目名（字体增加 100%）。
放不下时可以用鼠标滚轮横向滚动（也可用 Ctrl+1…0 / Ctrl+P / “全部项目”菜单切换）。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QScrollArea, QToolTip, QWidget


class ProjectButton(QAbstractButton):
    W, H = 200, 50

    def __init__(self, parent=None):
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
        self._drag_start_pos = None
        self._is_dragging = False
        self._drag_start_idx = -1

    def sizeHint(self):
        return QSize(self.W, self.H)

    def set_info(self, key, name, branch, dirty, ahead, behind, error, loaded, tip):
        self.key, self.name, self.branch = key, name, branch
        self.dirty, self.ahead, self.behind = dirty, ahead, behind
        self.error, self.loaded = error, loaded
        self.tip = tip
        self.setToolTip("")
        self.update()

    def _nav(self) -> Optional[ProjectNav]:
        if hasattr(self, "nav") and self.nav is not None:
            return self.nav
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, ProjectNav):
                return w
            w = w.parentWidget()
        return None

    def enterEvent(self, event):
        super().enterEvent(event)
        nav = self._nav()
        if nav is not None:
            nav._on_button_enter(self)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        nav = self._nav()
        if nav is not None:
            nav._on_button_leave(self)

    def mousePressEvent(self, event):
        nav = self._nav()
        if nav is not None:
            nav._hide_tip_now()
        if event.button() == Qt.MouseButton.LeftButton:
            # 细节：只允许拖动当前打开的项目；未打开项目点击只触发切换
            if not self.isChecked():
                self._drag_start_pos = None
                self._is_dragging = False
                super().mousePressEvent(event)
                return
            self._drag_start_pos = event.position().toPoint()
            self._is_dragging = False
            nav = self._nav()
            self._drag_start_idx = nav.button_index(self) if nav else -1
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.isChecked() or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            dist = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if not self._is_dragging and dist >= 8:
                self._is_dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
            if self._is_dragging:
                nav = self._nav()
                if nav is not None and getattr(nav, "_box", None) is not None:
                    # 使用全局坐标映射到 _box，绝对稳定且不受 widget 自身换位的影响
                    box_x = nav._box.mapFromGlobal(event.globalPosition().toPoint()).x()
                    nav._handle_drag_move(self, box_x)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_dragging:
            self._is_dragging = False
            self._drag_start_pos = None
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setDown(False)
            self.update()
            nav = self._nav()
            if nav is not None:
                nav._handle_drag_release(self, self._drag_start_idx)
            return
        self._drag_start_pos = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        checked, hover = self.isChecked(), self.underMouse()
        bg_col = QColor("#333338") if self._is_dragging else (QColor("#1e1e1e") if checked else QColor("#3a3a40") if hover else QColor("#2d2d30"))
        p.fillRect(r, bg_col)
        p.setPen(QColor("#0e7ad1") if self._is_dragging else QColor("#3c3c3c"))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        if checked or self._is_dragging:
            p.fillRect(0, 0, r.width(), 3, QColor("#0e7ad1"))

        base = self.font()
        base_size = base.pointSizeF() if base.pointSizeF() > 0 else float(base.pointSize())
        # 项目标题字号：适度放大（比常规字体大约 30%），确保上下与左右有舒适安全边距
        title_size = max(10.5, min(14.0, base_size * 1.3))
        f1 = QFont(base)
        f1.setBold(True)
        f1.setPointSizeF(title_size)

        # ---- 单行排版：精致热键徽标 + 大字项目名（去掉第 2 行分支信息）----
        p.setFont(f1)
        fm1 = p.fontMetrics()
        th = fm1.height()
        y1 = int((self.H - th) / 2 + fm1.ascent())

        pad_left = 14
        if self.key:
            k_font = QFont(base)
            k_font.setBold(True)
            k_font.setPointSizeF(max(8.0, min(10.0, base_size * 0.95)))
            p.setFont(k_font)
            kfm = p.fontMetrics()
            bw = max(20, kfm.horizontalAdvance(self.key) + 8)
            bh = 20
            by = int((self.H - bh) / 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#0e7ad1") if checked else QColor("#4a4a50"))
            p.drawRoundedRect(QRectF(pad_left, by, bw, bh), 4, 4)
            p.setPen(QColor("#ffffff"))
            ky = by + int((bh - kfm.height()) / 2 + kfm.ascent())
            p.drawText(QPointF(pad_left + (bw - kfm.horizontalAdvance(self.key)) / 2, ky), self.key)
            name_x = pad_left + bw + 10
        else:
            name_x = pad_left

        right_margin = 16
        if self.dirty:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#e2c08d"))
            p.drawEllipse(QPointF(self.W - 14, self.H / 2.0), 3.5, 3.5)
            right_margin = 26
        elif self.error:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#f14c4c"))
            p.drawEllipse(QPointF(self.W - 14, self.H / 2.0), 3.5, 3.5)
            right_margin = 26

        p.setFont(f1)
        p.setPen(QColor("#ffffff") if checked else QColor("#d4d4d4"))
        avail_w = max(20, int(self.W - name_x - right_margin))
        name = fm1.elidedText(self.name, Qt.TextElideMode.ElideRight, avail_w)
        p.drawText(QPointF(name_x, y1), name)
        p.end()


class ProjectNav(QScrollArea):
    selected = Signal(int)
    reordered = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(ProjectButton.H + 6)
        self._box = QWidget()
        self._lay = QHBoxLayout(self._box)
        self._lay.setContentsMargins(4, 4, 4, 0)
        self._lay.setSpacing(2)
        self.setWidget(self._box)
        for w in (self.viewport(), self._box):
            w.setAutoFillBackground(True)
            pal = w.palette()
            pal.setColor(w.backgroundRole(), QColor("#2d2d30"))
            w.setPalette(pal)
        self._buttons: list = []

        # Tooltip 控制：0 延迟即时显示与瞬间隐藏，彻底干掉等待
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.timeout.connect(self._on_tip_timeout)

        self._tip_leave_timer = QTimer(self)
        self._tip_leave_timer.setSingleShot(True)
        self._tip_leave_timer.timeout.connect(self._on_tip_leave_timeout)

        self._hovered_btn: Optional[ProjectButton] = None
        self._active_tip_btn: Optional[ProjectButton] = None

    def _on_button_enter(self, btn: ProjectButton) -> None:
        if self._tip_leave_timer.isActive():
            self._tip_leave_timer.stop()
        if self._tip_timer.isActive():
            self._tip_timer.stop()
        self._hovered_btn = btn
        if not getattr(btn, "tip", None):
            return
        # 0 等待即时秒弹
        self._show_tip(btn)

    def _on_button_leave(self, btn: ProjectButton) -> None:
        if self._hovered_btn == btn:
            self._hovered_btn = None
            # 鼠标移出立即瞬间隐藏，无残留等待
            self._hide_tip_now()

    def _on_tip_timeout(self) -> None:
        if self._hovered_btn is not None and getattr(self._hovered_btn, "tip", None):
            self._show_tip(self._hovered_btn)

    def _on_tip_leave_timeout(self) -> None:
        if self._hovered_btn is None:
            self._hide_tip_now()

    def _show_tip(self, btn: ProjectButton) -> None:
        if not getattr(btn, "tip", None):
            return
        # 弹出的信息框与项目标题本身左对齐，并精准紧贴底边：
        # 补偿 Qt 的 QToolTip 内部默认附加的 (+2px X, +16px Y) 光标避让偏移
        top_left = btn.mapToGlobal(QPoint(0, 0))
        btn_bottom_y = top_left.y() + btn.height()
        pos = QPoint(top_left.x() - 2, btn_bottom_y - 15)
        QToolTip.showText(pos, btn.tip, btn)
        self._active_tip_btn = btn

    def _hide_tip_now(self) -> None:
        if self._tip_timer.isActive():
            self._tip_timer.stop()
        if self._tip_leave_timer.isActive():
            self._tip_leave_timer.stop()
        self._active_tip_btn = None
        QToolTip.hideText()

    def button_index(self, btn: ProjectButton) -> int:
        return self._buttons.index(btn) if btn in self._buttons else -1

    def _on_btn_clicked(self, btn: ProjectButton) -> None:
        idx = self.button_index(btn)
        if idx >= 0:
            self.selected.emit(idx)

    def _handle_drag_move(self, btn: ProjectButton, box_x: int) -> None:
        self._hide_tip_now()
        cur_idx = self.button_index(btn)
        if cur_idx < 0:
            return
        n = len(self._buttons)
        if n <= 1:
            return
        pitch = ProjectButton.W + 2
        margin_left = 4
        target_idx = int((box_x - margin_left) // pitch)
        target_idx = max(0, min(n - 1, target_idx))
        if target_idx != cur_idx:
            self._buttons.remove(btn)
            self._buttons.insert(target_idx, btn)
            self._lay.removeWidget(btn)
            self._lay.insertWidget(target_idx, btn)
            self.ensureWidgetVisible(btn, 20, 0)

    def _handle_drag_release(self, btn: ProjectButton, start_idx: int) -> None:
        cur_idx = self.button_index(btn)
        if cur_idx >= 0 and cur_idx != start_idx:
            self.reordered.emit(start_idx, cur_idx)

    def rebuild(self, count: int) -> None:
        while len(self._buttons) > count:
            b = self._buttons.pop()
            self._lay.removeWidget(b)
            b.deleteLater()
        while len(self._buttons) < count:
            b = ProjectButton(self._box)
            b.nav = self
            b.clicked.connect(lambda _=False, btn=b: self._on_btn_clicked(btn))
            self._lay.addWidget(b)
            self._buttons.append(b)
        self._box.setFixedSize(max(1, 8 + count * (ProjectButton.W + 2)), ProjectButton.H + 6)

    def button(self, i: int):
        return self._buttons[i] if 0 <= i < len(self._buttons) else None

    def set_current(self, i: int) -> None:
        for j, b in enumerate(self._buttons):
            b.setChecked(j == i)
        b = self.button(i)
        if b is not None:
            self.ensureWidgetVisible(b, 20, 0)

    def wheelEvent(self, event):
        self._hide_tip_now()
        bar = self.horizontalScrollBar()
        d = event.angleDelta()
        bar.setValue(bar.value() - (d.y() or d.x()))
        event.accept()
