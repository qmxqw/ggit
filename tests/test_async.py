"""AsyncRunner 任务调度与取消机制的单元测试。"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication
from ggit.async_runner import AsyncRunner, CancellationToken, GitCancelledError


class AsyncRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_cancel_token(self):
        token = CancellationToken()
        self.assertFalse(token.cancelled)
        token.cancel()
        self.assertTrue(token.cancelled)

    def test_submit_and_cancel_before_run(self):
        runner = AsyncRunner()
        results = []

        def work():
            time.sleep(0.05)
            return "ok"

        tok = runner.submit(work, on_ok=lambda r: results.append(r))
        runner.cancel(tok)
        runner.shutdown()
        # 结果应被丢弃，不回调
        self.assertEqual(results, [])

    def test_submit_and_success(self):
        runner = AsyncRunner()
        results = []

        def work():
            return 42

        runner.submit(work, on_ok=lambda r: results.append(r))
        # 处理 Qt 事件循环让跨线程信号送达
        for _ in range(50):
            self.app.processEvents()
            if results:
                break
            time.sleep(0.02)
        runner.shutdown()
        self.assertEqual(results, [42])


if __name__ == "__main__":
    unittest.main()
