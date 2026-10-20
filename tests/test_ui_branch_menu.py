"""测试分支下拉菜单及顶部项目按钮排版。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from ggit import config, theme
from ggit.async_runner import AsyncRunner
from ggit.gitcore import BranchInfo, SCOPE_ALL
from ggit.ui_nav import ProjectButton
from ggit.ui_project import ProjectView


class UIBranchMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.runner = AsyncRunner()
        # 用临时配置文件：创建 MainWindow 时会读写配置，不能碰到用户真实的 %APPDATA%\ggit\config.json
        cls.cfg_dir = tempfile.mkdtemp()
        cls._orig_config_path = config.config_path
        config.config_path = lambda: os.path.join(cls.cfg_dir, "config.json")

    @classmethod
    def tearDownClass(cls):
        cls.runner.shutdown()
        config.config_path = cls._orig_config_path
        shutil.rmtree(cls.cfg_dir, ignore_errors=True)

    def test_project_button_paint_and_margins(self):
        btn = ProjectButton()
        btn.set_info("1", "ggit-repo", "main", True, 2, 1, False, True, "tip")
        self.assertEqual(btn.W, 200)
        self.assertEqual(btn.H, 50)
        # 触发 paintEvent 验证排版无异常
        btn.repaint()
        btn.deleteLater()

    def test_branch_button_and_menu_structure(self):
        view = ProjectView(os.path.abspath("."), "ggit", self.runner, None)
        self.assertIsNotNone(view.branch_btn)
        self.assertIsNotNone(view.branch_menu)
        # popupMode 必须是 InstantPopup
        from PySide6.QtWidgets import QToolButton
        self.assertEqual(view.branch_btn.popupMode(), QToolButton.ToolButtonPopupMode.InstantPopup)

        # 模拟分支缓存数据
        view._branches_cache = [
            BranchInfo("main", "refs/heads/main", "local", "1234567890", 1700000000, "origin/main", 1, 0, False, True, "feat: init"),
            BranchInfo("feature", "refs/heads/feature", "local", "2345678901", 1700000100, "", 0, 0, False, False, "test commit"),
            BranchInfo("origin/dev", "refs/remotes/origin/dev", "remote", "3456789012", 1700000200, "", 0, 0, False, False, "remote commit"),
        ]

        view._rebuild_branch_menu()
        actions = view.branch_menu.actions()
        # 验证菜单包含：全部分支、分隔符/本地分支标题、main、feature、分隔符/远端分支标题、origin/dev
        action_texts = [a.text() for a in actions]
        self.assertTrue(any("显示全部分支" in t for t in action_texts))
        self.assertTrue(any("main" in t for t in action_texts))
        self.assertTrue(any("feature" in t for t in action_texts))
        self.assertTrue(any("origin/dev" in t for t in action_texts))

        # 测试点击切换全部分支
        act_all = next(a for a in actions if "显示全部分支" in a.text())
        act_all.trigger()
        self.assertEqual(view._scope, SCOPE_ALL)

        # 测试点击某个特定分支
        act_feat = next(a for a in actions if "feature" in a.text() and not a.isSeparator())
        act_feat.trigger()
        self.assertEqual(view._scope, "ref:refs/heads/feature")

        view.dispose()
        view.deleteLater()

    def test_nav_drag_reorder(self):
        from ggit.ui_nav import ProjectNav
        nav = ProjectNav()
        nav.rebuild(3)
        b0 = nav.button(0)
        b1 = nav.button(1)
        b2 = nav.button(2)
        self.assertEqual(nav._buttons, [b0, b1, b2])

        # 未打开的项目（checked=False）按住移动不会触发拖拽
        b0.setChecked(False)
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QMouseEvent
        press_unselected = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPoint(10, 10), b0.mapToGlobal(QPoint(10, 10)), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        b0.mousePressEvent(press_unselected)
        move_unselected = QMouseEvent(QMouseEvent.Type.MouseMove, QPoint(100, 10), b0.mapToGlobal(QPoint(100, 10)), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        b0.mouseMoveEvent(move_unselected)
        self.assertFalse(b0._is_dragging)

        # 当前打开的项目（checked=True）按住移动正常触发拖拽与实时平移
        b0.setChecked(True)
        b0.mousePressEvent(press_unselected)
        b0.mouseMoveEvent(move_unselected)
        self.assertTrue(b0._is_dragging)
        self.assertGreater(b0.x(), 0)
        self.assertFalse(nav._placeholder.isHidden())

        events = []
        nav.reordered.connect(lambda f, t: events.append((f, t)))

        # 模拟将 0 号按钮拖到 2 号按钮的位置 (x 约为 450)
        nav._handle_drag_move(b0, 450)
        # 拖拽实时让位，b0 应该被移到索引 2，且占位槽显现并处于索引 2
        self.assertEqual(nav._buttons, [b1, b2, b0])
        self.assertFalse(nav._placeholder.isHidden())
        self.assertEqual(nav._lay.indexOf(nav._placeholder), 2)

        # 释放鼠标
        nav._handle_drag_release(b0, start_idx=0)
        self.assertEqual(events, [(0, 2)])
        self.assertTrue(nav._placeholder.isHidden())
        self.assertFalse(nav.dragging)
        self.assertEqual(nav._lay.indexOf(b0), 2)

        nav.deleteLater()

    def test_shortcuts_and_more_btn(self):
        from ggit.ui_main import MainWindow
        win = MainWindow()
        # 验证更多按钮没有原生重复三角：它在 topBar 里，全局样式表把 topBar 里按钮的三角去掉了
        self.assertEqual(win.more_btn.parentWidget().objectName(), "topBar")
        self.assertIn("QFrame#topBar QToolButton::menu-indicator { image: none", theme._STYLE)

        # 验证菜单热键
        acts = win.findChildren(type(win.findChild(object, "")))
        f3_found = False
        f4_found = False
        for a in win.menuBar().actions():
            m = a.menu()
            if m:
                for sub_a in m.actions():
                    if sub_a.text() == "上一处改动":
                        self.assertEqual(sub_a.shortcut().toString(), "F3")
                        f3_found = True
                    elif sub_a.text() == "下一处改动":
                        self.assertEqual(sub_a.shortcut().toString(), "F4")
                        f4_found = True
        self.assertTrue(f3_found)
        self.assertTrue(f4_found)
        win.close()
        win.deleteLater()

    def test_fast_tooltip_response(self):
        from PySide6.QtWidgets import QToolTip
        from PySide6.QtGui import QEnterEvent
        from PySide6.QtCore import QPoint
        from ggit.ui_nav import ProjectNav

        nav = ProjectNav()
        nav.rebuild(2)
        b0 = nav.button(0)
        b1 = nav.button(1)
        b0.set_info("1", "repo0", "main", False, 0, 0, False, True, "Tip content for repo0")
        b1.set_info("2", "repo1", "dev", False, 0, 0, False, True, "Tip content for repo1")

        # 模拟进入 b0：0 延迟秒弹，无需 sleep 等待
        enter0 = QEnterEvent(QPoint(10, 10), QPoint(10, 10), b0.mapToGlobal(QPoint(10, 10)))
        b0.enterEvent(enter0)
        self.app.processEvents()
        self.assertIn("repo0", QToolTip.text())

        # 鼠标平移到 b1 瞬间秒切
        enter1 = QEnterEvent(QPoint(10, 10), QPoint(10, 10), b1.mapToGlobal(QPoint(10, 10)))
        b1.enterEvent(enter1)
        self.app.processEvents()
        self.assertIn("repo1", QToolTip.text())

        # 点击立即隐藏
        nav._hide_tip_now()
        self.assertIsNone(nav._active_tip_btn)
        nav.deleteLater()

    def test_graph_view_instant_tooltip(self):
        from PySide6.QtWidgets import QToolTip
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
        from ggit.ui_graph import CommitModel, GraphView
        from ggit.items import Row
        from ggit.graph import GraphRow
        from ggit.gitcore import Commit

        view = GraphView()
        model = CommitModel()
        view.setModel(model)
        c0 = Commit(oid="1111111111111111111111111111111111111111", parents=[], author="alice", time=1000, subject="commit 1", refs=[])
        c1 = Commit(oid="2222222222222222222222222222222222222222", parents=[], author="bob", time=2000, subject="commit 2", refs=[])
        gr = GraphRow(col=0, color=0)
        model.reset([Row(c0, gr), Row(c1, gr)], head_oid=c0.oid, more=False)
        view.resize(400, 300)
        view.show()
        self.app.processEvents()

        # 模拟鼠标移动到第 0 行 (y ≈ 13)
        ev0 = QMouseEvent(QEvent.Type.MouseMove, QPointF(50, 13), QPointF(50, 13), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        view.mouseMoveEvent(ev0)
        self.app.processEvents()
        self.assertIn("commit 1", QToolTip.text())

        # 模拟鼠标移动到第 1 行 (y ≈ 39)
        ev1 = QMouseEvent(QEvent.Type.MouseMove, QPointF(50, 39), QPointF(50, 39), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        view.mouseMoveEvent(ev1)
        self.app.processEvents()
        self.assertIn("commit 2", QToolTip.text())

        # 模拟鼠标按下隐藏
        press_ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(50, 39), QPointF(50, 39), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        view.mousePressEvent(press_ev)
        self.app.processEvents()
        self.assertEqual(view._last_hover_row, -1)

        view.close()
        view.deleteLater()


if __name__ == "__main__":
    unittest.main()
