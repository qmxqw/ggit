"""difftools / items 的测试（纯 Python，不需要 git 和界面库）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ggit.difftools import build_split, change_groups, trim_context  # noqa: E402
from ggit.gitcore import Commit, CommitFile, DiffLine  # noqa: E402
from ggit.graph import GraphRow  # noqa: E402
from ggit.items import FileRow, ItemList, Row  # noqa: E402


def L(kind, old, new, text=""):
    return DiffLine(kind, old, new, text or kind)


def numbered(n, changes):
    """造一个 n 行的文件：changes 是 {行号: 'del'/'add'/'mod'}；mod = 一行被替换成另一行。"""
    lines, o, nw = [], 1, 1
    for i in range(1, n + 1):
        c = changes.get(i)
        if c == "del":
            lines.append(L("del", o, None, f"old{i}")); o += 1
        elif c == "add":
            lines.append(L("add", None, nw, f"new{i}")); nw += 1
        elif c == "mod":
            lines.append(L("del", o, None, f"old{i}")); o += 1
            lines.append(L("add", None, nw, f"new{i}")); nw += 1
        else:
            lines.append(L("ctx", o, nw, f"line{i}")); o += 1; nw += 1
    return lines


class DiffToolsTest(unittest.TestCase):
    def test_trim_context(self):
        lines = numbered(30, {15: "mod"})
        out = trim_context(lines, 3)
        kinds = [l.kind for l in out]
        self.assertEqual(kinds[0], "hunk")
        self.assertEqual(kinds.count("ctx"), 6)          # 前后各 3 行
        self.assertEqual(out[0].text, "@@ -12,7 +12,7 @@")
        self.assertEqual(sum(k == "hunk" for k in kinds), 1)

    def test_trim_context_two_hunks_and_merge(self):
        far = trim_context(numbered(40, {5: "add", 35: "del"}), 3)
        self.assertEqual(sum(l.kind == "hunk" for l in far), 2)
        near = trim_context(numbered(40, {5: "add", 9: "del"}), 3)    # 间隔小于 2n：合并成一段
        self.assertEqual(sum(l.kind == "hunk" for l in near), 1)

    def test_trim_no_changes(self):
        self.assertEqual(trim_context(numbered(10, {}), 3), [])

    def test_trim_new_file(self):
        lines = [L("add", None, i, f"x{i}") for i in range(1, 4)]
        out = trim_context(lines)
        self.assertEqual(out[0].text, "@@ -0,0 +1,3 @@")

    def test_build_split_alignment(self):
        lines = [L("ctx", 1, 1, "a"), L("del", 2, None, "b"), L("del", 3, None, "c"),
                 L("add", None, 2, "B"), L("ctx", 4, 3, "d"), L("add", None, 4, "e")]
        rows = build_split(lines)
        self.assertEqual(len(rows), 5)
        self.assertEqual([(r.left.text if r.left else None, r.right.text if r.right else None) for r in rows],
                         [("a", "a"), ("b", "B"), ("c", None), ("d", "d"), (None, "e")])
        # 每个 ctx 行两侧是同一行；两侧行数一致就是“进度对齐”
        self.assertIs(rows[0].left, rows[0].right)

    def test_build_split_ignores_hunk_lines(self):
        rows = build_split([L("hunk", None, None, "@@"), L("ctx", 1, 1, "a")])
        self.assertEqual(len(rows), 1)

    def test_change_groups(self):
        flags = [(False, False), (True, False), (False, True), (False, False), (False, True), (False, False)]
        self.assertEqual(change_groups(flags), [(1, 2, True, True), (4, 4, False, True)])
        self.assertEqual(change_groups([(True, False)]), [(0, 0, True, False)])
        self.assertEqual(change_groups([]), [])


def make_row(oid, parents=()):
    c = Commit(oid, list(parents), "a", 0, [], oid)
    return Row(c, GraphRow(col=0, color=0, width=1))


def cf(path):
    return CommitFile("M", path)


class ItemListTest(unittest.TestCase):
    def setUp(self):
        self.rows = [make_row("c3", ["c2"]), make_row("c2", ["c1"]), make_row("c1")]
        self.lst = ItemList()
        self.lst.reset(self.rows)

    def test_expand_collapse(self):
        self.assertEqual(self.lst.expand("c2", [cf("a"), cf("b")]), (2, 3))
        kinds = [type(i).__name__ for i in self.lst.items]
        self.assertEqual(kinds, ["Row", "Row", "FileRow", "FileRow", "Row"])
        self.assertTrue(self.rows[1].expanded)
        self.assertEqual(self.lst.file_pos("c2", "b"), 3)
        self.assertEqual(self.lst.file_pos("c3", "b"), -1)
        self.assertEqual(self.lst.pos_of("c1"), 4)
        self.assertEqual(self.lst.expand("c2", [cf("a")]), (0, -1))     # 已展开：不重复插入
        self.assertEqual(self.lst.collapse("c2"), (2, 3))
        self.assertEqual(len(self.lst), 3)
        self.assertFalse(self.rows[1].expanded)
        self.assertEqual(self.lst.collapse("c2"), (0, -1))

    def test_expand_two_commits_independent(self):
        self.lst.expand("c3", [cf("x")])
        self.lst.expand("c1", [cf("y"), cf("z")])
        self.lst.collapse("c3")
        self.assertEqual([type(i).__name__ for i in self.lst.items], ["Row", "Row", "Row", "FileRow", "FileRow"])
        self.assertIs(self.lst.items[3].parent, self.rows[2])

    def test_expand_empty_commit(self):
        self.assertEqual(self.lst.expand("c1", []), (0, -1))
        self.assertTrue(self.rows[2].expanded)

    def test_reset_with_expanded_files(self):
        lst = ItemList()
        lst.reset([make_row("a", ["b"]), make_row("b")], {"b": [cf("f")]})
        self.assertEqual([type(i).__name__ for i in lst.items], ["Row", "Row", "FileRow"])
        self.assertTrue(lst.items[1].expanded)
        self.assertEqual(lst.extend([make_row("c")]), (3, 3))

    def test_unknown_oid(self):
        self.assertEqual(self.lst.expand("zzz", [cf("a")]), (0, -1))
        self.assertEqual(self.lst.collapse("zzz"), (0, -1))


class IntegrationTest(unittest.TestCase):
    """用真实仓库的完整上下文 diff 走一遍：裁剪 + 对照。"""

    def test_with_real_repo(self):
        import shutil, subprocess, tempfile
        from ggit import gitcore
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@e", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        tmp = tempfile.mkdtemp()
        try:
            def g(*a):
                subprocess.run(["git", *a], cwd=tmp, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            g("init", "-q", "-b", "main")
            with open(os.path.join(tmp, "f.txt"), "w") as f:
                f.write("".join(f"line{i}\n" for i in range(1, 41)))
            g("add", "-A"); g("commit", "-q", "-m", "one")
            with open(os.path.join(tmp, "f.txt"), "w") as f:
                f.write("".join(f"line{i}\n" if i != 20 else "CHANGED\nEXTRA\n" for i in range(1, 41)))
            g("add", "-A"); g("commit", "-q", "-m", "two")
            oid = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp, env=env).decode().strip()
            fd = gitcore.get_commit_diff(tmp, oid, ["f.txt"])
            self.assertEqual(len([l for l in fd.lines if l.kind == "ctx"]), 39)     # 整个文件的上下文都在
            trimmed = trim_context(fd.lines, 3)
            self.assertEqual(len([l for l in trimmed if l.kind == "ctx"]), 6)
            rows = build_split(fd.lines)
            self.assertEqual(len(rows), 41)                                         # 39 行不变 + 1 对替换 + 1 行新增
            self.assertIsNone(rows[20].left)                                         # 第 21 行：新增的 EXTRA，左边补空行
            self.assertEqual(rows[19].left.text, "line20")
            self.assertEqual(rows[19].right.text, "CHANGED")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
