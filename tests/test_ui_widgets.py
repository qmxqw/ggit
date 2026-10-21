"""零散的界面部件：搜索框、文档标签页的重读规则、diff 视图、状态行、提交图的悬停提示。"""
import re
import unittest

import support
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFontMetrics, QMouseEvent, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QSplitter, QToolTip, QWidget
from support import TempDir, write

from ggit import fileutil, gitcore, theme, ui_docs
from ggit.async_runner import AsyncRunner
from ggit.gitcore import Commit, Ref, RepoStatus, Snapshot
from ggit.graph import GraphRow
from ggit.items import Row
from ggit.options import ViewOptions
from ggit.ui_dialogs import PickerDialog
from ggit.ui_diff import MODE_SPLIT, DiffDocument
from ggit.ui_docs import DocArea
from ggit.ui_files import FileDocument
from ggit.ui_graph import TIP_GAP, TOOLTIP_MAX_W, CommitModel, CommitTip, GraphView, row_refs
from ggit.ui_status import ProjectStatusBar


class PickerDialogTest(support.QtTestCase):
    def setUp(self):
        self.items = [(f"file{i}.txt", i) for i in range(1000)]
        self.dlg = PickerDialog("t", self.items)
        self.addCleanup(self.dlg.deleteLater)

    def shown(self):
        return [self.dlg.list.item(i).text() for i in range(self.dlg.list.count())]

    def expected(self, text):
        tokens = text.lower().split()
        return [d for d, _ in self.items if all(t in d.lower() for t in tokens)]

    def test_too_many_matches_shows_hint(self):
        self.assertEqual(self.dlg.list.count(), PickerDialog.MAX_SHOWN)
        self.assertEqual(self.dlg.match_count, 1000)
        self.assertFalse(self.dlg.hint.isHidden())
        self.assertIn("1000", self.dlg.hint.text())

    def test_incremental_filtering_matches_full_scan(self):
        for text in ("file1", "file12", "file12 ", "file12 9", "FILE", "file1 9", "zzz", ""):
            self.dlg.edit.setText(text)
            self.assertEqual(self.dlg.match_count, len(self.expected(text)), text)
            self.assertEqual(self.shown(), self.expected(text)[:PickerDialog.MAX_SHOWN], text)

    def test_no_match_hint(self):
        self.dlg.edit.setText("zzz")
        self.assertEqual(self.dlg.list.count(), 0)
        self.assertFalse(self.dlg.hint.isHidden())
        self.assertIn("没有匹配", self.dlg.hint.text())

    def test_enter_accepts(self):
        self.dlg.edit.setText("file999")
        QTest.keyClick(self.dlg.edit, Qt.Key.Key_Return)
        self.assertEqual(self.dlg.value, 999)
        self.assertEqual(self.dlg.result(), QDialog.DialogCode.Accepted)


class DocAreaTest(support.QtTestCase):
    def setUp(self):
        self.runner = AsyncRunner()
        self.addCleanup(self.runner.shutdown)
        self.area = DocArea(self.runner, ViewOptions(), FileDocument, "empty")
        self.addCleanup(self.area.deleteLater)
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        write(self.tmp.path, "x.txt", "hello\n")
        self.calls = []

    def read(self, _opts):
        self.calls.append(1)
        return fileutil.read_text_file(self.tmp.sub("x.txt"))

    def open(self, kind):
        return self.area.open_doc("k", "x", "x", kind, self.read, "")

    def test_reopen_right_after_load_does_not_reload(self):
        doc = self.open("file")
        support.wait_until(lambda: doc.sig is not None)
        self.open("file")                       # 双击会连着打开两次：刚读完，不重读
        self.app.processEvents()
        self.assertEqual(len(self.calls), 1)

    def test_reopen_later_reloads_disk_files_only(self):
        old = ui_docs.RELOAD_GAP
        ui_docs.RELOAD_GAP = 0
        self.addCleanup(setattr, ui_docs, "RELOAD_GAP", old)
        doc = self.open("file")
        support.wait_until(lambda: doc.sig is not None)
        self.open("file")                       # 磁盘上的文件可能变了：重读
        support.wait_until(lambda: len(self.calls) == 2)
        self.area.close_all()
        doc = self.open("commit")
        support.wait_until(lambda: doc.sig is not None)
        self.open("commit")                     # 提交里的文件永远不变：不重读
        self.app.processEvents()
        self.assertEqual(len(self.calls), 3)


