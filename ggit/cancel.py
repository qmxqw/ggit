"""后台任务的取消机制（纯 Python，不依赖界面库）。

AsyncRunner 在执行任务的线程里登记一个 CancellationToken；gitcore.run_git 从这里取出它，
把正在运行的 git 子进程交给它，这样界面一取消就能直接结束子进程。
"""
from __future__ import annotations

import threading
from typing import Optional


class GitCancelledError(Exception):
    """任务被主动取消。"""


class CancellationToken:
    def __init__(self):
        self.cancelled = False
        self.proc = None
        self._lock = threading.Lock()

    def set_proc(self, proc):
        with self._lock:
            self.proc = proc
            if self.cancelled and proc and proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass

    def cancel(self):
        with self._lock:
            self.cancelled = True
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.kill()
                except OSError:
                    pass


_thread_local = threading.local()


def get_current_cancel_token() -> Optional[CancellationToken]:
    return getattr(_thread_local, "token", None)


def set_current_cancel_token(token: Optional[CancellationToken]) -> None:
    _thread_local.token = token
