"""把耗时的 git 调用放到后台线程，结果回到界面线程的回调里。支持主动取消任务并终止子进程。"""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .cancel import CancellationToken, GitCancelledError, set_current_cancel_token

__all__ = ["AsyncRunner", "CancellationToken", "GitCancelledError"]

OnOk = Callable[[Any], None]
OnErr = Callable[[BaseException], None]


class _Signals(QObject):
    finished = Signal(int, object, object)     # 编号, 结果, 异常


class _Job(QRunnable):
    def __init__(self, token_id: int, cancel_token: CancellationToken, fn: Callable[[], Any],
                 signals: _Signals) -> None:
        super().__init__()
        self.token_id = token_id
        self.cancel_token = cancel_token
        self.fn = fn
        self.signals = signals

    def run(self) -> None:
        if self.cancel_token.cancelled:
            return
        set_current_cancel_token(self.cancel_token)
        res: Any = None
        err: BaseException | None = None
        try:
            try:
                res = self.fn()
            except BaseException as e:             # noqa: BLE001 —— 任何错误都要回传给界面（取消的不回传）
                err = e
            if not self.cancel_token.cancelled:
                with contextlib.suppress(RuntimeError):     # 程序正在退出，信号对象已经销毁
                    self.signals.finished.emit(self.token_id, res, err)
        finally:
            set_current_cancel_token(None)


class AsyncRunner(QObject):
    """submit(fn, on_ok, on_err)：fn 在后台线程执行；on_ok / on_err 在界面线程调用。支持 cancel(token)。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.finished.connect(self._on_finished)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(6)
        self._next = 0
        self._callbacks: dict[int, tuple[OnOk | None, OnErr | None]] = {}
        self._tokens: dict[int, CancellationToken] = {}

    def submit(self, fn: Callable[[], Any], on_ok: OnOk | None = None, on_err: OnErr | None = None) -> int:
        """在后台执行 fn()；返回任务编号（用于 cancel）。"""
        self._next += 1
        token_id = self._next
        cancel_token = CancellationToken()
        self._callbacks[token_id] = (on_ok, on_err)
        self._tokens[token_id] = cancel_token
        self._pool.start(_Job(token_id, cancel_token, fn, self._signals))
        return token_id

    def cancel(self, token_id: int | None) -> None:
        """取消任务：还没开始的不再执行，正在跑的 git 子进程被结束；回调都不会再被调用。"""
        if token_id is None:
            return
        self._callbacks.pop(token_id, None)
        token = self._tokens.pop(token_id, None)
        if token:
            token.cancel()

    @Slot(int, object, object)
    def _on_finished(self, token_id: int, res: Any, err: BaseException | None) -> None:
        self._tokens.pop(token_id, None)
        on_ok, on_err = self._callbacks.pop(token_id, (None, None))
        if isinstance(err, GitCancelledError):
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

    def shutdown(self) -> None:
        for token in list(self._tokens.values()):
            token.cancel()
        self._tokens.clear()
        self._callbacks.clear()
        self._pool.waitForDone(1000)
