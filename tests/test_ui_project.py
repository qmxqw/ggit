"""项目页面（ProjectView）和它的各个面板：刷新、提交图分页 / 展开、更改列表、文件历史、分支列表。

在 offscreen 平台上运行真实的界面，用一个临时仓库，只通过公开接口操作和检查。
"""
import unittest

import support
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from support import TempDir, commit, git, init_repo, write

from ggit import gitcore
from ggit.async_runner import AsyncRunner
from ggit.items import FileRow, Row
from ggit.options import ViewOptions
from ggit.ui_project import ProjectView


class ProjectViewTest(support.QtTestCase):
    tmp: TempDir
    repo: str
    oids: list[str]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = TempDir()
        r = cls.repo = init_repo(cls.tmp.sub("repo"))
        cls.oids = []
        for i in range(7):
            write(r, "a.txt", f"v{i}\n")
            cls.oids.append(commit(r, f"commit {i}"))
        git(r, "branch", "feature")
        write(r, "a.txt", "work\n")             # 工作区：一个修改、一个未跟踪
        write(r, "new.txt", "untracked\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.runner = AsyncRunner()
        self.addCleanup(self.runner.shutdown)       # 清理按相反顺序执行：先关页面，再停后台线程
        self.view = self.make_view(self.repo)

    def make_view(self, repo):
        view = ProjectView(repo, "repo", self.runner, ViewOptions())
        view.resize(1100, 700)
        view.show()

        def close():
            view.dispose()
            view.close()
            view.deleteLater()

        self.addCleanup(close)
        return view

    def refreshed(self, view=None):
        view = view or self.view
        view.refresh(force=True)
        support.wait_until(lambda: view.snap is not None and not view.graph_panel.loading)
        return view

    def loaded_oids(self, view=None):
        items = (view or self.view).graph_panel.model.list.items
        return [it.commit.oid for it in items if isinstance(it, Row)]

    # ---------------------------------------------------------------- 刷新
    def test_refresh_fills_status_changes_and_graph(self):
        self.refreshed()
        self.assertEqual(self.view.changes.paths(), ["a.txt", "new.txt"])
        self.assertEqual(self.view.changes.title.text(), "更改  (2)")
        self.assertEqual(self.loaded_oids(), self.oids[::-1])
        self.assertIn("main", self.view.status.branch_btn.text())
        self.assertEqual(self.view.error, "")

    def test_graph_loads_all_pages(self):
        old = gitcore.LOG_PAGE
        gitcore.LOG_PAGE = 3
        self.addCleanup(setattr, gitcore, "LOG_PAGE", old)
        gp = self.refreshed().graph_panel
        while gp.has_more:                      # 滚到底部会自动接着读；这里手动读完
            gp.load_more()
            support.wait_until(lambda: not gp.loading)
        self.assertEqual(self.loaded_oids(), self.oids[::-1])     # 3 + 3 + 1，不重不漏

    def test_changes_title_when_list_is_cut(self):
        changes = [gitcore.FileChange(f"f{i}.txt", None, ".", "M", "M") for i in range(3)]
        self.view.changes.set_changes(changes, 10)
        self.assertEqual(self.view.changes.title.text(), "更改  (10，只列出前 3 个)")

    # ---------------------------------------------------------------- 提交图
    def test_expand_and_collapse_commit(self):
        gp = self.refreshed().graph_panel
        head = self.oids[-1]
        gp.expand(head)
        support.wait_until(lambda: gp.is_expanded(head))
        files = [it.file.path for it in gp.model.list.items if isinstance(it, FileRow)]
        self.assertEqual(files, ["a.txt"])
        gp.collapse(head)
        self.assertFalse(gp.is_expanded(head))
        self.assertFalse(any(isinstance(it, FileRow) for it in gp.model.list.items))

    def test_only_one_list_has_a_selection(self):
        gp = self.refreshed().graph_panel
        gp.view.setCurrentIndex(gp.model.index(0, 0))      # 选中最新的提交
        self.assertEqual(gp.selected_commit_oid(), self.oids[-1])
        self.assertIn("commit 6", self.view.info.text())

        tree = self.view.changes.tree                       # 再点“更改”里的 a.txt
        rect = tree.visualItemRect(tree.topLevelItem(0))
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         rect.center())
        self.assertIsNone(gp.selected_commit_oid())
        self.assertEqual(self.view.changes.current_path(), "a.txt")
        doc = self.view.docs.current_doc()
        self.assertEqual(doc.key, "w|a.txt")
        support.wait_until(lambda: doc.view.fd is not None)
        self.assertEqual((doc.view.fd.added, doc.view.fd.deleted), (1, 1))

    # ---------------------------------------------------------------- 文件历史
    def test_history_panel(self):
        self.refreshed()
        self.view.show_history("a.txt")
        self.assertIs(self.view.left_stack.currentWidget(), self.view.history)
        support.wait_until(lambda: len(self.view.history.entries()) == 7)
        self.assertEqual([e.commit.oid for e in self.view.history.entries()], self.oids[::-1])
        self.view.close_history()
        self.assertIs(self.view.left_stack.currentWidget(), self.view.graph_panel)
        self.assertIsNone(self.view.history.path)

    # ---------------------------------------------------------------- 分支列表
    def test_branch_menu_lists_and_chooses_branches(self):
        self.refreshed()
        menu, gp = self.view.branch_menu, self.view.graph_panel
        support.wait_until(lambda: not menu.loading and bool(menu.branches()))
        self.assertEqual([b.name for b in menu.branches()], ["main", "feature"])     # 当前分支排第一
        menu.rebuild()
        texts = [a.text() for a in menu.actions()]
        self.assertIn("显示全部分支", texts[0])
        self.assertTrue(menu.actions()[0].isChecked())

        next(a for a in menu.actions() if "feature" in a.text()).trigger()
        self.assertEqual(gp.scope, "ref:refs/heads/feature")
        self.assertEqual(gp.scope_combo.currentText(), "只看：feature")
        support.wait_until(lambda: not gp.loading)
        menu.rebuild()                          # 打勾的换成了 feature
        self.assertFalse(menu.actions()[0].isChecked())
        checked = [a.text() for a in menu.actions() if a.isChecked()]
        self.assertEqual(len(checked), 1)
        self.assertTrue(checked[0].startswith("⎇  feature"))

        menu.actions()[0].trigger()
        self.assertEqual(gp.scope, gitcore.SCOPE_ALL)

    def test_deleted_branch_falls_back_to_all(self):
        repo = init_repo(self.tmp.sub("gone"))
        write(repo, "x.txt", "x\n")
        commit(repo, "c1")
        git(repo, "branch", "doomed")
        view = self.refreshed(self.make_view(repo))
        view.graph_panel.choose_branch("refs/heads/doomed")
        self.assertEqual(view.graph_panel.scope, "ref:refs/heads/doomed")
        support.wait_until(lambda: not view.graph_panel.loading)

        git(repo, "branch", "-D", "doomed")
        view.refresh(force=True)
        support.wait_until(lambda: view.graph_panel.scope == gitcore.SCOPE_ALL)
        self.assertEqual(view.graph_panel.scope_combo.count(), 2)          # “只看”那一项也去掉了
        self.assertIn("已不存在", view.info.text())

    # ---------------------------------------------------------------- file 模式
    def test_file_mode_preview(self):
        self.view.set_work_mode("file")
        self.assertIs(self.view.active_docs(), self.view.file_docs)
        self.view.open_file(self.repo + "/new.txt")
        doc = self.view.file_docs.current_doc()
        self.assertEqual(doc.key, "f|new.txt")
        support.wait_until(lambda: doc.sig is not None)
        self.view.set_work_mode("git")
        self.assertIs(self.view.active_docs(), self.view.docs)


if __name__ == "__main__":
    unittest.main()
