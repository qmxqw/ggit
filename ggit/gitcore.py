"""ggit 的 Git 访问层（只读）。

只通过 git 命令行读取数据，负责：
  * 调用 git，处理 Windows 下的子进程细节（不弹黑窗口、不抢索引锁）
  * 解码输出：逐行先按 UTF-8 严格解码，失败再按 GB18030（GBK 的超集）
  * 解析 status / log / diff / 文件历史

本模块不依赖任何界面库，可以单独测试。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

IS_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW

ENC_AUTO = "auto"
ENC_UTF8 = "utf-8"
ENC_GBK = "gb18030"

SCOPE_AUTO = "auto"  # 当前分支 + 它的远端对应分支
SCOPE_ALL = "all"    # 所有本地分支、远端分支、标签

LOG_PAGE = 500
FULL_CONTEXT = 1_000_000   # diff 一律取整个文件的上下文，界面再按需裁剪成“只看改动处”
MAX_UNTRACKED_BYTES = 5 * 1024 * 1024
MAX_DIFF_BYTES = 10 * 1024 * 1024
MAX_DIFF_PARSE_LINES = 30000
TRUNCATED_NOTE = "… 差异内容过大，已自动截断，后面的内容没有显示"


class GitError(Exception):
    """git 调用失败。消息可直接显示给用户。"""


# --------------------------------------------------------------------------
# 基础：找到 git、解码、调用
# --------------------------------------------------------------------------
_git_exe: Optional[str] = None


def find_git() -> Optional[str]:
    global _git_exe
    if _git_exe:
        return _git_exe
    exe = shutil.which("git")
    if not exe and IS_WIN:
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            for sub in (r"Git\cmd\git.exe", r"Programs\Git\cmd\git.exe"):
                cand = os.path.join(base, sub)
                if os.path.isfile(cand):
                    exe = cand
                    break
            if exe:
                break
    _git_exe = exe
    return exe


def decode_line(raw: bytes, encoding: str = ENC_AUTO) -> str:
    """解码一行字节。auto：先 UTF-8（严格），失败再 GB18030。

    必须按“行”解码：同一个 diff 里可能旧行是 GBK、新行是 UTF-8。
    顺序不能反过来：先试 GBK 会把一部分 UTF-8 文本误解成乱码。
    """
    if encoding and encoding != ENC_AUTO:
        return raw.decode(encoding, errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def run_git(repo: str, args: list[str], *, check: bool = True, timeout: int = 120) -> bytes:
    from .async_runner import GitCancelledError, get_current_cancel_token
    cancel_token = get_current_cancel_token()
    if cancel_token and cancel_token.cancelled:
        raise GitCancelledError("操作已取消")

    exe = find_git()
    if not exe:
        raise GitError("未找到 git。请先安装 Git，并确保 git 在 PATH 中。")
    cmd = [exe, "-c", "core.quotepath=false", "-c", "color.ui=false",
           "-c", "core.fsmonitor=false", *args]
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"      # 只读：status 不去更新索引、不抢锁
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.Popen(
            cmd, cwd=repo, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW, env=env,
        )
        if cancel_token:
            cancel_token.set_proc(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        finally:
            if cancel_token:
                cancel_token.set_proc(None)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.communicate()
        except OSError:
            pass
        raise GitError(f"git {args[0]} 超时（超过 {timeout} 秒）")
    except OSError as e:
        raise GitError(f"无法运行 git：{e}")

    if cancel_token and cancel_token.cancelled:
        raise GitCancelledError("操作已取消")

    if check and proc.returncode != 0:
        msg = decode_line(stderr).strip() or f"git {args[0]} 失败（返回码 {proc.returncode}）"
        raise GitError(msg)
    return stdout


def toplevel(path: str) -> str:
    """返回 path 所在仓库的根目录；不是仓库则抛 GitError。"""
    out = run_git(path, ["rev-parse", "--show-toplevel"])
    return os.path.normpath(decode_line(out).strip())


def _git_dir(repo: str) -> Optional[str]:
    dot = os.path.join(repo, ".git")
    if os.path.isdir(dot):
        return dot
    if os.path.isfile(dot):  # worktree / 子模块：.git 是个指向真实目录的文件
        try:
            with open(dot, "rb") as f:
                line = decode_line(f.readline()).strip()
            if line.startswith("gitdir:"):
                p = line[7:].strip()
                if not os.path.isabs(p):
                    p = os.path.join(repo, p)
                return os.path.normpath(p)
        except OSError:
            pass
    return None


def _last_fetch_time(repo: str) -> Optional[float]:
    gd = _git_dir(repo)
    if not gd:
        return None
    try:
        return os.path.getmtime(os.path.join(gd, "FETCH_HEAD"))
    except OSError:
        return None


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
@dataclass
class FileChange:
    path: str
    orig_path: Optional[str]
    x: str       # 暂存区状态字符，'.' 表示无
    y: str       # 工作区状态字符
    kind: str    # M 修改 / A 新增 / D 删除 / R 重命名 / U 未跟踪 / C 冲突


@dataclass
class RepoStatus:
    head_oid: str = ""                 # 空串：还没有任何提交
    branch: Optional[str] = None       # None：HEAD 分离
    upstream: Optional[str] = None
    ahead: int = 0
    behind: int = 0
    changes: list = field(default_factory=list)
    last_fetch: Optional[float] = None

    @property
    def dirty(self) -> bool:
        return bool(self.changes)


@dataclass
class Snapshot:
    status: RepoStatus
    refs: dict           # 完整引用名 -> 提交 id
    fingerprint: str     # 引用/HEAD 的指纹：没变就不用重新加载提交图


@dataclass
class Ref:
    name: str
    kind: str            # local / remote / tag / head（分离的 HEAD）
    is_head: bool = False


@dataclass
class Commit:
    oid: str
    parents: list
    author: str
    time: int
    refs: list
    subject: str

    @property
    def short(self) -> str:
        return self.oid[:7]


@dataclass
class CommitFile:
    status: str          # M A D R C T
    path: str
    old_path: Optional[str] = None


@dataclass
class DiffLine:
    kind: str            # ctx / add / del / hunk / note（解析时生成的提示行：分段标题、截断说明）
    old_no: Optional[int]
    new_no: Optional[int]
    text: str


@dataclass
class FileDiff:
    path: str = ""
    old_path: Optional[str] = None
    is_binary: bool = False
    lines: list = field(default_factory=list)
    added: int = 0
    deleted: int = 0
    message: str = ""
    truncated: bool = False   # 内容过大，只解析了前面一部分


@dataclass
class BranchInfo:
    name: str            # 短名：main / origin/feature
    ref: str             # 完整引用名：refs/heads/main / refs/remotes/origin/feature
    kind: str            # local / remote
    oid: str
    time: int            # 最新提交的时间
    upstream: str        # 本地分支的上游分支（没有则空串）
    ahead: int
    behind: int
    gone: bool           # 上游分支已经不存在
    is_current: bool
    subject: str


@dataclass
class HistoryEntry:
    commit: Commit
    path: str
    old_path: Optional[str]
    status: str


# --------------------------------------------------------------------------
# status / 引用快照
# --------------------------------------------------------------------------
def _kind(x: str, y: str) -> str:
    s = x + y
    if "D" in s:
        return "D"
    if "R" in s or "C" in s:
        return "R"
    if "A" in s:
        return "A"
    return "M"


def parse_status(data: bytes) -> RepoStatus:
    """解析 `git status --porcelain=v2 --branch -z` 的输出。"""
    st = RepoStatus()
    toks = data.split(b"\0")
    i = 0
    while i < len(toks):
        rec = toks[i]
        i += 1
        if not rec:
            continue
        s = decode_line(rec)
        if s.startswith("# "):
            parts = s[2:].split(" ")
            key = parts[0]
            if key == "branch.oid":
                st.head_oid = "" if parts[1] == "(initial)" else parts[1]
            elif key == "branch.head":
                name = s[len("# branch.head "):]
                st.branch = None if name == "(detached)" else name
            elif key == "branch.upstream":
                st.upstream = s[len("# branch.upstream "):]
            elif key == "branch.ab":
                st.ahead = int(parts[1].lstrip("+"))
                st.behind = abs(int(parts[2]))
        elif s[0] == "1":
            f = s.split(" ", 8)
            xy = f[1]
            st.changes.append(FileChange(f[8], None, xy[0], xy[1], _kind(xy[0], xy[1])))
        elif s[0] == "2":
            f = s.split(" ", 9)
            xy = f[1]
            orig = decode_line(toks[i]) if i < len(toks) else None
            i += 1
            st.changes.append(FileChange(f[9], orig, xy[0], xy[1], _kind(xy[0], xy[1])))
        elif s[0] == "u":
            f = s.split(" ", 10)
            st.changes.append(FileChange(f[10], None, "U", "U", "C"))
        elif s[0] == "?":
            st.changes.append(FileChange(s[2:], None, "?", "?", "U"))
    return st


def get_status(repo: str, untracked: str = "all") -> RepoStatus:
    """untracked：all = 列出未跟踪目录里的每个文件；normal = 未跟踪目录只算一条（快，只看“有没有改动”时用）。"""
    out = run_git(repo, ["status", "--porcelain=v2", "--branch", "-z", f"--untracked-files={untracked}"])
    st = parse_status(out)
    st.changes.sort(key=lambda c: c.path.lower())
    del st.changes[5000:]
    st.last_fetch = _last_fetch_time(repo)
    return st


def get_refs(repo: str) -> dict:
    out = run_git(repo, ["for-each-ref", "--format=%(objectname) %(refname)"])
    refs = {}
    for raw in out.split(b"\n"):
        if not raw:
            continue
        line = decode_line(raw)
        oid, _, name = line.partition(" ")
        if name and name != "refs/stash" and not name.startswith("refs/notes/"):
            refs[name] = oid
    return refs


def get_snapshot(repo: str) -> Snapshot:
    if not os.path.isdir(repo):
        raise GitError(f"目录不存在：{repo}")
    status = get_status(repo)
    refs = get_refs(repo)
    h = hashlib.sha1()
    h.update(status.head_oid.encode())
    h.update((status.branch or "").encode())
    for name in sorted(refs):
        h.update(f"{refs[name]} {name}\n".encode())
    return Snapshot(status, refs, h.hexdigest())


# --------------------------------------------------------------------------
# 分支列表（本地 + 远端）
# --------------------------------------------------------------------------
_BR_FMT = ("%(refname)%1f%(objectname)%1f%(committerdate:unix)%1f%(upstream:short)%1f"
           "%(upstream:track)%1f%(HEAD)%1f%(subject)")


def get_branches(repo: str) -> list:
    """所有本地分支和远端分支（远端的来自本地已有的远端引用）。当前分支排第一，其余按最近提交排序。"""
    out = run_git(repo, ["for-each-ref", "--sort=-committerdate", f"--format={_BR_FMT}",
                         "refs/heads", "refs/remotes"])
    res = []
    for raw in out.split(b"\n"):
        f = raw.split(b"\x1f")
        if len(f) < 7:
            continue
        ref = decode_line(f[0]).strip()
        if ref.startswith("refs/heads/"):
            kind, name = "local", ref[len("refs/heads/"):]
        elif ref.startswith("refs/remotes/"):
            kind, name = "remote", ref[len("refs/remotes/"):]
            if name.endswith("/HEAD"):       # origin/HEAD 只是个指向默认分支的符号引用
                continue
        else:
            continue
        track = decode_line(f[4])
        m_a, m_b = re.search(r"ahead (\d+)", track), re.search(r"behind (\d+)", track)
        try:
            ts = int(decode_line(f[2]).strip() or 0)
        except ValueError:
            ts = 0
        res.append(BranchInfo(
            name=name, ref=ref, kind=kind, oid=decode_line(f[1]).strip(), time=ts,
            upstream=decode_line(f[3]).strip(),
            ahead=int(m_a.group(1)) if m_a else 0, behind=int(m_b.group(1)) if m_b else 0,
            gone="gone" in track, is_current=decode_line(f[5]).strip() == "*",
            subject=decode_line(b"\x1f".join(f[6:])).strip()))
    res.sort(key=lambda b: (not b.is_current, b.kind != "local", -b.time))
    return res


# --------------------------------------------------------------------------
# 提交日志（提交图的数据来源）
# --------------------------------------------------------------------------
_FIELDS = "%H%x1f%P%x1f%an%x1f%at%x1f%D%x1f%s"


def _strip_prefix(s: str, prefix: str) -> str:
    return s[len(prefix):] if s.startswith(prefix) else s


def parse_decorations(s: str) -> list:
    """解析 `%D`（配合 --decorate=full）。返回 Ref 列表，当前分支排最前。"""
    refs = []
    for part in s.split(", "):
        part = part.strip()
        if not part:
            continue
        if part.startswith("tag: "):
            refs.append(Ref(_strip_prefix(part[5:], "refs/tags/"), "tag"))
        elif part.startswith("HEAD -> "):
            refs.append(Ref(_strip_prefix(part[8:], "refs/heads/"), "local", True))
        elif part == "HEAD":
            refs.append(Ref("HEAD", "head", True))
        elif part.startswith("refs/heads/"):
            refs.append(Ref(part[len("refs/heads/"):], "local"))
        elif part.startswith("refs/remotes/"):
            name = part[len("refs/remotes/"):]
            if not name.endswith("/HEAD"):
                refs.append(Ref(name, "remote"))
        # 其它（refs/stash、refs/notes/...）忽略
    order = {"local": 1, "remote": 2, "tag": 3, "head": 0}
    refs.sort(key=lambda r: (0 if r.is_head else 1, order.get(r.kind, 9)))
    return refs


def _commit_from_fields(f: list) -> Optional[Commit]:
    if len(f) < 6:
        return None
    oid = decode_line(f[0]).strip()
    parents = decode_line(f[1]).split()
    try:
        ts = int(decode_line(f[3]).strip())
    except ValueError:
        ts = 0
    subject = decode_line(b"\x1f".join(f[5:])).strip()
    return Commit(oid, parents, decode_line(f[2]), ts,
                  parse_decorations(decode_line(f[4])), subject)


def parse_log(data: bytes) -> list:
    commits = []
    for rec in data.split(b"\0"):
        if not rec.strip():
            continue
        c = _commit_from_fields(rec.lstrip(b"\n").split(b"\x1f"))
        if c:
            commits.append(c)
    return commits


def _log_revs(snap: Snapshot, scope: str) -> list:
    if scope.startswith("ref:"):          # 只看某一个分支（完整引用名）
        return [scope[4:]]
    if scope == SCOPE_ALL:
        return ["--branches", "--remotes", "--tags", "HEAD"]
    revs = ["HEAD"]
    up = snap.status.upstream
    if up and f"refs/remotes/{up}" in snap.refs:
        revs.append(f"refs/remotes/{up}")
    return revs


def get_log(repo: str, snap: Snapshot, scope: str = SCOPE_AUTO,
            skip: int = 0, limit: int = LOG_PAGE) -> list:
    if not snap.status.head_oid:
        return []
    args = ["log", "--date-order", "--decorate=full", "--no-color", "-z",
            f"--format={_FIELDS}", f"--max-count={limit}"]
    if skip:
        args.append(f"--skip={skip}")
    args += _log_revs(snap, scope)
    args.append("--")
    return parse_log(run_git(repo, args))


# --------------------------------------------------------------------------
# 单个提交的文件列表
# --------------------------------------------------------------------------
def parse_name_status_z(data: bytes) -> list:
    toks = data.split(b"\0")
    res = []
    i = 0
    while i < len(toks):
        st = decode_line(toks[i]).lstrip("\n")
        i += 1
        if not st:
            continue
        code = st[0]
        if code in "RC":
            if i + 1 >= len(toks):
                break
            old, new = decode_line(toks[i]), decode_line(toks[i + 1])
            i += 2
            res.append(CommitFile(code, new, old))
        else:
            if i >= len(toks):
                break
            res.append(CommitFile(code, decode_line(toks[i])))
            i += 1
    return res


def get_commit_files(repo: str, oid: str) -> list:
    """提交修改的文件。合并提交相对第一个父提交。"""
    out = run_git(repo, ["show", "--first-parent", "--format=", "--name-status",
                         "-M", "-z", "--no-ext-diff", oid, "--"])
    return parse_name_status_z(out)


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_CC_HUNK_RE = re.compile(r"^@@@ -(\d+)(?:,\d+)? -\d+(?:,\d+)? \+(\d+)")


def _diff_flags(ignore_ws: bool, ignore_eol: bool, context: int) -> list:
    flags = ["--no-color", "--no-ext-diff", "--no-textconv", f"-U{context}", "-M"]
    if ignore_ws:
        flags.append("-w")
    if ignore_eol:
        flags.append("--ignore-cr-at-eol")
    return flags


def parse_diff(data: bytes, encoding: str = ENC_AUTO) -> FileDiff:
    """解析统一 diff。行只分 ctx / add / del（不区分“修改行”）。"""
    fd = FileDiff()
    truncated = False
    if len(data) > MAX_DIFF_BYTES:
        pos = data.rfind(b"\n", 0, MAX_DIFF_BYTES)
        data = data[:pos] if pos != -1 else data[:MAX_DIFF_BYTES]
        truncated = True

    raw_lines = data.split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    in_hunk = False
    combined = False
    old_no = new_no = 0
    sections = 0
    for raw in raw_lines:
        if len(fd.lines) >= MAX_DIFF_PARSE_LINES:
            truncated = True
            break
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if raw.startswith((b"diff --git ", b"diff --cc ", b"diff --combined ")):
            in_hunk = False
            sections += 1
            if sections > 1:
                fd.lines.append(DiffLine("note", None, None,
                                         decode_line(raw.split(b" ", 2)[-1], encoding)))
            continue
        if not in_hunk and not raw.startswith(b"@@"):
            if raw.startswith((b"Binary files ", b"GIT binary patch")):
                fd.is_binary = True
            elif raw.startswith(b"rename from "):
                fd.old_path = decode_line(raw[12:], encoding)
            elif raw.startswith(b"+++ ") and not raw.startswith(b"+++ /dev/null") and not fd.path:
                p = decode_line(raw[4:], encoding)
                fd.path = p[2:] if p.startswith("b/") else p
            continue
        if raw.startswith(b"@@"):
            text = decode_line(raw, encoding)
            if raw.startswith(b"@@@"):
                combined = True
                m = _CC_HUNK_RE.match(text)
                old_no, new_no = 0, (int(m.group(2)) if m else 0)
                in_hunk = True
            else:
                m = _HUNK_RE.match(text)
                if m:
                    combined = False
                    old_no, new_no = int(m.group(1)), int(m.group(2))
                    in_hunk = True
            fd.lines.append(DiffLine("hunk", None, None, text))
            continue
        if raw.startswith(b"\\"):      # "\ No newline at end of file"
            continue
        if combined:
            prefix = raw[:2].decode("ascii", "replace")
            text = decode_line(raw[2:], encoding)
            if "+" in prefix:
                fd.lines.append(DiffLine("add", None, new_no, text))
                new_no += 1
                fd.added += 1
            elif "-" in prefix:
                fd.lines.append(DiffLine("del", None, None, text))
                fd.deleted += 1
            else:
                fd.lines.append(DiffLine("ctx", None, new_no, text))
                new_no += 1
            continue
        c = raw[:1]
        text = decode_line(raw[1:], encoding)
        if c == b"+":
            fd.lines.append(DiffLine("add", None, new_no, text))
            new_no += 1
            fd.added += 1
        elif c == b"-":
            fd.lines.append(DiffLine("del", old_no, None, text))
            old_no += 1
            fd.deleted += 1
        else:  # 空格（或被编辑器吃掉空格的空行）
            fd.lines.append(DiffLine("ctx", old_no, new_no, text))
            old_no += 1
            new_no += 1
    if truncated:
        fd.truncated = True
        fd.lines.append(DiffLine("note", None, None, TRUNCATED_NOTE))
    return fd


def get_commit_diff(repo: str, oid: str, paths: list, *, ignore_ws: bool = False,
                    ignore_eol: bool = False, context: int = FULL_CONTEXT,
                    encoding: str = ENC_AUTO) -> FileDiff:
    """某次提交对指定文件的 diff（合并提交相对第一个父提交；根提交相对空树）。"""
    args = ["show", "--first-parent", "--format="] + _diff_flags(ignore_ws, ignore_eol, context)
    args += [oid, "--"] + list(paths)
    fd = parse_diff(run_git(repo, args), encoding)
    if not fd.path:
        fd.path = paths[-1]
    return fd


def _untracked_diff(repo: str, rel: str, encoding: str) -> FileDiff:
    fd = FileDiff(path=rel)
    full = os.path.join(repo, rel)
    try:
        if os.path.isdir(full):
            fd.message = "这是一个目录（可能是嵌套的 Git 仓库）。"
            return fd
        if os.path.getsize(full) > MAX_UNTRACKED_BYTES:
            fd.message = "文件过大，不显示内容。"
            return fd
        with open(full, "rb") as f:
            data = f.read()
    except OSError as e:
        fd.message = f"无法读取文件：{e}"
        return fd
    if b"\0" in data[:8000]:
        fd.is_binary = True
        return fd
    raw_lines = data.split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    if len(raw_lines) > MAX_DIFF_PARSE_LINES:
        del raw_lines[MAX_DIFF_PARSE_LINES:]
        fd.truncated = True
    fd.lines.append(DiffLine("hunk", None, None, f"@@ 新文件（未跟踪）  +1,{len(raw_lines)} @@"))
    for n, raw in enumerate(raw_lines, 1):
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        fd.lines.append(DiffLine("add", None, n, decode_line(raw, encoding)))
    fd.added = len(raw_lines)
    if fd.truncated:
        fd.lines.append(DiffLine("note", None, None, TRUNCATED_NOTE))
    return fd


def get_worktree_diff(repo: str, change: FileChange, has_head: bool, *,
                      ignore_ws: bool = False, ignore_eol: bool = False,
                      context: int = FULL_CONTEXT, encoding: str = ENC_AUTO) -> FileDiff:
    """工作区文件相对最新提交的 diff（暂存区和工作区的改动合并显示）。"""
    if change.kind == "U":
        return _untracked_diff(repo, change.path, encoding)
    paths = [change.orig_path, change.path] if change.orig_path else [change.path]
    args = ["diff"] + _diff_flags(ignore_ws, ignore_eol, context)
    args.append("HEAD" if has_head else "--cached")
    args += ["--"] + paths
    fd = parse_diff(run_git(repo, args), encoding)
    if not fd.path:
        fd.path = change.path
    return fd


# --------------------------------------------------------------------------
# 文件历史
# --------------------------------------------------------------------------
_C_ESCAPES = {ord("a"): 7, ord("b"): 8, ord("t"): 9, ord("n"): 10, ord("v"): 11,
              ord("f"): 12, ord("r"): 13, ord('"'): 34, ord("\\"): 92}


def _unquote_path(raw: bytes) -> bytes:
    """还原 git 给“特殊”文件名加的 C 风格引号（"a\\tb" / "\\346\\226\\207"）。没加引号的原样返回。"""
    if len(raw) < 2 or not (raw.startswith(b'"') and raw.endswith(b'"')):
        return raw
    s, out, i = raw[1:-1], bytearray(), 0
    while i < len(s):
        c = s[i]
        if c == 92 and i + 1 < len(s):                      # 反斜杠转义
            n = s[i + 1]
            octal = s[i + 1:i + 4]
            if len(octal) == 3 and all(48 <= d <= 55 for d in octal):     # \ooo：一个字节
                out.append(int(octal, 8) & 0xFF)
                i += 4
                continue
            out.append(_C_ESCAPES.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return bytes(out)


def get_file_history(repo: str, path: str, limit: int = 2000) -> list:
    """某个文件的提交历史（当前分支，追踪重命名；已删除的文件也能查）。"""
    out = run_git(repo, ["log", "--follow", "--decorate=full", "--no-color", "-M",
                         "--name-status", f"--format=%x1e{_FIELDS}",
                         f"--max-count={limit}", "HEAD", "--", path])
    records = []   # [commit, status, old, new]
    cur = None
    for raw in out.split(b"\n"):
        if raw.startswith(b"\x1e"):
            c = _commit_from_fields(raw[1:].split(b"\x1f"))
            cur = [c, "", None, None] if c else None
            if cur:
                records.append(cur)
        elif cur is not None and raw.strip():
            parts = [decode_line(_unquote_path(p)) for p in raw.rstrip(b"\r").split(b"\t")]
            if len(parts) >= 2 and cur[3] is None:
                cur[1] = parts[0][:1]
                if parts[0][:1] in "RC" and len(parts) >= 3:
                    cur[2], cur[3] = parts[1], parts[2]
                else:
                    cur[3] = parts[1]
    entries = []
    cur_path = path
    for c, status, old, new in records:
        p = new or cur_path
        entries.append(HistoryEntry(c, p, old, status or "M"))
        cur_path = old if old else p
    return entries


def list_files(repo: str) -> list:
    out = run_git(repo, ["ls-files", "-z"])
    return [decode_line(t) for t in out.split(b"\0") if t]
