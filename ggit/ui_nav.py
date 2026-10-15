"""顶部项目导航：每个项目一个 200 宽、两行高的大标签。

第一行：热键序号 + 项目名；第二行：分支、是否有未提交改动（●）、领先/落后远端（↑ ↓）。
放不下时可以用鼠标滚轮横向滚动（也可用 Ctrl+1…0 / Ctrl+P / “全部项目”菜单切换）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QScrollArea, QWidget


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

    def sizeHint(self):
        return QSize(self.W, self.H)

    def set_info(self, key, name, branch, dirty, ahead, behind, error, loaded, tip):
        self.key, self.name, self.branch = key, name, branch
        self.dirty, self.ahead, self.behind = dirty, ahead, behind
        self.error, self.loaded = error, loaded
        self.setToolTip(tip)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        checked, hover = self.isChecked(), self.underMouse()
        p.fillRect(r, QColor("#1e1e1e") if checked else QColor("#3a3a40") if hover else QColor("#2d2d30"))
        p.setPen(QColor("#3c3c3c"))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        if checked:
            p.fillRect(0, 0, r.width(), 3, QColor("#0e7ad1"))

        base = self.font()
        f1 = QFont(base)
        f1.setBold(True)
        if base.pointSizeF() > 0:
            f1.setPointSizeF(base.pointSizeF() + 1.0)
        f2 = QFont(base)
        if base.pointSizeF() > 0:
            f2.setPointSizeF(max(7.0, base.pointSizeF() - 0.5))

        # ---- 第一行：热键徽标 + 项目名 ----
        p.setFont(f1)
        fm1 = p.fontMetrics()
        y1 = 8 + fm1.ascent()
        bw = 18
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0e7ad1") if checked else QColor("#4a4a50"))
        p.drawRoundedRect(QRectF(10, 8, bw, fm1.height()), 3, 3)
        p.setPen(QColor("#ffffff"))
        p.drawText(QPointF(10 + (bw - fm1.horizontalAdvance(self.key)) / 2, y1), self.key)
        name_x = 10 + bw + 8
        p.setPen(QColor("#ffffff") if checked else QColor("#d4d4d4"))
        name = fm1.elidedText(self.name, Qt.TextElideMode.ElideRight, self.W - name_x - 10)
        p.drawText(QPointF(name_x, y1), name)

        # ---- 第二行：分支 + 状态标记 ----
        p.setFont(f2)
        fm2 = p.fontMetrics()
        y2 = self.H - 8 - fm2.descent()
        x = 10.0
        if self.error:
            p.setPen(QColor("#f14c4c"))
            p.drawText(QPointF(x, y2), "无法读取这个项目")
        elif not self.loaded:
            p.setPen(QColor("#808080"))
            p.drawText(QPointF(x, y2), "加载中…")
        else:
            runs = []
            if self.dirty:
                runs.append(("●", QColor("#e2c08d")))
            if self.ahead:
                runs.append((f"↑{self.ahead}", QColor("#73c991")))
            if self.behind:
                runs.append((f"↓{self.behind}", QColor("#ff9e64")))
            tail_w = sum(fm2.horizontalAdvance(t) + 8 for t, _ in runs)
            branch = fm2.elidedText("⎇ " + self.branch, Qt.TextElideMode.ElideRight,
                                    max(20, int(self.W - 20 - tail_w)))
            p.setPen(QColor("#a0a0a0"))
            p.drawText(QPointF(x, y2), branch)
            x += fm2.horizontalAdvance(branch) + 8
            for text, color in runs:
                p.setPen(color)
                p.drawText(QPointF(x, y2), text)
                x += fm2.horizontalAdvance(text) + 8
        p.end()


class ProjectNav(QScrollArea):
    selected = Signal(int)

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

    def rebuild(self, count: int) -> None:
        while len(self._buttons) > count:
            b = self._buttons.pop()
            self._lay.removeWidget(b)
            b.deleteLater()
        while len(self._buttons) < count:
            i = len(self._buttons)
            b = ProjectButton(self._box)
            b.clicked.connect(lambda _=False, i=i: self.selected.emit(i))
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
        bar = self.horizontalScrollBar()
        d = event.angleDelta()
        bar.setValue(bar.value() - (d.y() or d.x()))
        event.accept()
