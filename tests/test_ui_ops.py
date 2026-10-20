"""项目页面上的写操作：命令面板、暂存 / 提交、Fetch / Pull / Push、分支、冲突时的继续 / 中止，以及主窗口的 Git 菜单。

在 offscreen 平台上运行真实的界面，用一个裸仓库当远端；确认 / 输入对话框换成直接返回答案的函数。
"""
import os
import unittest

import support
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu
from support import GitEnv, TempConfig, TempDir, clone_pair, commit, git, write

from ggit import config, gitcore
from ggit.async_runner import AsyncRunner
from ggit.options import ViewOptions
from ggit.ui_console import ConsolePanel, common_prefix
from ggit.ui_main import MainWindow
from ggit.ui_project import ProjectView


class Answers:
    """替换 GitOps 的对话框：记录问过什么，按预设回答。"""

    def __init__(self, ops):
        self.asked: list[str] = []
        self.yes = True
        self.text: str | None = ""
        self.pick_value = None
        self.choice: int | None = None
        ops.confirm = self.confirm
        ops.ask_text = self.ask_text
        ops.pick = self.pick
        ops.choose = self.choose

    def confirm(self, _parent, title, _text):
        self.asked.append(title)
        return self.yes

    def ask_text(self, _parent, title, _label, _default):
        self.asked.append(title)
        return self.text

    def pick(self, _parent, title, items):
        self.asked.append(title)
        if callable(self.pick_value):
            return self.pick_value(items)
        return self.pick_value

    def choose(self, _parent, title, _text, _options):
        self.asked.append(title)
        return self.choice


