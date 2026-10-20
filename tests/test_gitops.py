"""gitops（写操作层）的测试：输出切分、命令拆分与检查、用真实的远端仓库做 fetch / pull / push / 冲突。

运行：  python -m pytest   或   python -m unittest discover -s tests -v
"""
import os
import unittest

import support
from support import GitEnv, TempDir, clone_pair, commit, git, write

from ggit import gitcore, gitops
from ggit.cancel import CancellationToken, GitCancelledError, set_current_cancel_token
from ggit.gitops import OutputSplitter, check_command, format_command, split_command


class OutputSplitterTest(unittest.TestCase):
    def test_lines_and_progress(self):
        sp = OutputSplitter()
        out = sp.feed(b"Counting:  50%\rCounting: 100%\rCounting: 100%, done.\nTotal 3\n")
        self.assertEqual(out, ["Counting:  50%\r", "Counting: 100%\r", "Counting: 100%, done.\n", "Total 3\n"])
        self.assertEqual(sp.text(), "Counting: 100%, done.\nTotal 3")     # 进度行只留最后一次

    def test_crlf_split_across_chunks(self):
        sp = OutputSplitter()
        self.assertEqual(sp.feed(b"line1\r"), [])          # 还不知道 \r 后面是不是 \n
        self.assertEqual(sp.feed(b"\nline2"), ["line1\n"])
        self.assertEqual(sp.flush(), ["line2\n"])
        self.assertEqual(sp.text(), "line1\nline2")

    def test_multibyte_split_across_chunks(self):
        data = "你好，世界\n".encode()
        sp = OutputSplitter()
        self.assertEqual(sp.feed(data[:4]), [])
        self.assertEqual(sp.feed(data[4:]), ["你好，世界\n"])

    def test_gbk_line(self):
        sp = OutputSplitter()
        self.assertEqual(sp.feed("中文\n".encode("gbk")), ["中文\n"])


class CommandTest(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_command("git log --oneline -5"), ["log", "--oneline", "-5"])
        self.assertEqual(split_command('commit -m "fix: a b"'), ["commit", "-m", "fix: a b"])
        self.assertEqual(split_command("commit -m 'x \"y\"'"), ["commit", "-m", 'x "y"'])
        self.assertEqual(split_command(r'add C:\work\a.txt ""'), ["add", r"C:\work\a.txt", ""])
        self.assertEqual(split_command(r'commit -m "say \"hi\""'), ["commit", "-m", 'say "hi"'])
        self.assertEqual(split_command("  GIT   status  "), ["status"])
        for bad in ("", "git", "   ", 'commit -m "x'):
            with self.assertRaises(gitops.CommandError):
                split_command(bad)

    def test_format_roundtrip(self):
        for args in (["commit", "-m", "a b"], ["log", "-1"], ["tag", 'x"y'], ["add", ""]):
            self.assertEqual(split_command(format_command(args)), args)

    def test_subcommand_skips_global_options(self):
        self.assertEqual(gitops.subcommand(["-C", "dir", "-c", "a=b", "--no-pager", "log", "-1"]), ("log", ["-1"]))
        self.assertEqual(gitops.subcommand(["--version"]), ("", []))

    def test_blocked(self):
        for cmd in ("commit", "commit -a", "commit --amend", "rebase -i HEAD~3", "add -p", "add --patch x",
                    "checkout -p", "gui", "mergetool", "tag -a v1", "commit -m x -e", "clean -i"):
            self.assertTrue(check_command(split_command(cmd)).blocked, cmd)
        for cmd in ('commit -m "x"', "commit -am x", "commit --amend --no-edit", "commit -mfixed",
                    "commit -F msg.txt", "rebase main", "tag v1", "tag -a v1 -m x", "add -A", "status"):
            self.assertFalse(check_command(split_command(cmd)).blocked, cmd)

    def test_danger(self):
        for cmd in ("reset --hard", "reset --hard HEAD~1", "clean -fd", "clean -f", "push -f", "push --force",
                    "push origin +main", "push origin :old", "push --delete origin x", "branch -D x",
                    "branch -d -f x", "checkout -- a.txt", "checkout .", "restore a.txt", "stash drop",
                    "stash clear", "switch -f main", "tag -d v1", "remote remove origin"):
            self.assertTrue(check_command(split_command(cmd)).danger, cmd)
        for cmd in ("reset", "reset --soft HEAD~1", "clean -n", "clean -nfd", "push", "push -u origin main",
                    "branch -d x", "checkout main", "checkout -b new", "restore --staged a.txt", "stash",
                    "stash pop", "log -f", "commit -m fix"):
            self.assertFalse(check_command(split_command(cmd)).danger, cmd)

    def test_writes(self):
        self.assertFalse(check_command(["status"]).writes)
        self.assertFalse(check_command(["log", "-1"]).writes)
        self.assertTrue(check_command(["commit", "-m", "x"]).writes)
        self.assertTrue(check_command(["fetch"]).writes)


