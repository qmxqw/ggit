"""gitcore / graph 的测试：用真实的临时 Git 仓库验证。

运行：  python -m unittest discover -s tests -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ggit import gitcore  # noqa: E402
from ggit.graph import GraphLayout  # noqa: E402

ENV = dict(os.environ,
           GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="t@example.com",
           GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="t@example.com",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, env=ENV, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def write(repo, name, data):
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(os.path.join(repo, name), mode) as f:
        f.write(data)


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


class CoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        r = cls.repo = os.path.join(cls.tmp, "r")
        os.makedirs(r)
        git(r, "init", "-q", "-b", "main")
        write(r, "a.txt", "l1\nl2\nl3\n")
        write(r, "gbk.txt", "你好，世界\n第二行\n".encode("gbk"))
        commit(r, "init 初始提交")
        cls.root = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=r, env=ENV).decode().strip()
        git(r, "checkout", "-q", "-b", "feature")
        write(r, "a.txt", "l1\nl2 changed\nl3\n")
        commit(r, "feature change")
        git(r, "checkout", "-q", "main")
        write(r, "c.txt", "c\n")
        commit(r, "main work")
        git(r, "merge", "-q", "--no-ff", "feature", "-m", "merge feature")
        cls.merge = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=r, env=ENV).decode().strip()
        git(r, "mv", "a.txt", "b.txt")
        write(r, "b.txt", "l1\nl2 changed\nl3\nl4\n")
        commit(r, "rename a to b")
        cls.rename = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=r, env=ENV).decode().strip()
        git(r, "tag", "v1")
        # 工作区改动
        write(r, "b.txt", "l1\nl2 changed\nl3\nl4\nwip\n")
        write(r, "new.txt", "新文件\n第二行\n")
        write(r, "gbk.txt", "你好，世界\n改动后的第二行\n".encode("gbk"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- status ----
    def test_status(self):
        snap = gitcore.get_snapshot(self.repo)
        st = snap.status
        self.assertEqual(st.branch, "main")
        self.assertEqual(len(st.head_oid), 40)
        self.assertIsNone(st.upstream)
        kinds = {c.path: c.kind for c in st.changes}
        self.assertEqual(kinds, {"b.txt": "M", "gbk.txt": "M", "new.txt": "U"})
        self.assertTrue(st.dirty)

    def test_fingerprint_stable(self):
        a = gitcore.get_snapshot(self.repo).fingerprint
        b = gitcore.get_snapshot(self.repo).fingerprint
        self.assertEqual(a, b)

    def test_not_a_repo(self):
        with self.assertRaises(gitcore.GitError):
            gitcore.toplevel(self.tmp)
        with self.assertRaises(gitcore.GitError):
            gitcore.get_snapshot(os.path.join(self.tmp, "nope"))

    # ---- log ----
    def test_log_and_refs(self):
        snap = gitcore.get_snapshot(self.repo)
        for scope in (gitcore.SCOPE_AUTO, gitcore.SCOPE_ALL):
            commits = gitcore.get_log(self.repo, snap, scope)
            self.assertEqual(len(commits), 5)
        commits = gitcore.get_log(self.repo, snap, gitcore.SCOPE_ALL)
        pos = {c.oid: i for i, c in enumerate(commits)}
        for c in commits:                      # 拓扑序：子提交一定在父提交之前
            for p in c.parents:
                self.assertLess(pos[c.oid], pos[p])
        top = commits[0]
        self.assertEqual(top.oid, self.rename)
        names = [(r.name, r.kind, r.is_head) for r in top.refs]
        self.assertIn(("main", "local", True), names)
        self.assertIn(("v1", "tag", False), names)
        self.assertEqual(commits[-1].subject, "init 初始提交")
        self.assertTrue(any(r.name == "feature" for c in commits for r in c.refs))

    def test_log_paging(self):
        snap = gitcore.get_snapshot(self.repo)
        p1 = gitcore.get_log(self.repo, snap, gitcore.SCOPE_ALL, 0, 2)
        p2 = gitcore.get_log(self.repo, snap, gitcore.SCOPE_ALL, 2, 10)
        full = gitcore.get_log(self.repo, snap, gitcore.SCOPE_ALL)
        self.assertEqual([c.oid for c in p1 + p2], [c.oid for c in full])

    # ---- 提交图 ----
    def test_graph_invariants(self):
        snap = gitcore.get_snapshot(self.repo)
        commits = gitcore.get_log(self.repo, snap, gitcore.SCOPE_ALL)
        lay = GraphLayout()
        rows = [lay.add(c.oid, c.parents) for c in commits]
        for prev, nxt in zip(rows, rows[1:]):   # 上一行的下半格终点列 == 下一行的上半格起点列
            self.assertEqual({b for _, b, _ in prev.bottom}, {a for a, _, _ in nxt.top})
        self.assertEqual(rows[-1].bottom, [])    # 根提交之后没有线了
        merge_row = rows[[c.oid for c in commits].index(self.merge)]
        self.assertEqual(len({b for _, b, _ in merge_row.bottom}), 2)  # 合并提交往下分成两条线

    def test_graph_paging_equals_whole(self):
        snap = gitcore.get_snapshot(self.repo)
        commits = gitcore.get_log(self.repo, snap, gitcore.SCOPE_ALL)
        whole = GraphLayout()
        a = [whole.add(c.oid, c.parents) for c in commits]
        paged = GraphLayout()
        b = [paged.add(c.oid, c.parents) for c in commits[:2]]
        b += [paged.add(c.oid, c.parents) for c in commits[2:]]
        self.assertEqual(a, b)

    # ---- 提交的文件与 diff ----
    def test_commit_files(self):
        files = gitcore.get_commit_files(self.repo, self.rename)
        self.assertEqual([(f.status, f.old_path, f.path) for f in files], [("R", "a.txt", "b.txt")])
        root = gitcore.get_commit_files(self.repo, self.root)
        self.assertEqual(sorted((f.status, f.path) for f in root), [("A", "a.txt"), ("A", "gbk.txt")])
        merge = gitcore.get_commit_files(self.repo, self.merge)       # 相对第一个父提交
        self.assertEqual([(f.status, f.path) for f in merge], [("M", "a.txt")])

    def test_commit_diff_root_gbk(self):
        fd = gitcore.get_commit_diff(self.repo, self.root, ["gbk.txt"])
        texts = [l.text for l in fd.lines if l.kind == "add"]
        self.assertEqual(texts, ["你好，世界", "第二行"])
        self.assertEqual((fd.added, fd.deleted), (2, 0))
        bad = gitcore.get_commit_diff(self.repo, self.root, ["gbk.txt"], encoding=gitcore.ENC_UTF8)
        self.assertNotEqual([l.text for l in bad.lines if l.kind == "add"], texts)

    def test_commit_diff_rename_and_merge(self):
        fd = gitcore.get_commit_diff(self.repo, self.rename, ["a.txt", "b.txt"])
        self.assertEqual((fd.added, fd.deleted), (1, 0))
        fd = gitcore.get_commit_diff(self.repo, self.merge, ["a.txt"])
        self.assertEqual((fd.added, fd.deleted), (1, 1))
        dels = [(l.old_no, l.text) for l in fd.lines if l.kind == "del"]
        adds = [(l.new_no, l.text) for l in fd.lines if l.kind == "add"]
        self.assertEqual(dels, [(2, "l2")])
        self.assertEqual(adds, [(2, "l2 changed")])

    def test_ignore_whitespace(self):
        r = os.path.join(self.tmp, "ws")
        os.makedirs(r)
        git(r, "init", "-q", "-b", "main")
        write(r, "f.txt", "a b\nc\n")
        commit(r, "one")
        write(r, "f.txt", "a  b\r\nc\r\n")
        commit(r, "two")
        oid = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=r, env=ENV).decode().strip()
        self.assertGreater(gitcore.get_commit_diff(r, oid, ["f.txt"]).added, 0)
        self.assertEqual(gitcore.get_commit_diff(r, oid, ["f.txt"], ignore_ws=True).added, 0)

    # ---- 工作区 diff ----
    def test_worktree_diff(self):
        snap = gitcore.get_snapshot(self.repo)
        by = {c.path: c for c in snap.status.changes}
        fd = gitcore.get_worktree_diff(self.repo, by["b.txt"], True)
        self.assertEqual([(l.new_no, l.text) for l in fd.lines if l.kind == "add"], [(5, "wip")])
        fd = gitcore.get_worktree_diff(self.repo, by["gbk.txt"], True)
        self.assertEqual([l.text for l in fd.lines if l.kind == "del"], ["第二行"])
        self.assertEqual([l.text for l in fd.lines if l.kind == "add"], ["改动后的第二行"])
        fd = gitcore.get_worktree_diff(self.repo, by["new.txt"], True)
        self.assertEqual([l.text for l in fd.lines if l.kind == "add"], ["新文件", "第二行"])

    # ---- 文件历史 ----
    def test_file_history_follows_rename(self):
        hist = gitcore.get_file_history(self.repo, "b.txt")
        self.assertGreaterEqual(len(hist), 3)
        self.assertEqual((hist[0].path, hist[0].old_path, hist[0].status), ("b.txt", "a.txt", "R"))
        self.assertEqual(hist[-1].path, "a.txt")
        self.assertEqual(hist[-1].commit.oid, self.root)
        fd = gitcore.get_commit_diff(self.repo, hist[0].commit.oid,
                                     [p for p in (hist[0].old_path, hist[0].path) if p])
        self.assertEqual(fd.added, 1)

    def test_file_history_of_deleted_file(self):
        r = os.path.join(self.tmp, "del")
        os.makedirs(r)
        git(r, "init", "-q", "-b", "main")
        write(r, "x.txt", "x\n")
        commit(r, "add x")
        git(r, "rm", "-q", "x.txt")
        commit(r, "remove x")
        hist = gitcore.get_file_history(r, "x.txt")
        self.assertEqual([h.status for h in hist], ["D", "A"])

    def test_list_files(self):
        self.assertEqual(sorted(gitcore.list_files(self.repo)), ["b.txt", "c.txt", "gbk.txt"])


    # ---- 分支列表 ----
    def test_branch_list_local_and_remote(self):
        a = os.path.join(self.tmp, "br_a")
        os.makedirs(a)
        git(a, "init", "-q", "-b", "main")
        write(a, "f.txt", "1\n")
        commit(a, "c1")
        git(a, "checkout", "-q", "-b", "feature")
        write(a, "f.txt", "2\n")
        commit(a, "feature work")
        git(a, "checkout", "-q", "main")
        bare = os.path.join(self.tmp, "br_bare.git")
        subprocess.run(["git", "clone", "-q", "--bare", a, bare], env=ENV, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git(a, "remote", "add", "origin", bare)
        git(a, "fetch", "-q", "origin")
        git(a, "remote", "set-head", "origin", "main")        # 产生 origin/HEAD：列表里不该出现
        git(a, "branch", "-q", "-u", "origin/main", "main")
        write(a, "g.txt", "x\n")
        commit(a, "main ahead")
        brs = gitcore.get_branches(a)
        self.assertEqual({b.name for b in brs}, {"main", "feature", "origin/main", "origin/feature"})
        self.assertEqual(brs[0].name, "main")                  # 当前分支排第一
        self.assertTrue(brs[0].is_current)
        main = brs[0]
        self.assertEqual((main.upstream, main.ahead, main.behind, main.kind), ("origin/main", 1, 0, "local"))
        self.assertEqual(main.subject, "main ahead")
        self.assertEqual([b.kind for b in brs], ["local", "local", "remote", "remote"])
        # “只看某个分支”：只包含该分支可达的提交
        snap = gitcore.get_snapshot(a)
        only = gitcore.get_log(a, snap, "ref:refs/remotes/origin/feature")
        self.assertEqual([c.subject for c in only], ["feature work", "c1"])
        allc = gitcore.get_log(a, snap, gitcore.SCOPE_ALL)
        self.assertEqual({c.subject for c in allc}, {"c1", "feature work", "main ahead"})

    def test_branch_list_gone_upstream(self):
        r = os.path.join(self.tmp, "br_gone")
        os.makedirs(r)
        git(r, "init", "-q", "-b", "main")
        write(r, "f.txt", "1\n")
        commit(r, "c1")
        self.assertEqual([(b.name, b.upstream, b.is_current) for b in gitcore.get_branches(r)],
                         [("main", "", True)])

    # ---- 空仓库 ----
    def test_empty_repo(self):
        r = os.path.join(self.tmp, "empty")
        os.makedirs(r)
        git(r, "init", "-q", "-b", "main")
        write(r, "n.txt", "hi\n")
        snap = gitcore.get_snapshot(r)
        self.assertEqual(snap.status.head_oid, "")
        self.assertEqual(gitcore.get_log(r, snap), [])
        fd = gitcore.get_worktree_diff(r, snap.status.changes[0], False)
        self.assertEqual(fd.added, 1)


class ParseTest(unittest.TestCase):
    def test_parse_diff_crlf_and_markers(self):
        data = (b"diff --git a/f b/f\nindex 1..2 100644\n--- a/f\n+++ b/f\n"
                b"@@ -1,2 +1,2 @@ sec\n a\r\n-b\r\n+c\r\n\\ No newline at end of file\n")
        fd = gitcore.parse_diff(data)
        self.assertEqual([(l.kind, l.old_no, l.new_no, l.text) for l in fd.lines],
                         [("hunk", None, None, "@@ -1,2 +1,2 @@ sec"),
                          ("ctx", 1, 1, "a"), ("del", 2, None, "b"), ("add", None, 2, "c")])

    def test_content_that_looks_like_header(self):
        # 被删除的行内容是 "-- x"，被新增的行内容是 "++ y"，不能被当成文件头
        data = b"@@ -1 +1 @@\n--- x\n+++ y\n"
        fd = gitcore.parse_diff(data)
        self.assertEqual([(l.kind, l.text) for l in fd.lines[1:]], [("del", "-- x"), ("add", "++ y")])

    def test_mixed_encoding_lines(self):
        data = "@@ -1 +1 @@\n-".encode() + "旧的".encode("gbk") + b"\n+" + "新的".encode() + b"\n"
        fd = gitcore.parse_diff(data)
        self.assertEqual([l.text for l in fd.lines[1:]], ["旧的", "新的"])

    def test_binary(self):
        fd = gitcore.parse_diff(b"diff --git a/x b/x\nBinary files a/x and b/x differ\n")
        self.assertTrue(fd.is_binary)

    def test_diff_truncation_protection(self):
        # 超过行数截断保护
        huge_data = b"@@ -1,1 +1,40000 @@\n" + b"+line\n" * 35000
        fd = gitcore.parse_diff(huge_data)
        self.assertTrue(any("已自动截断" in (l.text or "") for l in fd.lines))
        self.assertLessEqual(len(fd.lines), gitcore.MAX_DIFF_PARSE_LINES + 2)

    def test_graph_linear_and_branches(self):
        lay = GraphLayout()
        r1 = lay.add("c", ["b"])
        r2 = lay.add("b", ["a"])
        r3 = lay.add("a", [])
        self.assertEqual((r1.col, r2.col, r3.col), (0, 0, 0))
        self.assertEqual(r3.bottom, [])
        # 两个分支末端指向同一个父提交
        lay = GraphLayout()
        x = lay.add("x", ["p"])
        y = lay.add("y", ["p"])
        p = lay.add("p", [])
        self.assertEqual((x.col, y.col), (0, 1))
        self.assertEqual(sorted(a for a, _, _ in p.top), [0, 1])


if __name__ == "__main__":
    unittest.main()