class OpsTest(support.QtTestCase):
    def setUp(self):
        self.env = GitEnv()
        self.addCleanup(self.env.restore)
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        self.remote, self.repo, self.other = clone_pair(self.tmp)
        self.runner = AsyncRunner()
        self.addCleanup(self.runner.shutdown)
        self.view = ProjectView(self.repo, "a", self.runner, ViewOptions())
        self.view.resize(1100, 700)
        self.view.show()
        self.addCleanup(self._close)
        self.answers = Answers(self.view.ops)
        self.refreshed()

    def _close(self):
        self.view.dispose()
        self.view.close()
        self.view.deleteLater()

    def refreshed(self):
        self.view.refresh(force=True)
        support.wait_until(lambda: self.view.snap is not None and self.view._snap_task is None)

    def wait_op(self):
        """等写操作结束，并等它触发的刷新完成。"""
        support.wait_until(lambda: not self.view.ops.busy)
        support.wait_until(lambda: self.view._snap_task is None)

    def st(self) -> gitcore.RepoStatus:
        snap = self.view.snap
        assert snap is not None
        return snap.status

    def console_text(self):
        return self.view.console.text()

    # ---------------------------------------------------------------- 命令面板
    def test_console_runs_command_and_keeps_history(self):
        self.view.toggle_console()
        self.assertTrue(self.view.console.isVisible())
        self.view.console.input.setText("git rev-parse --abbrev-ref HEAD")
        self.view.console.input.returnPressed.emit()
        self.wait_op()
        text = self.console_text()
        self.assertIn("$ git rev-parse --abbrev-ref HEAD", text)
        self.assertIn("\nmain\n", text)
        self.assertIn("✔", text)
        self.assertEqual(self.view.command_history(), ["git rev-parse --abbrev-ref HEAD"])

    def test_console_refuses_interactive_and_asks_for_dangerous(self):
        self.view.ops.run_command_text("commit")
        self.assertIn("没有执行", self.console_text())
        self.assertFalse(self.view.ops.busy)

        write(self.repo, "a.txt", "dirty\n")
        self.answers.yes = False
        self.view.ops.run_command_text("reset --hard")
        self.assertEqual(self.answers.asked, ["确认执行"])
        self.assertFalse(self.view.ops.busy)
        with open(os.path.join(self.repo, "a.txt")) as f:
            self.assertEqual(f.read(), "dirty\n")           # 没有执行

        self.answers.yes = True
        self.view.ops.run_command_text("reset --hard")
        self.wait_op()
        with open(os.path.join(self.repo, "a.txt")) as f:
            self.assertEqual(f.read(), "base\n")

    def test_failure_opens_console(self):
        self.assertFalse(self.view.console.isVisible())
        self.view.ops.run_command_text("checkout no-such-branch")
        self.wait_op()
        self.assertTrue(self.view.console.isVisible())
        self.assertIn("✘", self.console_text())

    def test_one_operation_at_a_time(self):
        self.assertTrue(self.view.ops.fetch())
        self.assertTrue(self.view.ops.busy)
        self.assertFalse(self.view.status.fetch_btn.isEnabled())
        self.assertFalse(self.view.ops.fetch())             # 上一个还没结束
        self.assertIn("还在执行", self.console_text())
        self.wait_op()
        self.assertTrue(self.view.status.fetch_btn.isEnabled())

    # ---------------------------------------------------------------- 暂存 / 提交 / 推送
    def test_stage_commit_push_via_panels(self):
        write(self.repo, "a.txt", "changed\n")
        write(self.repo, "new.txt", "new\n")
        self.refreshed()
        changes = self.view.changes.all_changes()
        self.assertEqual({c.path for c in changes}, {"a.txt", "new.txt"})

        # 点勾选框 = 暂存这个文件
        item = self.view.changes.tree.topLevelItem(0)
        assert item is not None
        self.assertEqual(item.checkState(0), Qt.CheckState.Unchecked)
        item.setCheckState(0, Qt.CheckState.Checked)
        self.wait_op()
        staged = [c.path for c in self.st().changes if c.x not in (".", "?")]
        self.assertEqual(staged, ["a.txt"])
        item = self.view.changes.tree.topLevelItem(0)
        assert item is not None
        self.assertEqual(item.checkState(0), Qt.CheckState.Checked)

        # 提交（只提交已暂存的 a.txt）
        self.view.changes.message.setPlainText("改了 a")
        self.view.changes.commit_btn.click()
        self.wait_op()
        self.assertEqual(self.answers.asked, [])           # 有暂存的文件：不用问
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s"), "改了 a")
        self.assertEqual(self.view.changes.message.toPlainText(), "")
        self.assertEqual([c.path for c in self.st().changes], ["new.txt"])
        self.assertEqual(self.st().ahead, 1)
        self.assertEqual(self.view.status.push_btn.text(), "↑ Push 1")

        # Push
        self.view.status.push_btn.click()
        self.wait_op()
        self.assertEqual(self.st().ahead, 0)
        self.assertEqual(git(self.remote, "log", "-1", "--format=%s", "main"), "改了 a")

    def test_commit_with_nothing_staged_asks_to_stage_all(self):
        write(self.repo, "a.txt", "changed\n")
        self.refreshed()
        self.view.ops.commit("")
        self.assertIn("请先填写提交说明", self.console_text())
        self.view.ops.commit("全部提交")
        self.wait_op()
        self.assertEqual(self.answers.asked, ["提交"])
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s"), "全部提交")
        self.assertEqual(self.st().changes, [])

    def test_discard_asks_first(self):
        write(self.repo, "a.txt", "changed\n")
        write(self.repo, "new.txt", "new\n")
        self.refreshed()
        self.answers.yes = False
        self.view.ops.discard(self.view.changes.all_changes())
        self.assertFalse(self.view.ops.busy)
        self.answers.yes = True
        self.view.ops.discard(self.view.changes.all_changes())
        self.wait_op()
        self.assertEqual(self.st().changes, [])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "new.txt")))

    def test_amend_prefills_last_message(self):
        self.view.changes.amend.setChecked(True)
        support.wait_until(lambda: self.view.changes.message.toPlainText() == "base")

    # ---------------------------------------------------------------- Pull / 冲突
    def test_pull_brings_remote_commit(self):
        write(self.other, "b.txt", "from other\n")
        commit(self.other, "from other")
        git(self.other, "push", "-q")
        self.view.ops.fetch()
        self.wait_op()
        self.assertEqual(self.st().behind, 1)
        self.assertEqual(self.view.status.pull_btn.text(), "↓ Pull 1")
        self.view.status.pull_btn.click()
        self.wait_op()
        self.assertEqual(self.st().behind, 0)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "b.txt")))

    def test_conflict_shows_banner_and_abort(self):
        write(self.other, "a.txt", "other\n")
        commit(self.other, "other")
        git(self.other, "push", "-q")
        write(self.repo, "a.txt", "mine\n")
        commit(self.repo, "mine")
        self.view.ops.pull("merge")
        self.wait_op()
        self.assertEqual(self.st().operation, "merge")
        self.assertFalse(self.view.status.op_row.isHidden())
        self.assertIn("1 个冲突", self.view.status.op_label.text())

        self.view.ops.continue_operation()                  # 还有冲突：不执行
        self.assertFalse(self.view.ops.busy)
        self.assertIn("没有解决", self.console_text())

        self.view.status.abort_btn.click()
        self.wait_op()
        self.assertEqual(self.answers.asked, ["中止合并"])
        self.assertEqual(self.st().operation, "")
        self.assertTrue(self.view.status.op_row.isHidden())

    def test_pull_without_upstream_explains(self):
        git(self.repo, "switch", "-q", "-c", "local-only")
        self.refreshed()
        self.assertFalse(self.view.status.pull_btn.isEnabled())
        self.assertFalse(self.view.ops.pull())
        self.assertIn("没有上游分支", self.console_text())

    def test_push_new_branch_asks_and_sets_upstream(self):
        git(self.repo, "switch", "-q", "-c", "feature")
        self.refreshed()
        self.view.ops.push()
        support.wait_until(lambda: self.answers.asked == ["推送新分支"])
        self.wait_op()
        self.assertEqual(self.st().upstream, "origin/feature")

    # ---------------------------------------------------------------- 分支 / 提交图
    def test_branch_actions(self):
        git(self.repo, "branch", "topic")
        self.view.branch_menu.refresh()
        support.wait_until(lambda: any(b.name == "topic" for b in self.view.branch_menu.branches()))
        topic = next(b for b in self.view.branch_menu.branches() if b.name == "topic")

        self.view.branch_action("switch", topic)
        self.wait_op()
        self.assertEqual(self.st().branch, "topic")

        self.answers.text = "from-topic"
        self.view.branch_action("branch", topic)
        self.wait_op()
        self.assertEqual(self.st().branch, "from-topic")

        self.answers.pick_value = lambda items: next(v for _t, v in items if v.name == "main")
        self.view.ops.pick_branch("切换分支", self.view.ops.switch_branch)
        self.wait_op()
        self.assertEqual(self.st().branch, "main")

        self.view.branch_menu.refresh()
        support.wait_until(lambda: not self.view.branch_menu.loading)
        topic = next(b for b in self.view.branch_menu.branches() if b.name == "topic")
        self.view.branch_action("delete", topic)
        self.wait_op()
        self.assertNotIn("topic", git(self.repo, "branch", "--format=%(refname:short)").split())

    def test_remote_branch_switch_creates_tracking_branch(self):
        git(self.other, "switch", "-q", "-c", "remote-only")
        git(self.other, "push", "-q", "-u", "origin", "remote-only")
        self.view.ops.fetch()
        self.wait_op()
        self.view.branch_menu.refresh()
        support.wait_until(lambda: any(b.name == "origin/remote-only" for b in self.view.branch_menu.branches()))
        rb = next(b for b in self.view.branch_menu.branches() if b.name == "origin/remote-only")
        self.view.branch_action("switch", rb)
        self.wait_op()
        self.assertEqual(self.st().branch, "remote-only")
        self.assertEqual(self.st().upstream, "origin/remote-only")

    def test_commit_actions(self):
        write(self.repo, "a.txt", "second\n")
        second = commit(self.repo, "second")
        self.refreshed()
        c = gitcore.Commit(second, [], "", 0, [], "second")
        self.answers.text = "v9"
        self.view.ops.commit_action("tag", c)
        self.wait_op()
        self.assertEqual(git(self.repo, "rev-parse", "v9^{commit}"), second)

        self.view.ops.commit_action("revert", c)
        self.wait_op()
        with open(os.path.join(self.repo, "a.txt")) as f:
            self.assertEqual(f.read(), "base\n")

        head = git(self.repo, "rev-parse", "HEAD")
        self.answers.yes = False
        self.view.ops.commit_action("reset-hard", c)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.answers.yes = True
        self.view.ops.commit_action("reset-hard", c)
        self.wait_op()
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), second)

    def test_stash_push_and_pop(self):
        write(self.repo, "a.txt", "wip\n")
        self.refreshed()
        self.answers.text = "半成品"
        self.view.ops.stash_push()
        self.wait_op()
        self.assertEqual(self.st().changes, [])
        self.answers.pick_value = "stash@{0}"
        self.answers.choice = 0
        self.view.ops.stash_browse()
        self.wait_op()
        support.wait_until(lambda: "储藏" in self.answers.asked)
        self.wait_op()
        self.assertEqual([c.path for c in self.st().changes], ["a.txt"])


