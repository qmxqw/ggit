"""一个项目的 Git 写操作：Fetch / Pull / Push、分支、暂存与提交、储藏、命令面板里输入的命令。

  * 同一个项目同一时间只执行一个写操作（不同项目之间互不影响）
  * 所有输出都写到这个项目的命令面板里；失败时自动展开面板
  * 会丢数据的操作先确认；执行完自动刷新项目状态
  * 对话框（确认、输入名字、多选一）都通过可替换的函数弹出，测试时换成直接返回答案的函数
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox, QWidget

from . import gitops, theme
from .async_runner import AsyncRunner
from .gitcore import BranchInfo, Commit, FileChange, Snapshot
from .gitops import OpResult, format_command
from .ui_console import ConsolePanel
from .ui_dialogs import PickerDialog

Confirm = Callable[[QWidget, str, str], bool]
AskText = Callable[[QWidget, str, str, str], "str | None"]
Choose = Callable[[QWidget, str, str, list], "int | None"]
Pick = Callable[[QWidget, str, list], Any]

MAX_LISTED_FILES = 12       # 确认对话框里最多列出这么多个文件


# ---------------------------------------------------------------- 默认的对话框
def confirm_box(parent: QWidget, title: str, text: str) -> bool:
    box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                      QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, parent)
    box.button(QMessageBox.StandardButton.Ok).setText("确定")
    box.button(QMessageBox.StandardButton.Cancel).setText("取消")
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Ok


def ask_text_box(parent: QWidget, title: str, label: str, default: str) -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, default)
    return text.strip() if ok else None


def choose_box(parent: QWidget, title: str, text: str, options: list) -> int | None:
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    buttons = [box.addButton(o, QMessageBox.ButtonRole.AcceptRole) for o in options]
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    return buttons.index(clicked) if clicked in buttons else None


def pick_box(parent: QWidget, title: str, items: list) -> Any:
    dlg = PickerDialog(title, items, parent)
    return dlg.value if dlg.exec() else None


def _file_list(paths: list[str]) -> str:
    shown = "\n".join(f"  {p}" for p in paths[:MAX_LISTED_FILES])
    more = f"\n  … 另外还有 {len(paths) - MAX_LISTED_FILES} 个" if len(paths) > MAX_LISTED_FILES else ""
    return shown + more


def _with(label: str, word: str) -> str:
    """“Fetch 完成”“暂存完成”：英文结尾的操作名后面加个空格。"""
    return f"{label} {word}" if label[-1:].isascii() else label + word


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


class GitOps(QObject):
    busyChanged = Signal(bool)
    opFinished = Signal(str, bool, str)     # 操作名称, 是否成功, 一句话说明
    consoleRequested = Signal(bool)         # 要求展开命令面板：是否把焦点放到输入框
    commitDone = Signal()                   # 提交成功（清空提交说明）

    def __init__(self, runner: AsyncRunner, repo: str, console: ConsolePanel, parent_widget: QWidget,
                 snapshot: Callable[[], Snapshot | None], refresh: Callable[[], None],
                 pull_mode: Callable[[], str], branches: Callable[[], list]) -> None:
        super().__init__(parent_widget)
        self.runner = runner
        self.repo = repo
        self.console = console
        self.widget = parent_widget
        self._snapshot = snapshot
        self._refresh = refresh
        self._pull_mode = pull_mode
        self._branches = branches
        self._task: int | None = None
        self._label = ""
        self._alive = True
        # 对话框（测试时替换）
        self.confirm: Confirm = confirm_box
        self.ask_text: AskText = ask_text_box
        self.choose: Choose = choose_box
        self.pick: Pick = pick_box
        console.commandEntered.connect(self.run_command_text)
        console.cancelRequested.connect(self.cancel)

    # ================================================================ 执行
    @property
    def busy(self) -> bool:
        return self._task is not None

    def run(self, args: list[str], label: str, *, sequence: list[list[str]] | None = None,
            accept_default_message: bool = False, refresh: bool = True,
            on_done: Callable[[OpResult], None] | None = None) -> bool:
        """在后台执行一条（或依次执行几条）git 命令。正在执行别的操作时不执行，返回 False。"""
        if not self._alive:
            return False
        if self.busy:
            self.console.note(f"上一个操作（{self._label}）还在执行，请等它结束或取消。", theme.MODIFIED)
            self.consoleRequested.emit(False)
            return False
        cmds = sequence or [args]
        repo = self.repo
        self._label = label
        self.console.begin(format_command(cmds[0]))

        def work(emit: Callable[[str], None]) -> OpResult:
            if len(cmds) > 1:
                return gitops.run_sequence(repo, cmds, emit)
            return gitops.run_git_stream(repo, cmds[0], emit, accept_default_message=accept_default_message)

        self._task = self.runner.submit_stream(work, self.console.append,
                                               lambda r: self._on_done(r, label, refresh, on_done),
                                               lambda e: self._on_error(e, label))
        self.console.set_busy(True, f"正在执行：{label}…")
        self.busyChanged.emit(True)
        return True

    def _end(self) -> None:
        self._task = None
        self.console.set_busy(False)
        self.busyChanged.emit(False)

    def _on_done(self, res: OpResult, label: str, refresh: bool,
                 on_done: Callable[[OpResult], None] | None) -> None:
        self._end()
        if res.ok:
            self.console.finish(True, _with(label, f"完成（{res.duration:.1f} 秒）"))
            detail = _last_line(res.output) or "完成"
        else:
            self.console.finish(False, _with(label, f"失败（返回码 {res.returncode}，{res.duration:.1f} 秒）"))
            detail = _last_line(res.output) or f"返回码 {res.returncode}"
            self.consoleRequested.emit(False)
        if refresh:
            self._refresh()
        self.opFinished.emit(label, res.ok, detail)
        if on_done:
            on_done(res)

    def _on_error(self, err: BaseException, label: str) -> None:
        self._end()
        self.console.finish(False, _with(label, f"失败：{err}"))
        self.consoleRequested.emit(False)
        self.opFinished.emit(label, False, str(err))

    def cancel(self) -> None:
        if self._task is None:
            return
        self.runner.cancel(self._task)
        label = self._label
        self._end()
        self.console.finish(False, _with(label, "已取消"))
        self._refresh()
        self.opFinished.emit(label, False, "已取消")

    def dispose(self) -> None:
        self._alive = False
        if self._task is not None:
            self.runner.cancel(self._task)
            self._task = None

    def _fail(self, label: str, message: str) -> None:
        """还没开始执行就发现做不了：在命令面板里说明。"""
        self.console.note(f"✘ {label}：{message}", theme.ERROR)
        self.consoleRequested.emit(False)
        self.opFinished.emit(label, False, message)

    def _status(self):
        snap = self._snapshot()
        return snap.status if snap is not None else None

    # ================================================================ 命令面板
    def run_command_text(self, text: str) -> None:
        try:
            args = gitops.split_command(text)
        except gitops.CommandError as e:
            self.console.note(f"✘ {e}", theme.ERROR)
            return
        chk = gitops.check_command(args)
        if chk.blocked:
            self.console.note(f"$ git {format_command(args)}\n✘ 没有执行：{chk.blocked}", theme.ERROR)
            return
        if chk.danger and not self.confirm(self.widget, "确认执行",
                                           f"git {format_command(args)}\n\n{chk.danger}。确定要执行吗？"):
            self.console.note(f"$ git {format_command(args)}\n（已取消执行）")
            return
        self.run(args, f"git {gitops.subcommand(args)[0] or args[0]}", refresh=chk.writes)

    # ================================================================ 同步
    def fetch(self) -> bool:
        return self.run(gitops.fetch_args(), "Fetch")

    def pull(self, mode: str | None = None) -> bool:
        st = self._status()
        if st is not None and not st.branch:
            self._fail("Pull", "当前不在任何分支上（HEAD 分离），无法 pull")
            return False
        if st is not None and not st.upstream:
            self._fail("Pull", "当前分支没有上游分支；请先 Push（会自动设置上游），或在命令面板里指定远端分支")
            return False
        mode = mode or self._pull_mode()
        return self.run(gitops.pull_args(mode), "Pull")

    def push(self, force: bool = False) -> None:
        st = self._status()
        label = "强制推送" if force else "Push"
        if st is None:
            self._fail(label, "还没有读到项目状态，请稍后再试")
            return
        if not st.branch:
            self._fail(label, "当前不在任何分支上（HEAD 分离），无法推送")
            return
        if force and not self.confirm(self.widget, "强制推送",
                                      f"用 --force-with-lease 强制推送分支“{st.branch}”？\n\n"
                                      "远端上别人新推送的提交不会被覆盖（会推送失败），"
                                      "但远端上你自己已经推送过、本地又改写掉的提交会被替换。"):
            return
        if st.upstream:
            self.run(gitops.push_args(st, force=force), label)
            return
        # 没有上游：先找出推送到哪个远端，确认后推送并设为上游
        repo = self.repo
        self.runner.submit(lambda: gitops.push_remote(repo),
                           lambda remote: self._push_new_branch(st, remote, force, label),
                           lambda e: self._fail(label, str(e)))

    def _push_new_branch(self, st, remote: str, force: bool, label: str) -> None:
        if not self._alive:
            return
        if not self.confirm(self.widget, "推送新分支",
                            f"分支“{st.branch}”还没有上游分支。\n\n推送到 {remote}/{st.branch}，并设为它的上游？"):
            return
        self.run(gitops.push_args(st, remote, force=force), label)

    # ================================================================ 进行到一半的合并 / 变基
    def abort_operation(self) -> None:
        st = self._status()
        op = st.operation if st else ""
        if not op:
            self._fail("中止", "当前没有进行中的合并 / 变基")
            return
        name = gitops.OPERATION_NAMES[op]
        if self.confirm(self.widget, f"中止{name}", f"中止当前的{name}，回到开始之前的状态？\n\n"
                                                    "为解决冲突做的修改会被丢掉。"):
            self.run(gitops.ABORT_ARGS[op], f"中止{name}")

    def continue_operation(self) -> None:
        st = self._status()
        op = st.operation if st else ""
        if not op:
            self._fail("继续", "当前没有进行中的合并 / 变基")
            return
        if st is not None and st.conflicts:
            self._fail("继续", f"还有 {st.conflicts} 个冲突文件没有解决（解决后在“更改”里勾选暂存它们）")
            return
        self.run(gitops.CONTINUE_ARGS[op], f"继续{gitops.OPERATION_NAMES[op]}", accept_default_message=True)

    # ================================================================ 分支
    def _local_names(self) -> set[str]:
        return {b.name for b in self._branches() if b.kind == "local"}

    def switch_branch(self, b: BranchInfo) -> None:
        if b.is_current:
            return
        if b.kind == "local":
            self.run(["switch", b.name], f"切换到 {b.name}")
            return
        local = b.name.partition("/")[2]
        if local and local in self._local_names():
            self.run(["switch", local], f"切换到 {local}")
        else:
            self.run(["switch", "--track", b.name], f"检出 {b.name}")

    def create_branch(self, start: str, start_label: str) -> None:
        name = self.ask_text(self.widget, "新建分支", f"基于 {start_label} 新建分支并切换过去。\n\n分支名：", "")
        if name:
            self.run(["switch", "-c", name, start], f"新建分支 {name}")

    def merge_branch(self, b: BranchInfo) -> None:
        cur = self._current_name()
        if self.confirm(self.widget, "合并分支", f"把“{b.name}”合并到当前分支“{cur}”？"):
            self.run(["merge", b.name], f"合并 {b.name}")

    def rebase_onto(self, b: BranchInfo) -> None:
        cur = self._current_name()
        if self.confirm(self.widget, "变基", f"把当前分支“{cur}”变基到“{b.name}”上？\n\n"
                                             "当前分支独有的提交会被改写（提交 ID 会变）。"):
            self.run(["rebase", b.name], f"变基到 {b.name}")

    def delete_branch(self, b: BranchInfo) -> None:
        if b.kind == "remote":
            remote, _, name = b.name.partition("/")
            if self.confirm(self.widget, "删除远端分支",
                            f"从远端 {remote} 上删除分支“{name}”？\n\n其他人将再也拉取不到这个分支。"):
                self.run(["push", remote, "--delete", name], f"删除远端分支 {b.name}")
            return
        if b.is_current:
            self._fail("删除分支", "不能删除当前所在的分支，请先切换到别的分支")
            return
        if not self.confirm(self.widget, "删除分支", f"删除本地分支“{b.name}”？"):
            return

        def done(res: OpResult) -> None:
            if not res.ok and "not fully merged" in res.output and self.confirm(
                    self.widget, "强制删除分支",
                    f"分支“{b.name}”还有没合并的提交。\n\n仍然删除？这些提交之后不容易找回。"):
                self.run(["branch", "-D", b.name], f"强制删除分支 {b.name}")

        self.run(["branch", "-d", b.name], f"删除分支 {b.name}", on_done=done)

    def _current_name(self) -> str:
        st = self._status()
        if st and st.branch:
            return st.branch
        return "HEAD"

    def pick_branch(self, title: str, action: Callable[[BranchInfo], None], *, include_current: bool = False,
                    local_only: bool = False) -> None:
        """弹出分支搜索框，选中后执行 action。"""
        branches = [b for b in self._branches()
                    if (include_current or not b.is_current) and (not local_only or b.kind == "local")]
        if not branches:
            self._fail(title, "没有可选的分支（分支列表还在加载时请稍后再试）")
            return
        items = [((f"{theme.CLOUD} " if b.kind == "remote" else "⎇ ") + b.name
                  + (f"    {b.subject}" if b.subject else ""), b) for b in branches]
        chosen = self.pick(self.widget, title, items)
        if isinstance(chosen, BranchInfo):
            action(chosen)

    # ================================================================ 提交图上的操作
    def commit_action(self, op: str, c: Commit, arg: str = "") -> None:
        title = f"{c.short} {c.subject}"
        merge_opts = ["-m", "1"] if len(c.parents) > 1 else []     # 合并提交：相对第一个父提交
        if op == "branch":
            self.create_branch(c.oid, c.short)
        elif op == "tag":
            name = self.ask_text(self.widget, "新建标签", f"在提交 {title} 上新建标签。\n\n标签名：", "")
            if name:
                self.run(["tag", name, c.oid], f"新建标签 {name}")
        elif op == "switch":
            self.run(["switch", arg], f"切换到 {arg}")
        elif op == "checkout":
            if self.confirm(self.widget, "检出提交", f"检出提交 {title}？\n\n"
                                                    "会进入“HEAD 分离”状态：在这里做的新提交不属于任何分支，"
                                                    "要保留它们请随后新建分支。"):
                self.run(["switch", "--detach", c.oid], f"检出 {c.short}")
        elif op == "cherry-pick":
            if self.confirm(self.widget, "拣选提交", f"把提交 {title} 应用到当前分支“{self._current_name()}”？"):
                self.run(["cherry-pick", *merge_opts, c.oid], f"拣选 {c.short}")
        elif op == "revert":
            if self.confirm(self.widget, "撤销提交", f"新建一个提交，撤销 {title} 的改动？"):
                self.run(["revert", "--no-edit", *merge_opts, c.oid], f"撤销 {c.short}")
        elif op in ("reset-soft", "reset-mixed", "reset-hard"):
            mode = op.split("-")[1]
            notes = {"soft": "改动保留在暂存区。", "mixed": "改动保留在工作区（不暂存）。",
                     "hard": "工作区和暂存区里所有未提交的改动都会丢掉！"}
            if self.confirm(self.widget, "重置当前分支",
                            f"把当前分支“{self._current_name()}”重置到 {title}（--{mode}）？\n\n{notes[mode]}"):
                self.run(["reset", f"--{mode}", c.oid], f"重置到 {c.short}")

    # ================================================================ 暂存 / 提交
    @staticmethod
    def _paths(changes: list[FileChange]) -> list[str]:
        out: list[str] = []
        for ch in changes:
            out.append(ch.path)
            if ch.orig_path:
                out.append(ch.orig_path)        # 重命名：新旧两个路径都要带上
        return out

    def stage(self, changes: list[FileChange]) -> None:
        if changes:
            self.run(gitops.stage_args(self._paths(changes)), "暂存")

    def unstage(self, changes: list[FileChange]) -> None:
        if not changes:
            return
        st = self._status()
        self.run(gitops.unstage_args(self._paths(changes), bool(st and st.head_oid)), "取消暂存")

    def discard(self, changes: list[FileChange]) -> None:
        if not changes:
            return
        tracked = self._paths([c for c in changes if c.kind != "U"])
        untracked = [c.path for c in changes if c.kind == "U"]
        text = f"放弃这 {len(changes)} 个文件的改动？\n\n{_file_list([c.path for c in changes])}\n\n"
        if untracked:
            text += "其中未跟踪的新文件会被删除。"
        text += "放弃的改动无法恢复。"
        if not self.confirm(self.widget, "放弃改动", text):
            return
        cmds = gitops.discard_commands(tracked, untracked)
        self.run(cmds[0], "放弃改动", sequence=cmds)

    def commit(self, message: str, amend: bool = False) -> None:
        message = message.strip()
        if not message:
            self._fail("提交", "请先填写提交说明")
            return
        st = self._status()
        staged = [c for c in (st.changes if st else []) if c.x not in (".", "?")]
        args = gitops.commit_args(message, amend=amend)
        label = "修改上一次提交" if amend else "提交"
        done = self._commit_done
        if staged or amend:
            self.run(args, label, on_done=done)
            return
        if not st or not st.changes:
            self._fail("提交", "没有任何改动可以提交")
            return
        if self.confirm(self.widget, "提交",
                        f"还没有暂存任何文件。\n\n暂存全部 {st.total_changes} 个改动的文件并提交？"):
            self.run(["add", "-A"], label, sequence=[["add", "-A"], args], on_done=done)

    def _commit_done(self, res: OpResult) -> None:
        if res.ok:
            self.commitDone.emit()

    def load_last_message(self, on_ok: Callable[[str], None]) -> None:
        repo = self.repo
        self.runner.submit(lambda: gitops.last_commit_message(repo), on_ok, lambda _e: None)

    # ================================================================ 储藏
    def stash_push(self) -> None:
        st = self._status()
        if st is not None and not st.changes:
            self._fail("储藏", "没有未提交的改动")
            return
        msg = self.ask_text(self.widget, "储藏改动",
                            "把所有未提交的改动（包括未跟踪的文件）储藏起来。\n\n说明（可以不填）：", "")
        if msg is None:
            return
        args = ["stash", "push", "--include-untracked"] + (["-m", msg] if msg else [])
        self.run(args, "储藏")

    def stash_pop(self) -> None:
        self.run(["stash", "pop"], "恢复储藏")

    def stash_browse(self) -> None:
        repo = self.repo
        self.runner.submit(lambda: gitops.stash_list(repo), self._on_stash_list,
                           lambda e: self._fail("储藏列表", str(e)))

    def _on_stash_list(self, stashes: list[tuple[str, str]]) -> None:
        if not self._alive:
            return
        if not stashes:
            self._fail("储藏列表", "没有储藏的改动")
            return
        name = self.pick(self.widget, "储藏列表", [(f"{n}    {s}", n) for n, s in stashes])
        if not name:
            return
        i = self.choose(self.widget, "储藏", f"{name}：", ["恢复并删除（pop）", "只恢复（apply）", "删除（drop）"])
        if i == 0:
            self.run(["stash", "pop", name], "恢复储藏")
        elif i == 1:
            self.run(["stash", "apply", name], "恢复储藏")
        elif i == 2 and self.confirm(self.widget, "删除储藏", f"删除 {name}？删除后不容易找回。"):
            self.run(["stash", "drop", name], "删除储藏")

