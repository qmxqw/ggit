"""命令面板（Ctrl+`）：输入 git 命令执行，所有写操作（Fetch / Pull / Push / 提交…）的输出也都显示在这里。

  * 输入框：开头的 git 可写可不写；↑ / ↓ 翻历史命令；Tab 补全子命令和分支名
  * 输出：每条命令一行 "$ git …" 开头，结束时一行“✔ 完成 / ✘ 失败”；进度行原地更新
  * 输出先攒起来，每 50 毫秒刷一次，push 大仓库时也不卡界面
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .options import FONT_DEFAULT
from .ui_common import section_header
from .ui_diff import code_font

MAX_HISTORY = 200
MAX_OUTPUT_BLOCKS = 20000       # 输出最多保留这么多行，更早的自动丢掉

# Tab 补全用的常见子命令
SUBCOMMANDS = sorted({
    "add", "am", "apply", "bisect", "blame", "branch", "checkout", "cherry-pick", "clean", "clone", "commit",
    "config", "describe", "diff", "fetch", "gc", "grep", "init", "log", "ls-files", "merge", "mv", "notes",
    "pull", "push", "rebase", "reflog", "remote", "reset", "restore", "revert", "rm", "shortlog", "show",
    "stash", "status", "submodule", "switch", "tag", "worktree",
})


def common_prefix(words: list[str]) -> str:
    if not words:
        return ""
    lo, hi = min(words), max(words)
    i = 0
    while i < len(lo) and lo[i] == hi[i]:
        i += 1
    return lo[:i]


class CommandLine(QLineEdit):
    """命令输入框：↑ / ↓ 翻历史，Tab 补全，Esc 清空。"""
    completionListed = Signal(list)     # 有多个候选时，把候选交给面板显示

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history: list[str] = []
        self._pos = 0                   # 正在看第几条历史（== len(history) 表示正在输入新命令）
        self._draft = ""
        self.branch_source: Callable[[], list[str]] = list     # Tab 补全用的分支名（项目页面设置）
        self.setPlaceholderText("输入 git 命令后回车，例如：status、log --oneline -10、checkout -b 新分支")

    def remember(self, text: str) -> None:
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
            del self.history[:-MAX_HISTORY]
        self._pos = len(self.history)
        self._draft = ""

    def set_history(self, items: list[str]) -> None:
        self.history = [str(x) for x in items if isinstance(x, str) and x][-MAX_HISTORY:]
        self._pos = len(self.history)

    def event(self, e: QEvent) -> bool:
        # Tab 默认是切换焦点：在这里先拦下来做补全
        if e.type() == QEvent.Type.KeyPress and isinstance(e, QKeyEvent) and e.key() == Qt.Key.Key_Tab:
            self.complete()
            return True
        return super().event(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Up:
            self._browse(-1)
        elif e.key() == Qt.Key.Key_Down:
            self._browse(1)
        elif e.key() == Qt.Key.Key_Escape and self.text():
            self.clear()
            self._pos = len(self.history)
        else:
            super().keyPressEvent(e)

    def _browse(self, step: int) -> None:
        if not self.history:
            return
        if self._pos == len(self.history):
            self._draft = self.text()
        self._pos = max(0, min(len(self.history), self._pos + step))
        self.setText(self._draft if self._pos == len(self.history) else self.history[self._pos])

    def complete(self) -> None:
        """补全光标前的那个词：第一个词补子命令，后面的补分支名。"""
        text = self.text()
        cur = self.cursorPosition()
        before = text[:cur]
        start = len(before) - len(before.split(" ")[-1])
        word = before[start:]
        words_before = before[:start].split()
        if words_before and words_before[0].lower() == "git":
            words_before = words_before[1:]
        pool = SUBCOMMANDS if not words_before else self.branch_source()
        cands = sorted({c for c in pool if c.startswith(word)})
        if not cands:
            return
        fill = cands[0] + " " if len(cands) == 1 else common_prefix(cands)
        if len(cands) > 1 and fill == word:
            self.completionListed.emit(cands)
            return
        self.setText(text[:start] + fill + text[cur:])
        self.setCursorPosition(start + len(fill))


class ConsolePanel(QWidget):
    commandEntered = Signal(str)        # 回车：输入的一行（还没拆分）
    cancelRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("consolePanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        hdr, self.title, hl = section_header("命令")
        self.state = QLabel()
        self.state.setObjectName("metaText")
        hl.insertWidget(1, self.state)
        self.cancel_btn = QToolButton()
        self.cancel_btn.setText("■ 取消")
        self.cancel_btn.setToolTip("结束正在执行的命令")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancelRequested)
        hl.addWidget(self.cancel_btn)
        clear_btn = QToolButton()
        clear_btn.setText("清空")
        hl.addWidget(clear_btn)
        close_btn = QToolButton()
        close_btn.setText("×")
        close_btn.setToolTip("收起命令面板 (Ctrl+`)")
        close_btn.clicked.connect(self.closeRequested)
        hl.addWidget(close_btn)
        lay.addWidget(hdr)

        self.output = QPlainTextEdit()
        self.output.setObjectName("consoleOutput")
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.output.setMaximumBlockCount(MAX_OUTPUT_BLOCKS)
        self.output.setFont(code_font(FONT_DEFAULT))
        lay.addWidget(self.output, 1)
        clear_btn.clicked.connect(self.output.clear)

        row = QWidget()
        row.setObjectName("consoleInputRow")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 3, 6, 3)
        rl.setSpacing(6)
        prompt = QLabel("git ▸")
        prompt.setObjectName("consolePrompt")
        rl.addWidget(prompt)
        self.input = CommandLine()
        self.input.setObjectName("consoleInput")
        self.input.setFont(code_font(FONT_DEFAULT))
        self.input.returnPressed.connect(self._on_enter)
        self.input.completionListed.connect(lambda c: self.note("可选：" + "   ".join(c[:60])))
        rl.addWidget(self.input, 1)
        lay.addWidget(row)

        self._pending: list[str] = []
        self._overwrite = False          # 最后一行是进度行：下一段输出替换它
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(50)
        self._flush_timer.timeout.connect(self.flush)
        self._fmt_plain = QTextCharFormat()
        self._fmt_plain.setForeground(QColor(theme.TEXT))

    # ---------------------------------------------------------------- 对外接口
    def apply_font_size(self, size: int) -> None:
        self.output.setFont(code_font(size))
        self.input.setFont(code_font(size))

    def focus_input(self) -> None:
        self.input.setFocus()
        self.input.selectAll()

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.cancel_btn.setVisible(busy)
        self.state.setText(text)

    def begin(self, command: str) -> None:
        """开始一条命令：先写一行 "$ git …"。"""
        self.flush()
        self._ensure_newline()
        if not self.output.document().isEmpty():
            self._write("\n", self._fmt_plain)
        self._write(f"$ git {command}\n", self._fmt(theme.REF_TEXT, bold=True))

    def append(self, piece: str) -> None:
        """命令的一段输出（以 \\n 或 \\r 结尾）。攒一下再显示。"""
        self._pending.append(piece)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def finish(self, ok: bool, text: str) -> None:
        self.flush()
        self._ensure_newline()
        self._write(("✔ " if ok else "✘ ") + text + "\n", self._fmt(theme.ADDED if ok else theme.ERROR, bold=True))

    def note(self, text: str, color: str = theme.TEXT_MUTED) -> None:
        """一行说明（比如“已取消”“命令被拒绝”）。"""
        self.flush()
        self._ensure_newline()
        self._write(text + "\n", self._fmt(color))

    def text(self) -> str:
        self.flush()
        return self.output.toPlainText()

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _fmt(color: str, *, bold: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(700)
        return f

    def _on_enter(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.remember(text)
        self.input.clear()
        self.commandEntered.emit(text)

    def _cursor_end(self) -> QTextCursor:
        c = self.output.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        return c

    def _write(self, text: str, fmt: QTextCharFormat) -> None:
        bar = self.output.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self._cursor_end().insertText(text, fmt)
        self._overwrite = False
        if at_bottom:
            bar.setValue(bar.maximum())

    def _ensure_newline(self) -> None:
        """上一条输出没有以换行结束（比如停在进度行上）：先换行。"""
        doc = self.output.document()
        if self._overwrite or (not doc.isEmpty() and doc.lastBlock().text()):
            self._overwrite = False
            self._cursor_end().insertText("\n", self._fmt_plain)

    def flush(self) -> None:
        if not self._pending:
            return
        pieces, self._pending = self._pending, []
        bar = self.output.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        c = self._cursor_end()
        c.beginEditBlock()
        for piece in pieces:
            body = piece.rstrip("\r\n")
            if self._overwrite:         # 替换上一条进度行
                c.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
                c.removeSelectedText()
            c.insertText(body, self._fmt_plain)
            self._overwrite = piece.endswith("\r")
            if not self._overwrite:
                c.insertText("\n", self._fmt_plain)
        c.endEditBlock()
        if at_bottom:
            bar.setValue(bar.maximum())
