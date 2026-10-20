"""顶部项目标签：绘制、拖动排序、悬停提示。"""
import unittest
from unittest import mock

import support
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from ggit.ui_nav import ProjectButton, ProjectNav

PRESS_X = 10


def send_mouse(widget, kind, x, buttons):
    local = QPointF(x, 10)
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    ev = QMouseEvent(kind, local, widget.mapToGlobal(local), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


class NavTest(support.QtTestCase):
    def make_nav(self, count):
        nav = ProjectNav()
        nav.rebuild(count)
        nav.resize(900, 60)
        nav.show()
        self.app.processEvents()
        self.addCleanup(nav.deleteLater)
        return nav

    def test_button_paints(self):
        btn = ProjectButton()
        btn.set_info("1", "ggit-repo", "main", True, 2, 1, False, True, "tip")
        self.assertEqual((btn.width(), btn.height()), (ProjectButton.W, ProjectButton.H))
        self.assertFalse(btn.grab().isNull())          # 触发 paintEvent，排版不出异常
        btn.deleteLater()

    def test_drag_reorder(self):
        nav = self.make_nav(3)
        b0, b1, b2 = nav.buttons()
        moves, finished = [], []
        nav.reordered.connect(lambda f, t: moves.append((f, t)))
        nav.dragFinished.connect(lambda: finished.append(True))
        left = Qt.MouseButton.LeftButton

        # 不是当前项目：按住移动不会拖动
        send_mouse(b0, QEvent.Type.MouseButtonPress, PRESS_X, left)
        send_mouse(b0, QEvent.Type.MouseMove, 100, left)
        self.assertFalse(nav.dragging)
        send_mouse(b0, QEvent.Type.MouseButtonRelease, 100, Qt.MouseButton.NoButton)

        # 当前项目：拖动，标签跟着鼠标走，占位槽实时让位
        nav.set_current(0)
        send_mouse(b0, QEvent.Type.MouseButtonPress, PRESS_X, left)
        send_mouse(b0, QEvent.Type.MouseMove, PRESS_X + 30, left)
        self.assertTrue(nav.dragging)
        self.assertTrue(b0.dragging)
        target_left = 450 - ProjectButton.W // 2                # 把按钮中心拖到 x=450（第 3 个位置）
        send_mouse(b0, QEvent.Type.MouseMove, target_left - b0.x() + PRESS_X, left)
        self.assertEqual(nav.buttons(), [b1, b2, b0])

        send_mouse(b0, QEvent.Type.MouseButtonRelease, 0, Qt.MouseButton.NoButton)
        self.assertFalse(nav.dragging)
        self.assertEqual(moves, [(0, 2)])
        self.assertEqual(finished, [True])
        self.assertIs(nav.button(2), b0)

    def test_tooltip_follows_hover(self):
        # offscreen 平台上 Qt 不会真的隐藏提示框，所以这里检查的是导航栏有没有让 Qt 显示 / 隐藏
        nav = self.make_nav(2)
        b0, b1 = nav.buttons()
        b0.set_info("1", "repo0", "main", False, 0, 0, False, True, "Tip content for repo0")
        b1.set_info("2", "repo1", "dev", False, 0, 0, False, True, "Tip content for repo1")

        def enter(btn):
            p = QPointF(10, 10)
            QApplication.sendEvent(btn, QEnterEvent(p, p, btn.mapToGlobal(p)))

        def leave(btn):
            QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))

        with mock.patch("ggit.ui_nav.QToolTip") as tip:
            enter(b0)                                   # 移入：立即显示，不用等
            self.assertEqual(tip.showText.call_args.args[1], "Tip content for repo0")
            enter(b1)                                   # 移到另一个标签：立即换成它的提示
            self.assertEqual(tip.showText.call_args.args[1], "Tip content for repo1")
            tip.hideText.reset_mock()
            leave(b0)                                   # 迟到的“离开 b0”不能把 b1 的提示藏掉
            tip.hideText.assert_not_called()
            leave(b1)                                   # 离开：立即隐藏
            tip.hideText.assert_called_once()


if __name__ == "__main__":
    unittest.main()
