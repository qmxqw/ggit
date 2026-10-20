"""零散的界面部件：搜索框、文档标签页的重读规则、diff 视图、状态行、提交图的悬停提示。"""
import unittest
from unittest import mock

import support
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from support import TempDir, write

from ggit import fileutil, gitcore, ui_docs
from ggit.async_runner import AsyncRunner
from ggit.gitcore import Commit, RepoStatus, Snapshot
from ggit.graph import GraphRow
from ggit.items import Row
from ggit.options import ViewOptions
from ggit.ui_dialogs import PickerDialog
from ggit.ui_diff import MODE_SPLIT, DiffDocument
from ggit.ui_docs import DocArea
from ggit.ui_files import FileDocument
from ggit.ui_graph import CommitModel, GraphView
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


class GraphTooltipTest(support.QtTestCase):
    def test_tooltip_follows_rows(self):
        view = GraphView()
        model = CommitModel()
        view.setModel(model)
        self.addCleanup(view.deleteLater)
        gr = GraphRow(col=0, color=0)
        c0 = Commit("1" * 40, [], "alice", 1000, [], "commit 1")
        c1 = Commit("2" * 40, [], "bob", 2000, [], "commit 2")
        model.reset([Row(c0, gr), Row(c1, gr)], head_oid=c0.oid, more=False)
        view.resize(400, 300)
        view.show()
        self.app.processEvents()

        def mouse(kind, y, button=Qt.MouseButton.NoButton):
            p = QPointF(50, y)
            QApplication.sendEvent(view.viewport(), QMouseEvent(kind, p, view.viewport().mapToGlobal(p), button,
                                                                button, Qt.KeyboardModifier.NoModifier))

        row_h = view.sizeHintForRow(0)
        with mock.patch("ggit.ui_graph.QToolTip") as tip:       # offscreen 平台上提示框不会真的隐藏
            mouse(QEvent.Type.MouseMove, row_h // 2)
            self.assertIn("commit 1", tip.showText.call_args.args[1])
            mouse(QEvent.Type.MouseMove, row_h + row_h // 2)
            self.assertIn("commit 2", tip.showText.call_args.args[1])
            tip.hideText.reset_mock()
            mouse(QEvent.Type.MouseButtonPress, row_h + row_h // 2, Qt.MouseButton.LeftButton)   # 按下：提示消失
            tip.hideText.assert_called()


if __name__ == "__main__":
    unittest.main()
