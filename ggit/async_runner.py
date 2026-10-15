"""把耗时的 git 调用放到后台线程，结果回到界面线程的回调里。"""
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Signals(QObject):
    finished = Signal(int, object, object)     # 编号, 结果, 异常


class _Job(QRunnable):
    def __init__(self, token, fn, signals):
        super().__init__()
        self.token = token
        self.fn = fn
        self.signals = signals

    def run(self):
        try:
            res, err = self.fn(), None
        except BaseException as e:             # noqa: BLE001 —— 任何错误都要回传给界面
            res, err = None, e
        self.signals.finished.emit(self.token, res, err)


class AsyncRunner(QObject):
    """submit(fn, on_ok, on_err)：fn 在后台线程执行；on_ok / on_err 在界面线程调用。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.finished.connect(self._on_finished)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(6)
        self._next = 0
        self._callbacks = {}

    def submit(self, fn, on_ok=None, on_err=None):
        self._next += 1
        token = self._next
        self._callbacks[token] = (on_ok, on_err)
        self._pool.start(_Job(token, fn, self._signals))

    @Slot(int, object, object)
    def _on_finished(self, token, res, err):
        on_ok, on_err = self._callbacks.pop(token, (None, None))
        try:
            if err is not None:
                if on_err:
                    on_err(err)
            elif on_ok:
                on_ok(res)
        except RuntimeError:
            pass        # 回调所属的界面对象已被销毁（比如项目被移除）

    def shutdown(self):
        self._pool.waitForDone(3000)
