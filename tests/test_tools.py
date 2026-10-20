"""difftools / items / utils 的测试（纯 Python，不需要界面库）。"""
import unittest

from support import TempDir, commit, init_repo, write

from ggit import gitcore
from ggit.difftools import build_split, change_groups, trim_context
from ggit.gitcore import Commit, CommitFile, DiffLine
from ggit.graph import GraphRow
from ggit.items import ItemList, Row
from ggit.utils import LruCache


def L(kind, old, new, text=""):
    return DiffLine(kind, old, new, text or kind)


def numbered(n, changes):
    """造一个 n 行的文件：changes 是 {行号: 'del'/'add'/'mod'}；mod = 一行被替换成另一行。"""
    lines, o, nw = [], 1, 1
    for i in range(1, n + 1):
        c = changes.get(i)
        if c in ("del", "mod"):
            lines.append(L("del", o, None, f"old{i}"))
            o += 1
        if c in ("add", "mod"):
            lines.append(L("add", None, nw, f"new{i}"))
            nw += 1
        if c is None:
            lines.append(L("ctx", o, nw, f"line{i}"))
            o += 1
            nw += 1
    return lines


class DiffToolsTest(unittest.TestCase):
    def test_trim_context(self):
        out = trim_context(numbered(30, {15: "mod"}), 3)
        kinds = [ln.kind for ln in out]
        self.assertEqual(kinds[0], "hunk")
        self.assertEqual(kinds.count("ctx"), 6)          # 前后各 3 行
        self.assertEqual(out[0].text, "@@ -12,7 +12,7 @@")
        self.assertEqual(sum(k == "hunk" for k in kinds), 1)

    def test_trim_context_two_hunks_and_merge(self):
        far = trim_context(numbered(40, {5: "add", 35: "del"}), 3)
        self.assertEqual(sum(ln.kind == "hunk" for ln in far), 2)
        near = trim_context(numbered(40, {5: "add", 9: "del"}), 3)    # 间隔小于 2n：合并成一段
        self.assertEqual(sum(ln.kind == "hunk" for ln in near), 1)

    def test_trim_no_changes(self):
        self.assertEqual(trim_context(numbered(10, {}), 3), [])

    def test_trim_new_file(self):
        out = trim_context([L("add", None, i, f"x{i}") for i in range(1, 4)])
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

    def test_note_lines_survive_trim_and_split(self):
        # 截断说明 / 分段标题（note）不能在两种显示模式里丢掉
        lines = numbered(40, {5: "add"}) + [L("note", None, None, "截断了")]
        out = trim_context(lines, 3)
        self.assertEqual((out[-1].kind, out[-1].text), ("note", "截断了"))
        rows = build_split(lines)
        last = rows[-1].left
        assert last is not None
        self.assertEqual(last.text, "截断了")
        self.assertIs(rows[-1].left, rows[-1].right)

    def test_trim_note_between_sections(self):
        sec2 = [L("note", None, None, "b/other")] + numbered(20, {15: "del"})
        kinds = [ln.kind for ln in trim_context(numbered(20, {3: "add"}) + sec2, 3)]
        self.assertEqual(kinds.count("hunk"), 2)
        self.assertEqual(kinds.count("note"), 1)
        self.assertLess(kinds.index("hunk"), kinds.index("note"))
        self.assertLess(kinds.index("note"), len(kinds) - 1 - kinds[::-1].index("hunk"))

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


class LruCacheTest(unittest.TestCase):
    def test_evicts_least_recently_used(self):
        c: LruCache[str, int] = LruCache(2)
        c.put("a", 1)
        c.put("b", 2)
        self.assertEqual(c.get("a"), 1)          # a 刚用过，b 变成最旧的
        c.put("c", 3)
        self.assertNotIn("b", c)
        self.assertEqual((c.get("a"), c.get("c"), len(c)), (1, 3, 2))

    def test_keep_is_never_evicted(self):
        c: LruCache[str, int] = LruCache(3)
        c.put("keep", 0)
        for i in range(10):
            c.put(f"k{i}", i, keep={"keep"})
        self.assertEqual(len(c), 3)
        self.assertIn("keep", c)
        self.assertEqual(c.peek("k9"), 9)

    def test_peek_does_not_refresh(self):
        c: LruCache[str, int] = LruCache(2)
        c.put("a", 1)
        c.put("b", 2)
        self.assertEqual(c.peek("a"), 1)
        c.put("c", 3)
        self.assertNotIn("a", c)
        self.assertIsNone(c.get("a"))


class IntegrationTest(unittest.TestCase):
    """用真实仓库的完整上下文 diff 走一遍：裁剪 + 对照。"""

    def test_with_real_repo(self):
        tmp = TempDir()
        self.addCleanup(tmp.cleanup)
        repo = init_repo(tmp.sub("r"))
        write(repo, "f.txt", "".join(f"line{i}\n" for i in range(1, 41)))
        commit(repo, "one")
        write(repo, "f.txt", "".join(f"line{i}\n" if i != 20 else "CHANGED\nEXTRA\n" for i in range(1, 41)))
        oid = commit(repo, "two")
        fd = gitcore.get_commit_diff(repo, oid, ["f.txt"])
        self.assertEqual(len([ln for ln in fd.lines if ln.kind == "ctx"]), 39)     # 整个文件的上下文都在
        trimmed = trim_context(fd.lines, 3)
        self.assertEqual(len([ln for ln in trimmed if ln.kind == "ctx"]), 6)
        rows = build_split(fd.lines)
        self.assertEqual(len(rows), 41)                                         # 39 行不变 + 1 对替换 + 1 行新增
        self.assertIsNone(rows[20].left)                                         # 第 21 行：新增的 EXTRA，左边补空行
        left, right = rows[19].left, rows[19].right
        assert left is not None and right is not None
        self.assertEqual((left.text, right.text), ("line20", "CHANGED"))


if __name__ == "__main__":
    unittest.main()
