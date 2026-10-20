"""ggit 的 Git 写操作层：执行会修改仓库或联网的 git 命令（fetch / pull / push / 提交 / 命令面板里输入的命令）。

和 gitcore（只读）分开：
  * 不设 GIT_OPTIONAL_LOCKS=0：写操作要正常加锁
  * 输出边执行边回传（on_output），进度行（以 \\r 结尾）由界面原地覆盖
  * 不会卡住等输入：没有终端、不弹编辑器、SSH 不问密码（BatchMode）；HTTPS 认证交给 Git Credential Manager
  * 没有超时，靠取消结束（连同 ssh 等子进程一起结束）
  * 失败不抛异常：返回码和输出都放进 OpResult，由界面显示

本模块不依赖任何界面库，可以单独测试。
"""
from __future__ import annotations

import io
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from .cancel import GitCancelledError, get_current_cancel_token
from .gitcore import _NO_WINDOW, GitError, RepoStatus, decode_line, find_git, run_git

OutputFn = Callable[[str], None]

PULL_FF_ONLY = "ff-only"
PULL_REBASE = "rebase"
PULL_MERGE = "merge"
PULL_MODES = (PULL_FF_ONLY, PULL_REBASE, PULL_MERGE)

# 需要编辑器时：直接失败并说明原因（git 会在后面追加文件名参数，false 不理会它）。
# 只能用 ASCII：这一串经过 Git 自带的 sh 转一道，中文会变成乱码
_NO_EDITOR = "echo 'ggit: no interactive editor available, pass the text with -m' >&2; false"


@dataclass
class OpResult:
    args: list[str]
    returncode: int
    output: str          # 全部输出（进度行只保留最后一次的内容）
    duration: float      # 秒

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# --------------------------------------------------------------------------
# 输出：按行切开，进度行原地覆盖
# --------------------------------------------------------------------------
class OutputSplitter:
    """把子进程输出的字节流切成行。每一段以 "\\n" 或 "\\r" 结尾（"\\r" 表示下一段要覆盖这一行）。

    逐行解码（UTF-8 失败再 GBK），所以多字节字符被切在两次读取之间也没关系。
    """

    def __init__(self) -> None:
        self._buf = b""
        self._lines: list[str] = []
        self._overwrite = False          # 上一段以 \r 结尾：下一段替换它

    def feed(self, data: bytes) -> list[str]:
        self._buf += data
        out: list[str] = []
        start = 0
        i = 0
        n = len(self._buf)
        while i < n:
            ch = self._buf[i]
            if ch in (0x0A, 0x0D):
                term = "\n"
                if ch == 0x0D:
                    if i + 1 >= n:          # \r 是最后一个字节：可能后面紧跟 \n，等下一块数据再定
                        break
                    if self._buf[i + 1] == 0x0A:
                        i += 1              # \r\n 当作普通换行
                    else:
                        term = "\r"
                line = decode_line(self._buf[start:i + 1].rstrip(b"\r\n"))
                out.append(line + term)
                self._record(line, term)
                start = i + 1
            i += 1
        self._buf = self._buf[start:]
        return out

    def flush(self) -> list[str]:
        """进程结束：剩下不完整的一行也交出去。"""
        rest = self._buf.rstrip(b"\r")
        self._buf = b""
        if not rest:
            return []
        line = decode_line(rest)
        self._record(line, "\n")
        return [line + "\n"]

    def _record(self, line: str, term: str) -> None:
        if self._overwrite and self._lines:
            self._lines[-1] = line
        else:
            self._lines.append(line)
        self._overwrite = term == "\r"

    def text(self) -> str:
        return "\n".join(self._lines)


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------
def _ssh_configured(repo: str) -> bool:
    """用户自己指定了 ssh 程序（环境变量或 core.sshCommand）：不要覆盖它。"""
    if os.environ.get("GIT_SSH_COMMAND") or os.environ.get("GIT_SSH"):
        return True
    try:
        return bool(run_git(repo, ["config", "--get", "core.sshCommand"], check=False).strip())
    except GitError:
        return False


