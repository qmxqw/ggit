"""AsyncRunner：后台执行、取消、回调里的异常处理。"""
import sys
import time
import unittest

import support

from ggit.async_runner import AsyncRunner
from ggit.cancel import CancellationToken


class AsyncRunnerTest(support.QtTestCase):
    def setUp(self):
        self.runner = AsyncRunner()
        self.addCleanup(self.runner.shutdown)

    def test_cancel_token(self):
        token = CancellationToken()
        self.assertFalse(token.cancelled)
        token.cancel()
        self.assertTrue(token.cancelled)

    def test_cancelled_job_has_no_callback(self):
        results: list[object] = []

        def work():
            time.sleep(0.05)
            return "ok"

        tok = self.runner.submit(work, on_ok=results.append)
        self.runner.cancel(tok)
        self.runner.shutdown()          # 等后台线程跑完
        self.app.processEvents()
        self.assertEqual(results, [])

    def test_result_reaches_ui_thread(self):
        results: list[object] = []
        self.runner.submit(lambda: 42, on_ok=results.append)
        support.wait_until(lambda: results == [42])

    def test_error_goes_to_on_err(self):
        errors: list[BaseException] = []

        def fail():
            raise ValueError("boom")

        self.runner.submit(fail, on_ok=lambda _r: None, on_err=errors.append)
        support.wait_until(lambda: len(errors) == 1)
        self.assertIsInstance(errors[0], ValueError)

    def test_callback_errors(self):
        """回调所属的界面对象已销毁：忽略；其它异常是真正的程序错误，交给全局异常处理。"""
        seen: list[str] = []
        old_hook = sys.excepthook
        sys.excepthook = lambda _t, v, _tb: seen.append(str(v))
        self.addCleanup(setattr, sys, "excepthook", old_hook)
        called: list[str] = []

        def deleted(_r):
            called.append("deleted")
            raise RuntimeError("Internal C++ object (QLabel) already deleted.")

        def bug(_r):
            called.append("bug")
            raise RuntimeError("real bug")

        self.runner.submit(lambda: 1, deleted)
        self.runner.submit(lambda: 2, bug)
        support.wait_until(lambda: len(called) == 2)
        self.assertEqual(seen, ["real bug"])


if __name__ == "__main__":
    unittest.main()