class RemoteTest(unittest.TestCase):
    """真实的远端（裸仓库）+ 两份克隆。"""
    tmp: TempDir

    def setUp(self):
        self.env = GitEnv()
        self.addCleanup(self.env.restore)
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        self.remote, self.a, self.b = clone_pair(self.tmp)

    def run_ok(self, repo, args, **kw):
        res = gitops.run_git_stream(repo, args, **kw)
        self.assertTrue(res.ok, res.output)
        return res

    def test_push_then_pull(self):
        write(self.a, "a.txt", "from a\n")
        self.run_ok(self.a, ["commit", "-am", "change in a"])
        st = gitcore.get_status(self.a)
        self.assertEqual(st.ahead, 1)
        pieces: list[str] = []
        res = self.run_ok(self.a, gitops.push_args(st), on_output=pieces.append)
        self.assertTrue(pieces)
        self.assertIn("main -> main", res.output)
        self.assertEqual(gitcore.get_status(self.a).ahead, 0)

        self.run_ok(self.b, gitops.fetch_args())
        self.assertEqual(gitcore.get_status(self.b).behind, 1)
        self.run_ok(self.b, gitops.pull_args())
        with open(os.path.join(self.b, "a.txt")) as f:
            self.assertEqual(f.read(), "from a\n")

    def test_push_new_branch_sets_upstream(self):
        git(self.a, "switch", "-q", "-c", "feature")
        st = gitcore.get_status(self.a)
        self.assertIsNone(st.upstream)
        remote = gitops.push_remote(self.a)
        self.assertEqual(remote, "origin")
        args = gitops.push_args(st, remote)
        self.assertEqual(args[-3:], ["-u", "origin", "feature"])
        self.run_ok(self.a, args)
        self.assertEqual(gitcore.get_status(self.a).upstream, "origin/feature")

    def test_push_args_errors(self):
        st = gitcore.get_status(self.a)
        st.branch = None
        with self.assertRaises(gitcore.GitError):
            gitops.push_args(st)
        git(self.a, "remote", "remove", "origin")
        with self.assertRaises(gitcore.GitError):
            gitops.push_remote(self.a)

    def test_pull_ff_only_refuses_divergence_and_merge_conflict_can_be_aborted(self):
        write(self.a, "a.txt", "a side\n")
        commit(self.a, "a side")
        git(self.a, "push", "-q")
        write(self.b, "a.txt", "b side\n")
        commit(self.b, "b side")
        res = gitops.run_git_stream(self.b, gitops.pull_args(gitops.PULL_FF_ONLY))
        self.assertFalse(res.ok)
        self.assertEqual(gitcore.get_status(self.b).operation, "")

        res = gitops.run_git_stream(self.b, gitops.pull_args(gitops.PULL_MERGE))
        self.assertFalse(res.ok)                    # 冲突
        st = gitcore.get_status(self.b)
        self.assertEqual(st.operation, "merge")
        self.assertEqual(st.conflicts, 1)
        self.run_ok(self.b, gitops.ABORT_ARGS["merge"])
        self.assertEqual(gitcore.get_status(self.b).operation, "")

    def test_continue_merge_after_resolving(self):
        git(self.a, "switch", "-q", "-c", "other")
        write(self.a, "a.txt", "other\n")
        commit(self.a, "other")
        git(self.a, "switch", "-q", "main")
        write(self.a, "a.txt", "main\n")
        commit(self.a, "main")
        self.assertFalse(gitops.run_git_stream(self.a, ["merge", "other"]).ok)
        write(self.a, "a.txt", "resolved\n")
        self.run_ok(self.a, gitops.stage_args(["a.txt"]))
        self.run_ok(self.a, gitops.CONTINUE_ARGS["merge"], accept_default_message=True)
        self.assertEqual(gitcore.get_status(self.a).operation, "")
        self.assertEqual(len(git(self.a, "log", "-1", "--format=%P").split()), 2)

    def test_editor_is_never_opened(self):
        write(self.a, "a.txt", "x\n")
        git(self.a, "add", "a.txt")
        res = gitops.run_git_stream(self.a, ["commit"])        # 没有 -m：不能卡住等编辑器
        self.assertFalse(res.ok)
        self.assertIn("ggit: no interactive editor", res.output)

    def test_stage_unstage_discard_commit(self):
        write(self.a, "a.txt", "changed\n")
        write(self.a, "new.txt", "new\n")
        self.run_ok(self.a, gitops.stage_args(["a.txt", "new.txt"]))
        xs = {c.path: c.x for c in gitcore.get_status(self.a).changes}
        self.assertEqual(xs, {"a.txt": "M", "new.txt": "A"})
        self.run_ok(self.a, gitops.unstage_args(["new.txt"], has_head=True))
        kinds = {c.path: c.kind for c in gitcore.get_status(self.a).changes}
        self.assertEqual(kinds["new.txt"], "U")
        res = gitops.run_sequence(self.a, gitops.discard_commands(["a.txt"], ["new.txt"]))
        self.assertTrue(res.ok, res.output)
        self.assertEqual(gitcore.get_status(self.a).changes, [])

        write(self.a, "b.txt", "b\n")
        res = gitops.run_sequence(self.a, [["add", "-A"], gitops.commit_args("第一行\n\n正文")])
        self.assertTrue(res.ok, res.output)
        self.assertEqual(gitops.last_commit_message(self.a), "第一行\n\n正文")
        self.run_ok(self.a, gitops.commit_args("改过的说明", amend=True))
        self.assertEqual(gitops.last_commit_message(self.a), "改过的说明")

    def test_discard_staged_new_file(self):
        write(self.a, "n.txt", "n\n")
        git(self.a, "add", "n.txt")
        res = gitops.run_sequence(self.a, gitops.discard_commands(["n.txt"], []))
        self.assertTrue(res.ok, res.output)
        self.assertFalse(os.path.exists(os.path.join(self.a, "n.txt")))

    def test_unstage_without_head(self):
        repo = support.init_repo(self.tmp.sub("empty"))
        write(repo, "x.txt", "x\n")
        git(repo, "add", "x.txt")
        self.run_ok(repo, gitops.unstage_args(["x.txt"], has_head=False))
        self.assertEqual(gitcore.get_status(repo).changes[0].kind, "U")

    def test_stash_list(self):
        write(self.a, "a.txt", "wip\n")
        self.run_ok(self.a, ["stash", "push", "-m", "我的储藏"])
        stashes = gitops.stash_list(self.a)
        self.assertEqual(len(stashes), 1)
        self.assertEqual(stashes[0][0], "stash@{0}")
        self.assertIn("我的储藏", stashes[0][1])

    def test_sequence_stops_at_failure(self):
        lines: list[str] = []
        res = gitops.run_sequence(self.a, [["checkout", "no-such-branch"], ["commit", "-m", "x"]], lines.append)
        self.assertFalse(res.ok)
        self.assertEqual(res.args, ["checkout", "no-such-branch"])
        self.assertFalse(any(ln.startswith("$ git commit") for ln in lines))

    def test_cancelled_before_start(self):
        token = CancellationToken()
        token.cancel()
        set_current_cancel_token(token)
        try:
            with self.assertRaises(GitCancelledError):
                gitops.run_git_stream(self.a, ["status"])
        finally:
            set_current_cancel_token(None)

    def test_missing_directory(self):
        with self.assertRaises(gitcore.GitError):
            gitops.run_git_stream(self.tmp.sub("nope"), ["status"])


if __name__ == "__main__":
    unittest.main()