def git_env(repo: str, *, accept_default_message: bool = False) -> dict[str, str]:
    """写操作用的环境变量。accept_default_message=True：需要编辑提交说明时直接用默认的（继续合并 / 变基）。"""
    env = dict(os.environ)
    env.pop("GIT_OPTIONAL_LOCKS", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["GIT_MERGE_AUTOEDIT"] = "no"
    editor = ":" if accept_default_message else _NO_EDITOR      # ":"：git 认得它，表示不打开编辑器
    env["GIT_EDITOR"] = editor
    env["GIT_SEQUENCE_EDITOR"] = editor
    if not _ssh_configured(repo):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"          # 没有 ssh-agent 时直接报错，不卡住等密码
    return env


def run_git_stream(repo: str, args: list[str], on_output: OutputFn | None = None, *,
                   accept_default_message: bool = False) -> OpResult:
    """执行一条会修改仓库 / 联网的 git 命令。输出一段一段交给 on_output（在调用者的线程里）。

    git 本身失败不抛异常（看 OpResult.returncode）；找不到 git、无法启动时抛 GitError；被取消时抛 GitCancelledError。
    """
    cancel_token = get_current_cancel_token()
    if cancel_token and cancel_token.cancelled:
        raise GitCancelledError("操作已取消")
    exe = find_git()
    if not exe:
        raise GitError("未找到 git。请先安装 Git，并确保 git 在 PATH 中。")
    if not os.path.isdir(repo):
        raise GitError(f"目录不存在：{repo}")
    cmd = [exe, "-c", "core.quotepath=false", "-c", "color.ui=false", *args]
    env = git_env(repo, accept_default_message=accept_default_message)
    started = time.monotonic()
    splitter = OutputSplitter()
    try:
        proc = subprocess.Popen(cmd, cwd=repo, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, creationflags=_NO_WINDOW, env=env)
    except OSError as e:
        raise GitError(f"无法运行 git：{e}") from e
    if cancel_token:
        cancel_token.set_proc(proc, tree=True)
    try:
        out = proc.stdout
        assert isinstance(out, io.BufferedReader)
        while True:
            chunk = out.read1(65536)     # 有多少读多少，不等凑满
            if not chunk:
                break
            for piece in splitter.feed(chunk):
                if on_output:
                    on_output(piece)
        for piece in splitter.flush():
            if on_output:
                on_output(piece)
        returncode = proc.wait()
    finally:
        if cancel_token:
            cancel_token.set_proc(None)
        if proc.stdout:
            proc.stdout.close()
    if cancel_token and cancel_token.cancelled:
        raise GitCancelledError("操作已取消")
    return OpResult(list(args), returncode, splitter.text(), time.monotonic() - started)


def run_sequence(repo: str, commands: list[list[str]], on_output: OutputFn | None = None) -> OpResult:
    """依次执行几条命令，遇到失败就停下（比如“暂存后提交”）。每条命令前输出一行 "$ git …"。"""
    started = time.monotonic()
    texts: list[str] = []
    res = OpResult([], 0, "", 0.0)
    for i, args in enumerate(commands):
        if i and on_output:
            on_output(f"$ git {format_command(args)}\n")
        res = run_git_stream(repo, args, on_output)
        texts.append(res.output)
        if not res.ok:
            break
    return OpResult(res.args, res.returncode, "\n".join(t for t in texts if t), time.monotonic() - started)


# --------------------------------------------------------------------------
# 命令面板：拆分输入的命令、检查
# --------------------------------------------------------------------------
class CommandError(ValueError):
    """输入的命令无法执行（格式不对、需要交互）。消息可直接显示给用户。"""


def split_command(text: str) -> list[str]:
    """把输入的一行拆成参数（Windows 习惯：反斜杠是普通字符，只在双引号里用 \\" 表示引号）。

    单引号、双引号都可以把带空格的内容括起来。开头的 "git" 可写可不写。
    """
    args: list[str] = []
    cur: list[str] = []
    has_token = False
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(text) and text[i + 1] == '"':
                cur.append('"')
                i += 1
            elif ch == quote:
                quote = ""
            else:
                cur.append(ch)
        elif ch in ("'", '"'):
            quote = ch
            has_token = True
        elif ch.isspace():
            if has_token:
                args.append("".join(cur))
                cur, has_token = [], False
        else:
            cur.append(ch)
            has_token = True
        i += 1
    if quote:
        raise CommandError("引号没有配对")
    if has_token:
        args.append("".join(cur))
    if args and args[0].lower() in ("git", "git.exe"):
        args = args[1:]
    if not args:
        raise CommandError("请输入 git 命令，例如：status、log --oneline -5")
    return args


def format_command(args: list[str]) -> str:
    """把参数拼回一行显示（带空格或引号的参数加上双引号）。"""
    out = []
    for a in args:
        if not a or any(c.isspace() for c in a) or '"' in a or "'" in a:
            out.append('"' + a.replace('"', '\\"') + '"')
        else:
            out.append(a)
    return " ".join(out)


_GLOBAL_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}


