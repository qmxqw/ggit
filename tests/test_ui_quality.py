"""界面层的几处行为测试：字体缩放、文档重读、被删分支回退、拖动时推迟更新、文件列表缓存。

用临时配置文件，不会读写用户真实的 %APPDATA%\\ggit\\config.json。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from ggit import config, gitcore  # noqa: E402
from ggit.async_runner import AsyncRunner  # noqa: E402
from ggit.options import ViewOptions  # noqa: E402
from ggit.ui_docs import Doc, DocArea  # noqa: E402
from ggit.ui_project import FILES_CACHE_MAX, ProjectView  # noqa: E402

ENV = dict(os.environ,
           GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="t@example.com",
           GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="t@example.com",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, env=ENV, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class UIQualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.runner = AsyncRunner()
        cls.tmp = tempfile.mkdtemp()
        cls._orig_config_path = config.config_path
        config.config_path = lambda: os.path.join(cls.tmp, "cfg", "config.json")
        cls.repo = os.path.join(cls.tmp, "repo")
        os.makedirs(cls.repo)
        git(cls.repo, "init", "-q", "-b", "main")
        with open(os.path.join(cls.repo, "a.txt"), "w") as f:
            f.write("a\n")
        git(cls.repo, "add", "-A")
        git(cls.repo, "commit", "-q", "-m", "init")

    @classmethod
    def tearDownClass(cls):
        cls.runner.shutdown()
        config.config_path = cls._orig_config_path
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_font_zoom_keeps_bold(self):
        from ggit.ui_main import MainWindow
        win = MainWindow()
        win._add_project_path(self.repo)
        bold = [l for l in win.findChildren(QLabel) if l.font().bold()]
        self.assertTrue(bold)                               # 区块标题是加粗的
        start = win.options.font_size
        win.change_font_size(2)
        self.assertTrue(all(l.font().bold() for l in bold))
        self.assertEqual({l.font().pointSize() for l in bold}, {start + 2})
        win.reset_font_size()
        win._quitting = True
        win.close()
        win.deleteLater()

    def test_doc_not_reloaded_twice(self):
        area = DocArea(self.runner, ViewOptions(), lambda: None, "")
        doc = Doc("k", "t", "tip", "work", None, "", None)
        self.assertTrue(area._needs_reload(doc))           # 从没读成功过：要读
        doc.task_id = 5
        self.assertFalse(area._needs_reload(doc))          # 正在读：不重复读
        doc.task_id = None
        doc.sig = ("x",)
        doc.loaded_at = time.monotonic()
        self.assertFalse(area._needs_reload(doc))          # 刚读完（双击）：不重读
        doc.loaded_at -= 5
        self.assertTrue(area._needs_reload(doc))           # 工作区文件：过一会儿再打开要重读
        doc.kind = "commit"
        self.assertFalse(area._needs_reload(doc))          # 提交里的文件永远不变
        area.deleteLater()

    def test_deleted_scope_branch_falls_back_to_all(self):
        view = ProjectView(self.repo, "repo", self.runner, ViewOptions())
        view._set_scope_ref("refs/heads/gone")
        self.assertEqual(view._scope, "ref:refs/heads/gone")
        view._gen += 1
        view._on_snapshot(view._gen, gitcore.get_snapshot(self.repo), False)
        self.assertEqual(view._scope, gitcore.SCOPE_ALL)
        self.assertEqual(view.scope_combo.currentIndex(), 0)
        self.assertEqual(view.scope_combo.count(), 2)
        self.assertIn("已不存在", view.info.text())
        view.dispose()
        view.deleteLater()

    def test_files_cache_bounded_but_keeps_expanded(self):
        view = ProjectView(self.repo, "repo", self.runner, ViewOptions())
        view._expanded.add("keep")
        view._files_cache["keep"] = []
        for i in range(FILES_CACHE_MAX + 50):
            view._files_cache[f"c{i}"] = []
            view._trim_files_cache()
        self.assertEqual(len(view._files_cache), FILES_CACHE_MAX)
        self.assertIn("keep", view._files_cache)
        self.assertIn(f"c{FILES_CACHE_MAX + 49}", view._files_cache)
        view.dispose()
        view.deleteLater()

    def test_nav_update_deferred_while_dragging(self):
        from ggit.ui_main import MainWindow
        win = MainWindow()
        win._add_project_path(self.repo)
        win.nav._placeholder.show()                       # 模拟拖动中
        self.assertTrue(win.nav.dragging)
        win._update_button(0)
        self.assertTrue(win._nav_stale)
        win.nav._placeholder.hide()
        win._on_nav_drag_finished()
        self.assertFalse(win._nav_stale)
        win._quitting = True
        win.close()
        win.deleteLater()


if __name__ == "__main__":
    unittest.main()