class DiffDocumentTest(support.QtTestCase):
    def test_truncated_diff_shows_note_in_both_modes(self):
        old = gitcore.MAX_DIFF_PARSE_LINES
        gitcore.MAX_DIFF_PARSE_LINES = 5
        try:
            fd = gitcore.parse_diff(b"@@ -1,1 +1,9 @@\n" + b"+x\n" * 9)
        finally:
            gitcore.MAX_DIFF_PARSE_LINES = old
        doc = DiffDocument()
        self.addCleanup(doc.deleteLater)
        doc.show()
        doc.set_diff(fd)
        self.assertEqual(doc.unified.document().lastBlock().text(), gitcore.TRUNCATED_NOTE)
        self.assertIn("已截断", doc.stats.text())
        doc.set_mode(MODE_SPLIT)
        self.assertEqual(doc.split.right.document().lastBlock().text(), gitcore.TRUNCATED_NOTE)
        self.assertEqual(doc.split.left.document().lastBlock().text(), gitcore.TRUNCATED_NOTE)


class StatusBarTest(support.QtTestCase):
    def test_snapshot_and_error(self):
        bar = ProjectStatusBar()
        self.addCleanup(bar.deleteLater)
        st = RepoStatus(head_oid="a" * 40, branch="main", upstream="origin/main", ahead=2, behind=1)
        refs = {"refs/heads/main": "a" * 40, "refs/remotes/origin/main": "b" * 40,
                "refs/remotes/origin/HEAD": "b" * 40}
        bar.show_snapshot(Snapshot(st, refs, "fp"), "repo", "/path/repo")
        self.assertEqual(bar.branch_btn.text(), "⎇ main ▾")
        self.assertIn("↑ 2", bar.sync_badge.text())
        self.assertIn("↓ 1", bar.sync_badge.text())
        self.assertIn("→ origin/main", bar.meta.text())
        self.assertIn("本地 1 · 远端 1", bar.meta.text())
        bar.show_error("boom <b>")
        self.assertIn("读取失败", bar.sync_badge.text())
        self.assertIn("boom &lt;b&gt;", bar.meta.text())


class CommitTooltipTest(support.QtTestCase):
    def tooltip(self, commit):
        model = CommitModel()
        self.addCleanup(model.deleteLater)
        model.reset([Row(commit, GraphRow(col=0, color=0))], head_oid="", more=False)
        return model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)

    @staticmethod
    def width_of(tip):
        """提示框按内容定的宽度（外面那个表格的 width）。"""
        m = re.search(r"<table width=(\d+)", tip)
        assert m is not None, tip
        return int(m.group(1))

    @staticmethod
    def doc(tip):
        doc = QTextDocument()
        doc.setDefaultFont(QToolTip.font())
        doc.setHtml(tip)
        doc.size()                              # 排一次版，才能查每段折成了几行
        return doc

    def color_of(self, tip, text):
        doc = self.doc(tip)                     # 文档要留着：光标不会让它活下来
        return doc.find(text).charFormat().foreground().color().name()

    def test_single_line_message(self):
        tip = self.tooltip(Commit("1" * 40, [], "alice", 1000, [], "只有标题"))
        self.assertIn("只有标题", tip)
        self.assertEqual(self.color_of(tip, "alice"), theme.TIP_LINK)       # 作者、提交 id 用链接色
        self.assertEqual(self.color_of(tip, "1111111"), theme.TIP_LINK)

    def test_title_not_bold(self):
        """标题和正文同一种字体、常规字重，只是颜色亮一点。"""
        doc = self.doc(self.tooltip(Commit("1" * 40, [], "alice", 1000, [], "只有标题")))
        fmt = doc.find("只有标题").charFormat()
        self.assertEqual(fmt.fontWeight(), 400)
        self.assertEqual(fmt.foreground().color().name(), theme.TIP_TITLE)

    def test_body_list_and_trailers(self):
        """“- ”开头的行排成列表，续行和它在同一格（悬挂缩进）；末尾的尾注变暗、邮箱用链接色；`代码` 换颜色。"""
        body = "说明 `code`\n\n- 第一项\n  续行\n- 第二项\n\nCo-Authored-By: Bob <bob@x.org>"
        doc = self.doc(self.tooltip(Commit("1" * 40, [], "alice", 1000, [], "标题", body)))
        first, cont = doc.find("第一项"), doc.find("续行")
        table = first.currentTable()
        self.assertEqual(table.cellAt(first).column(), 1)                  # 第 0 列是项目符号
        self.assertEqual(table.cellAt(cont), table.cellAt(first))
        self.assertEqual(table.cellAt(doc.find("第二项")).row(), 1)
        self.assertEqual(doc.find("Co-Authored-By").charFormat().foreground().color().name(), theme.TIP_TEXT_DIM)
        self.assertEqual(doc.find("bob@x.org").charFormat().foreground().color().name(), theme.TIP_LINK)
        self.assertEqual(doc.find("code").charFormat().foreground().color().name(), theme.TIP_CODE)
        self.assertTrue(doc.find("`").isNull())                            # 反引号去掉了

    def test_synced_remote_collapses_to_icon(self):
        """提交行上：远端分支带云朵图标；和同名本地分支在同一个提交上时只画一朵云。"""
        refs = [Ref("main", "local", True), Ref("origin/main", "remote"), Ref("origin/dev", "remote")]
        self.assertEqual([(label, cloud) for label, _, _, cloud in row_refs(refs)],
                         [("◎ main", False), ("", True), ("origin/dev", True)])

    def test_width_follows_longest_line(self):
        line = "a much longer line in the commit message"
        tip = self.tooltip(Commit("1" * 40, [], "alice", 1000, [], "short", f"one\n{line}\ntwo"))
        self.assertGreaterEqual(self.width_of(tip), QFontMetrics(QToolTip.font()).horizontalAdvance(line))
        self.assertLess(self.width_of(tip), TOOLTIP_MAX_W)

    def test_width_capped(self):
        tip = self.tooltip(Commit("1" * 40, [], "alice", 1000, [], "标题", "word " * 300))
        self.assertEqual(self.width_of(tip), TOOLTIP_MAX_W)              # 再长的行在这个宽度内折行

    def test_refs_line_fits(self):
        """标题很短、引用很长：宽度要放得下引用标签那一行，不能把它挤成好几行。"""
        refs = [Ref("main", "local"), Ref("origin/feature/some-long-name", "remote"), Ref("v1.2.3", "tag")]
        doc = self.doc(self.tooltip(Commit("1" * 40, [], "alice", 1000, refs, "fix")))
        for name in ("main", "origin/feature/some-long-name", "v1.2.3"):
            self.assertFalse(doc.find(name).isNull(), name)
        self.assertEqual(doc.find("v1.2.3").block().layout().lineCount(), 1)

    def test_tip_label_not_squeezed(self):
        """真的弹出提示框：Qt 自己给富文本定的宽度会把长行压窄；按内容定宽后放得下最长的一行。"""
        fm = QFontMetrics(QToolTip.font())
        words = ["word"]
        while fm.horizontalAdvance(" ".join([*words, "word"])) < TOOLTIP_MAX_W - 40:
            words.append("word")
        line = " ".join(words)
        label = CommitTip()
        self.addCleanup(label.deleteLater)
        label.show_at(self.tooltip(Commit("1" * 40, [], "alice", 1000, [], "标题", line)), 0, 0)
        self.assertGreaterEqual(label.contentsRect().width(), fm.horizontalAdvance(line))

    def test_body_shows_first_30_lines(self):
        body = "\n".join(f"line{i} <x>" for i in range(1, 41))
        tip = self.tooltip(Commit("1" * 40, [], "alice", 1000, [], "标题", body))
        self.assertIn("line30 &lt;x&gt;", tip)                    # 正文按原样显示（转义过）
        self.assertNotIn("line31", tip)
        self.assertIn("还有 10 行", tip)
        self.assertLess(tip.index("标题"), tip.index("line1 "))    # 标题在正文前面