def subcommand(args: list[str]) -> tuple[str, list[str]]:
    """跳过全局选项（-c x=y、-C 目录、--no-pager…），返回 (子命令, 子命令的参数)。没有子命令时子命令为空串。"""
    i = 0
    while i < len(args):
        a = args[i]
        if not a.startswith("-"):
            return a, args[i + 1:]
        i += 2 if a in _GLOBAL_WITH_VALUE else 1
    return "", []


def _has(opts: list[str], *names: str) -> bool:
    """opts 里有没有这些选项。长选项也认 --name=value；短选项也认直接跟值的 -mxxx，
    以及合在一起写的开关（-fd 里的 -f，最多 3 个字母，免得把 -mfixed 里的 e 当成 -e）。"""
    for o in opts:
        if o == "--":
            break
        if not o.startswith("-"):
            continue
        for name in names:
            if o == name or (name.startswith("--") and o.startswith(name + "=")):
                return True
            if len(name) == 2 and not o.startswith("--") and (
                    o.startswith(name) or (len(o) <= 4 and o[1:].isalpha() and name[1] in o[1:])):
                return True
    return False


# 不会修改仓库的子命令：执行完不用刷新
READ_ONLY = {
    "status", "log", "show", "diff", "blame", "grep", "ls-files", "ls-tree", "ls-remote", "rev-parse",
    "rev-list", "cat-file", "describe", "shortlog", "whatchanged", "version", "help", "show-ref",
    "for-each-ref", "name-rev", "count-objects", "check-ignore", "merge-base", "cherry", "var",
    "show-branch", "range-diff", "verify-commit", "verify-tag", "fsck", "annotate",
}

# 需要图形界面 / 交互，命令面板里不能用
_INTERACTIVE = {
    "gui": "会打开 Git GUI 窗口", "citool": "会打开 Git GUI 窗口", "gitk": "会打开 gitk 窗口",
    "difftool": "需要外部比较工具交互", "mergetool": "需要外部合并工具交互",
    "daemon": "会一直运行", "instaweb": "会一直运行", "web--browse": "会打开浏览器",
    "credential": "需要从标准输入读取内容", "send-email": "需要交互",
}

_MESSAGE_OPTS = ("-m", "--message", "-F", "--file", "-C", "--reuse-message", "--no-edit", "--fixup")


@dataclass
class CommandCheck:
    blocked: str = ""       # 不能执行的原因（空串：可以执行）
    danger: str = ""        # 需要确认的原因（空串：不用确认）
    writes: bool = True     # 可能修改仓库：执行完要刷新