class ConsoleWidgetTest(support.QtTestCase):
    def test_progress_lines_overwrite(self):
        c = ConsolePanel()
        self.addCleanup(c.deleteLater)
        c.begin("push")
        for piece in ("Writing:  10%\r", "Writing:  50%\r", "Writing: 100%, done.\n", "done\n"):
            c.append(piece)
        c.finish(True, "Push 完成")
        self.assertEqual(c.text(), "$ git push\nWriting: 100%, done.\ndone\n✔ Push 完成\n")

    def test_unfinished_progress_line_then_finish(self):
        c = ConsolePanel()
        self.addCleanup(c.deleteLater)
        c.begin("fetch")
        c.append("Receiving: 30%\r")
        c.finish(False, "Fetch 已取消")
        self.assertEqual(c.text(), "$ git fetch\nReceiving: 30%\n✘ Fetch 已取消\n")

    def test_history_and_completion(self):
        c = ConsolePanel()
        self.addCleanup(c.deleteLater)
        line = c.input
        line.set_history(["status", "log -1"])
        line.setText("draft")
        line._browse(-1)
        self.assertEqual(line.text(), "log -1")
        line._browse(-1)
        self.assertEqual(line.text(), "status")
        line._browse(1)
        line._browse(1)
        self.assertEqual(line.text(), "draft")

        line.setText("chec")
        line.complete()
        self.assertEqual(line.text(), "checkout ")
        line.branch_source = lambda: ["feature/a", "feature/b", "main"]
        line.setText("checkout fea")
        line.complete()
        self.assertEqual(line.text(), "checkout feature/")
        listed: list[list[str]] = []
        line.completionListed.connect(listed.append)
        line.complete()
        self.assertEqual(listed, [["feature/a", "feature/b"]])
        self.assertEqual(common_prefix(["abc", "abd"]), "ab")


