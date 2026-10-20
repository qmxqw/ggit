"""后台任务的取消机制（纯 Python，不依赖界面库）。

AsyncRunner 在执行任务的线程里登记一个 CancellationToken；gitcore.run_git 从这里取出它，
把正在运行的 git 子进程交给它，这样界面一取消就能直接结束子进程。
"""
from __future__ import annotations

import contextlib
import subprocess
import sys
import threading

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


class GitCancelledError(Exception):
    """任务被主动取消。"""


class CancellationToken:
    def __init__(self) -> None:
        self.cancelled = False
        self.proc: subprocess.Popen[bytes] | None = None
        self._tree = False
        self._lock = threading.Lock()

    def set_proc(self, proc: subprocess.Popen[bytes] | None, *, tree: bool = False) -> None:
        """tree=True：取消时连同它启动的子进程一起结束（push / pull 会再启动 ssh、git-remote-https）。"""
        with self._lock:
            self.proc = proc
            self._tree = tree
            if self.cancelled and proc:
                self._kill(proc)

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            if self.proc:
                self._kill(self.proc)

    def _kill(self, proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        if self._tree and sys.platform == "win32":
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                               capture_output=True, creationflags=_NO_WINDOW, timeout=10)
        with contextlib.suppress(OSError):          # 进程刚好自己结束了
            proc.kill()


_thread_local = threading.local()


def get_current_cancel_token() -> CancellationToken | None:
    return getattr(_thread_local, "token", None)


def set_current_cancel_token(token: CancellationToken | None) -> None:
    _thread_local.token = token