def check_command(args: list[str]) -> CommandCheck:
    """检查命令面板里输入的命令：会卡住等交互的拒绝执行；会丢掉数据的要求确认。"""
    sub, opts = subcommand(args)
    res = CommandCheck(writes=sub not in READ_ONLY)
    if not sub:
        return res
    if sub in _INTERACTIVE:
        res.blocked = f"git {sub} {_INTERACTIVE[sub]}，不能在 ggit 里执行"
    elif sub in ("rebase", "clean") and _has(opts, "-i", "--interactive"):
        res.blocked = f"交互式 {sub}（-i）需要编辑器，不能在 ggit 里执行"
    elif sub in ("add", "checkout", "reset", "restore", "stash", "commit") and _has(opts, "-p", "--patch"):
        res.blocked = "逐块选择（-p / --patch）需要交互，不能在 ggit 里执行"
    elif sub == "add" and _has(opts, "-i", "--interactive", "-e", "--edit"):
        res.blocked = "交互式 add 需要交互，不能在 ggit 里执行"
    elif sub == "commit" and not _has(opts, *_MESSAGE_OPTS):
        res.blocked = "请用 -m \"说明\" 提供提交说明（ggit 不能打开编辑器）；修改上次提交而不改说明用 --amend --no-edit"
    elif sub == "commit" and _has(opts, "-c", "--reedit-message", "-e", "--edit"):
        res.blocked = "编辑提交说明需要编辑器，请改用 -m"
    elif sub == "merge" and _has(opts, "-e", "--edit"):
        res.blocked = "编辑合并说明需要编辑器，请改用 -m"
    elif sub == "tag" and _has(opts, "-a", "--annotate", "-s", "--sign") and not _has(opts, "-m", "-F", "--message",
                                                                                       "--file"):
        res.blocked = "带说明的标签请用 -m 提供说明（ggit 不能打开编辑器）"
    if res.blocked:
        return res
    res.danger = _danger(sub, opts)
    return res


def _danger(sub: str, opts: list[str]) -> str:
    plain = [o for o in opts if not o.startswith("-")]
    if sub == "reset" and _has(opts, "--hard"):
        return "reset --hard 会丢掉所有未提交的改动"
    if sub == "clean" and not _has(opts, "-n", "--dry-run"):
        return "clean 会删除未跟踪的文件，删除后无法恢复"
    if sub == "push":
        if _has(opts, "-f", "--force", "--force-with-lease", "--force-if-includes"):
            return "强制推送会覆盖远端的提交"
        if _has(opts, "--mirror", "-d", "--delete", "--prune") or any(p.startswith(":") for p in plain):
            return "这条 push 会删除远端的分支或标签"
        if any(p.startswith("+") for p in plain):
            return "带 + 的引用会强制覆盖远端"
    if sub == "branch" and (_has(opts, "-D") or (_has(opts, "-d", "--delete") and _has(opts, "-f", "--force"))):
        return "强制删除分支，没合并的提交可能会丢失"
    if sub == "branch" and _has(opts, "-M", "-C"):
        return "会覆盖同名的已有分支"
    if sub in ("checkout", "switch") and _has(opts, "-f", "--force", "--discard-changes"):
        return "会丢掉未提交的改动"
    if sub == "checkout" and "--" in opts:
        return "checkout -- <文件> 会丢掉这些文件未提交的改动"
    if sub == "checkout" and plain == ["."]:
        return "checkout . 会丢掉所有未提交的改动"
    if sub == "restore" and (not _has(opts, "-S", "--staged") or _has(opts, "-W", "--worktree")):
        return "restore 会丢掉这些文件在工作区里未提交的改动"
    if sub == "stash" and plain[:1] in (["drop"], ["clear"]):
        return "删除的储藏（stash）不容易找回"
    if sub == "tag" and _has(opts, "-d", "--delete"):
        return "会删除标签"
    if sub == "remote" and plain[:1] in (["remove"], ["rm"]):
        return "会删除远端配置和它的所有远端分支"
    if sub in ("filter-branch", "filter-repo", "replace"):
        return f"{sub} 会改写历史"
    if sub == "update-ref" and _has(opts, "-d"):
        return "会直接删除引用"
    if sub == "reflog" and plain[:1] in (["expire"], ["delete"]):
        return "会删除 reflog 记录（找回丢失提交的依据）"
    if sub in ("gc", "prune") and any(o.startswith("--prune") for o in opts):
        return "会永久清理不再被引用的提交"
    return ""