class MainWindowGitTest(support.QtTestCase):
    def setUp(self):
        self.env = GitEnv()
        self.addCleanup(self.env.restore)
        self.cfg = TempConfig()
        self.addCleanup(self.cfg.restore)
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        self.remote, self.a, self.b = clone_pair(self.tmp)

    def make_window(self):
        win = MainWindow(system_integration=False)
        self.addCleanup(win.quit_app)
        return win

    def test_git_menu_shortcuts(self):
        win = self.make_window()
        shortcuts = {a.text(): a.shortcut().toString()
                     for m in win.menuBar().actions() if m.text() == "Git(&G)" and isinstance(menu := m.menu(), QMenu)
                     for a in menu.actions()}
        self.assertEqual(shortcuts["Fetch（获取所有远端）"], "Ctrl+Shift+F")
        self.assertEqual(shortcuts["Pull（拉取）"], "Ctrl+Shift+L")
        self.assertEqual(shortcuts["Push（推送）"], "Ctrl+Shift+K")
        self.assertEqual(shortcuts["提交…（填写提交说明）"], "Ctrl+K")
        self.assertEqual(shortcuts["命令面板（输入 git 命令）"], "Ctrl+`")

    def test_fetch_all_and_config(self):
        win = self.make_window()
        win.add_project_path(self.a)
        win.add_project_path(self.b)            # b 是当前项目；a 的页面也已经建好
        write(self.b, "x.txt", "x\n")
        commit(self.b, "x")
        git(self.b, "push", "-q")
        win.fetch_all()
        support.wait_until(lambda: win._fetch_all is None)
        self.assertIn("2 个项目都已完成", win.statusBar().currentMessage())
        view_a = win.views[self.a]
        support.wait_until(lambda: view_a._snap_task is None and view_a.snap is not None
                           and view_a.snap.status.behind == 1)

        win._set_pull_mode("rebase")
        win.current_view().console.input.remember("log -1")
        win._save_config()
        cfg = config.load()
        self.assertEqual(cfg["options"]["pull_mode"], "rebase")
        self.assertEqual(cfg["cmd_history"][self.b], ["log -1"])

        win2 = self.make_window()
        self.assertEqual(win2.options.pull_mode, "rebase")
        self.assertEqual(win2.current_view().command_history(), ["log -1"])


if __name__ == "__main__":
    unittest.main()