class GraphTooltipTest(support.QtTestCase):
    def test_tooltip_follows_rows(self):
        """提示框跟着鼠标所在的行换内容；贴在右边那一栏（正文区）的左侧、和行顶对齐，不挡提交图；按下鼠标就收起。"""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        view = GraphView()
        model = CommitModel()
        view.setModel(model)
        content = QWidget()
        splitter.addWidget(view)
        splitter.addWidget(content)
        self.addCleanup(splitter.deleteLater)
        gr = GraphRow(col=0, color=0)
        c0 = Commit("1" * 40, [], "alice", 1000, [], "commit 1")
        c1 = Commit("2" * 40, [], "bob", 2000, [], "commit 2")
        model.reset([Row(c0, gr), Row(c1, gr)], head_oid=c0.oid, more=False)
        splitter.resize(600, 300)
        splitter.setSizes([250, 350])
        splitter.show()
        self.app.processEvents()

        def mouse(kind, y, button=Qt.MouseButton.NoButton):
            p = QPointF(50, y)
            QApplication.sendEvent(view.viewport(), QMouseEvent(kind, p, view.viewport().mapToGlobal(p), button,
                                                                button, Qt.KeyboardModifier.NoModifier))

        row_h = view.sizeHintForRow(0)
        mouse(QEvent.Type.MouseMove, row_h // 2)
        self.assertTrue(view.tip.isVisible())
        self.assertIn("commit 1", view.tip.text())
        mouse(QEvent.Type.MouseMove, row_h + row_h // 2)
        self.assertIn("commit 2", view.tip.text())
        self.assertEqual(view.tip.x(), content.mapToGlobal(QPoint(0, 0)).x() + TIP_GAP)
        self.assertEqual(view.tip.y(), view.viewport().mapToGlobal(QPoint(0, row_h)).y())
        mouse(QEvent.Type.MouseButtonPress, row_h + row_h // 2, Qt.MouseButton.LeftButton)   # 按下：提示消失
        self.assertFalse(view.tip.isVisible())


if __name__ == "__main__":
    unittest.main()