# --------------------------------------------------------------------------
# 常用操作的命令参数
# --------------------------------------------------------------------------
def fetch_args() -> list[str]:
    return ["fetch", "--all", "--prune", "--progress"]


def pull_args(mode: str = PULL_FF_ONLY) -> list[str]:
    flag = {PULL_FF_ONLY: "--ff-only", PULL_REBASE: "--rebase", PULL_MERGE: "--no-rebase"}.get(mode, "--ff-only")
    return ["pull", flag, "--progress"]


def remotes(repo: str) -> list[str]:
    out = run_git(repo, ["remote"])
    return [r for r in (decode_line(x).strip() for x in out.split(b"\n")) if r]


def push_remote(repo: str) -> str:
    """没有上游的分支第一次推送时用哪个远端：remote.pushDefault > origin > 唯一的那个远端。找不到时抛 GitError。"""
    names = remotes(repo)
    if not names:
        raise GitError("这个仓库还没有配置远端（git remote add origin <地址>）")
    configured = decode_line(run_git(repo, ["config", "--get", "remote.pushDefault"], check=False)).strip()
    if configured in names:
        return configured
    if "origin" in names:
        return "origin"
    if len(names) == 1:
        return names[0]
    raise GitError(f"有多个远端（{'、'.join(names)}），不知道推送到哪个；请在命令面板里用 push -u <远端> <分支>")


def push_args(status: RepoStatus, remote: str = "", *, force: bool = False) -> list[str]:
    """推送当前分支。没有上游时加 -u <remote> <分支>（remote 由调用者用 push_remote 选好）。"""
    if not status.branch:
        raise GitError("当前不在任何分支上（HEAD 分离），无法推送")
    args = ["push", "--progress"]
    if force:
        args.append("--force-with-lease")
    if not status.upstream:
        if not remote:
            raise GitError("没有指定推送到哪个远端")
        args += ["-u", remote, status.branch]
    return args


ABORT_ARGS = {"merge": ["merge", "--abort"], "rebase": ["rebase", "--abort"],
              "cherry-pick": ["cherry-pick", "--abort"], "revert": ["revert", "--abort"]}
CONTINUE_ARGS = {"merge": ["commit", "--no-edit"], "rebase": ["rebase", "--continue"],
                 "cherry-pick": ["cherry-pick", "--continue"], "revert": ["revert", "--continue"]}
OPERATION_NAMES = {"merge": "合并", "rebase": "变基", "cherry-pick": "拣选（cherry-pick）",
                   "revert": "撤销提交（revert）"}


def stage_args(paths: list[str]) -> list[str]:
    return ["add", "-A", "--", *paths]


def unstage_args(paths: list[str], has_head: bool) -> list[str]:
    if has_head:
        return ["restore", "--staged", "--", *paths]
    return ["rm", "-r", "-q", "--cached", "--", *paths]     # 还没有任何提交：从暂存区移除


def discard_commands(tracked: list[str], untracked: list[str]) -> list[list[str]]:
    """放弃改动：已跟踪的恢复成 HEAD 的样子（暂存区和工作区都恢复），未跟踪的删除。"""
    cmds = []
    if tracked:
        cmds.append(["restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked])
    if untracked:
        cmds.append(["clean", "-f", "-q", "--", *untracked])
    return cmds


def commit_args(message: str, *, amend: bool = False) -> list[str]:
    args = ["commit", "-m", message]
    if amend:
        args.append("--amend")
    return args


def last_commit_message(repo: str) -> str:
    return decode_line(run_git(repo, ["log", "-1", "--format=%B"]).rstrip(b"\r\n")).replace("\r\n", "\n")


def stash_list(repo: str) -> list[tuple[str, str]]:
    """[(stash@{n}, 说明)]，最新的在前。"""
    out = run_git(repo, ["stash", "list", "--format=%gd%x1f%gs%x1f%cr"])
    res = []
    for raw in out.split(b"\n"):
        if not raw.strip():
            continue
        name, _, rest = decode_line(raw).partition("\x1f")
        subject, _, when = rest.partition("\x1f")
        res.append((name, f"{subject}  ({when})" if when else subject))
    return res
