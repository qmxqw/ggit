"""主窗口：不碰真实桌面（托盘 / 热键）、菜单快捷键、字体缩放、配置保存失败提示、拖动标签时推迟更新。

用临时配置文件和临时仓库，在 offscreen 平台上运行。
"""
import os
import unittest

import support
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QMenu
from support import TempConfig, TempDir, commit, init_repo, write

from ggit import config, theme
from ggit.options import FONT_DEFAULT
from ggit.ui_main import MainWindow


def send_mouse(widget, kind, x, buttons):
    """给 widget 发一个鼠标事件（坐标是 widget 里的 x，y 固定 10）。"""
    local = QPointF(x, 10)
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    ev = QMouseEvent(kind, local, widget.mapToGlobal(local), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


class MainWindowTest(support.QtTestCase):
    tmp: TempDir
    repo: str

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = TempDir()
        cls.repo = init_repo(cls.tmp.sub("repo"))
        write(cls.repo, "a.txt", "1\n")
        commit(cls.repo, "c1")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.cfg = TempConfig()
        self.addCleanup(self.cfg.restore)
        self.win = MainWindow(system_integration=False)
        self.addCleanup(self.win.quit_app)

    def test_no_tray_and_no_global_hotkey(self):
        self.assertIsNone(self.win.tray)
        self.assertFalse(self.win.hotkey.registered)

    def test_menu_shortcuts(self):
        shortcuts = {a.text(): a.shortcut().toString()
                     for m in self.win.menuBar().actions() if isinstance(menu := m.menu(), QMenu)
                     for a in menu.actions()}
        self.assertEqual(shortcuts["上一处改动"], "F3")
        self.assertEqual(shortcuts["下一处改动"], "F4")
        self.assertEqual(shortcuts["切换工作模式（git / 文件）"], "Alt+F")

    def test_more_button_has_no_native_arrow(self):
        # “全部项目 ▾”按钮在 topBar 里，全局样式表去掉了 topBar 里按钮自带的下拉三角
        parent = self.win.more_btn.parentWidget()
        assert parent is not None
        self.assertEqual(parent.objectName(), "topBar")
        self.assertIn("QFrame#topBar QToolButton::menu-indicator { image: none", theme.stylesheet())

    def test_add_project_and_switch(self):
        self.win.add_project_path(self.repo)
        self.assertEqual([p.path for p in self.win.projects], [self.repo])
        view = self.win.current_view()
        self.assertEqual(view.path, self.repo)
        support.wait_until(lambda: view.snap is not None)
        self.win.add_project_path(self.repo)                    # 重复添加：只是切过去
        self.assertEqual(len(self.win.projects), 1)
        self.assertEqual(self.win.windowTitle(), "ggit - repo")

    def test_font_zoom_keeps_bold_and_rescales_hints(self):
        self.win.add_project_path(self.repo)
        bold = [lbl for lbl in self.win.findChildren(QLabel) if lbl.font().bold()]
        self.assertTrue(bold)                                   # 区块标题是加粗的
        start = self.win.options.font_size
        self.win.change_font_size(2)
        self.assertTrue(all(lbl.font().bold() for lbl in bold))
        self.assertEqual({lbl.font().pointSize() for lbl in bold}, {start + 2})
        self.assertIn(f"font-size: {start + 3}pt", self.app.styleSheet())    # 提示文字跟着变大
        self.win.reset_font_size()
        self.assertEqual(self.win.options.font_size, FONT_DEFAULT)
        self.assertIn(f"font-size: {FONT_DEFAULT + 1}pt", self.app.styleSheet())

    def test_config_save_failure_is_reported(self):
        blocker = self.cfg.dir.sub("blocker")
        write(self.cfg.dir.path, "blocker", "x")               # 父目录的位置被一个文件占了：一定写不进去
        config.config_path = lambda: os.path.join(blocker, "config.json")
        self.win.toggle_diff_mode()
        self.assertIn("配置保存失败", self.win.statusBar().currentMessage())
        self.win.toggle_diff_mode()

    def test_tab_updates_wait_until_drag_ends(self):
        self.win.add_project_path(self.repo)
        self.win.show()
        nav = self.win.nav
        btn = nav.button(0)
        assert btn is not None
        self.assertTrue(btn.isChecked())
        left = Qt.MouseButton.LeftButton
        send_mouse(btn, QEvent.Type.MouseButtonPress, 10, left)
        send_mouse(btn, QEvent.Type.MouseMove, 40, left)         # 开始拖动
        self.assertTrue(nav.dragging)

        view = self.win.current_view()
        support.wait_until(lambda: view.snap is not None)       # 拖动中读到了项目状态
        self.assertEqual(btn.branch, "")                        # 先不更新标签

        send_mouse(btn, QEvent.Type.MouseButtonRelease, 40, Qt.MouseButton.NoButton)
        self.assertFalse(nav.dragging)
        self.assertIs(nav.button(0), btn)
        self.assertEqual(btn.branch, "main")                    # 松手后补上


if __name__ == "__main__":
    unittest.main()
