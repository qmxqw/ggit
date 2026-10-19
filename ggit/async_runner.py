"""把耗时的 git 调用放到后台线程，结果回到界面线程的回调里。支持主动取消任务并终止子进程。"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class GitCancelledError(Exception):
    """任务被主动取消。"""


_thread_local = threading.local()


def get_current_cancel_token() -> Optional[CancellationToken]:
    return getattr(_thread_local, "token", None)


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


class _Signals(QObject):
    finished = Signal(int, object, object)     # 编号, 结果, 异常


class _Job(QRunnable):
    def __init__(self, token_id: int, cancel_token: CancellationToken, fn: Callable, signals: _Signals):
        super().__init__()
        self.token_id = token_id
        self.cancel_token = cancel_token
        self.fn = fn
        self.signals = signals

    def run(self):
        if self.cancel_token.cancelled:
            return
        _thread_local.token = self.cancel_token
        try:
            try:
                res, err = self.fn(), None
            except GitCancelledError as e:
                res, err = None, e
            except BaseException as e:             # noqa: BLE001 —— 任何错误都要回传给界面
                res, err = None, e
            if not self.cancel_token.cancelled:
                try:
                    self.signals.finished.emit(self.token_id, res, err)
                except RuntimeError:
                    pass
        finally:
            _thread_local.token = None


class AsyncRunner(QObject):
    """submit(fn, on_ok, on_err)：fn 在后台线程执行；on_ok / on_err 在界面线程调用。支持 cancel(token)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.finished.connect(self._on_finished)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(6)
        self._next = 0
        self._callbacks: dict[int, tuple] = {}
        self._tokens: dict[int, CancellationToken] = {}

    def submit(self, fn: Callable, on_ok=None, on_err=None) -> int:
        self._next += 1
        token_id = self._next
        cancel_token = CancellationToken()
        self._callbacks[token_id] = (on_ok, on_err)
        self._tokens[token_id] = cancel_token
        self._pool.start(_Job(token_id, cancel_token, fn, self._signals))
        return token_id

    def cancel(self, token_id: Optional[int]) -> None:
        if token_id is None:
            return
        self._callbacks.pop(token_id, None)
        token = self._tokens.pop(token_id, None)
        if token:
            token.cancel()

    @Slot(int, object, object)
    def _on_finished(self, token_id: int, res, err):
        self._tokens.pop(token_id, None)
        on_ok, on_err = self._callbacks.pop(token_id, (None, None))
        if err is not None and isinstance(err, GitCancelledError):
            return
        try:
            if err is not None:
                if on_err:
                    on_err(err)
            elif on_ok:
                on_ok(res)
        except RuntimeError as e:
            if "already deleted" not in str(e):
                raise   # 真正的程序错误：交给全局异常处理（记日志 + 提示），不要悄悄吞掉
            # 否则：回调所属的界面对象已被销毁（比如项目被移除），忽略

    def shutdown(self):
        for token in list(self._tokens.values()):
            token.cancel()
        self._tokens.clear()
        self._callbacks.clear()
        self._pool.waitForDone(1000)
