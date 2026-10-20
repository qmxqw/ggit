"""后台任务的取消机制（纯 Python，不依赖界面库）。

AsyncRunner 在执行任务的线程里登记一个 CancellationToken；gitcore.run_git 从这里取出它，
把正在运行的 git 子进程交给它，这样界面一取消就能直接结束子进程。
"""
from __future__ import annotations

import contextlib
import subprocess
import threading


class GitCancelledError(Exception):
    """任务被主动取消。"""


class CancellationToken:
    def __init__(self) -> None:
        self.cancelled = False
        self.proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def set_proc(self, proc: subprocess.Popen[bytes] | None) -> None:
        with self._lock:
            self.proc = proc
            if self.cancelled and proc and proc.poll() is None:
                with contextlib.suppress(OSError):          # 进程刚好自己结束了
                    proc.kill()

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            if self.proc and self.proc.poll() is None:
                with contextlib.suppress(OSError):
                    self.proc.kill()


_thread_local = threading.local()


def get_current_cancel_token() -> CancellationToken | None:
    return getattr(_thread_local, "token", None)


def set_current_cancel_token(token: CancellationToken | None) -> None:
    _thread_local.token = token
