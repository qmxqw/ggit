"""测试共用的工具：Qt 一律用 offscreen 平台、临时配置文件、临时 Git 仓库、等待后台任务。

导入本模块不会加载 PySide6（核心层的测试不需要界面库）；需要 Qt 时调用 qapp()。
所有测试模块都应在导入 ggit 之前先 `import support`。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

# 界面测试一律在 offscreen 平台上跑：不在真实桌面上弹窗口、托盘图标和系统通知
os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

GIT_ENV = dict(os.environ,
               GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="t@example.com",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


# ---------------------------------------------------------------- Git 仓库
def git(repo: str, *args: str) -> str:
    """在 repo 里运行 git，返回标准输出（去掉首尾空白）。"""
    out = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, check=True, capture_output=True)
    return out.stdout.decode("utf-8", "replace").strip()


def write(repo: str, name: str, data: str | bytes) -> None:
    path = os.path.join(repo, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as f:
        f.write(data)


def commit(repo: str, msg: str) -> str:
    """提交全部改动，返回新提交的 id。"""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def init_repo(path: str) -> str:
    """在 path 建一个空仓库（main 分支）。"""
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    return path


def clone_pair(tmp: TempDir) -> tuple[str, str, str]:
    """一个裸仓库当远端（main 分支上有一个提交），再克隆出两份：返回 (远端, 克隆 a, 克隆 b)。"""
    remote = tmp.sub("remote.git")
    git(tmp.path, "init", "-q", "--bare", "-b", "main", remote)
    seed = init_repo(tmp.sub("seed"))
    write(seed, "a.txt", "base\n")
    commit(seed, "base")
    git(seed, "push", "-q", remote, "main")
    a, b = tmp.sub("a"), tmp.sub("b")
    git(tmp.path, "clone", "-q", remote, a)
    git(tmp.path, "clone", "-q", remote, b)
    return remote, a, b


class GitEnv:
    """让 ggit 自己启动的 git（写操作）也用测试的身份，并且不读用户的全局 / 系统 git 配置。用完调用 restore()。"""
    KEYS = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
            "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")

    def __init__(self) -> None:
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ[k] = GIT_ENV[k]

    def restore(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TempDir:
    """临时目录；用完调用 cleanup()（Windows 上 git 偶尔还占着文件，删不掉就算了）。"""

    def __init__(self) -> None:
        self.path = tempfile.mkdtemp(prefix="ggit-test-")

    def sub(self, *parts: str) -> str:
        return os.path.join(self.path, *parts)

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


# ---------------------------------------------------------------- 配置文件
class TempConfig:
    """把 ggit 的配置文件换成临时目录里的，绝不读写用户真实的 %APPDATA%\\ggit\\config.json。"""

    def __init__(self) -> None:
        from ggit import config
        self._config = config
        self._orig = config.config_path
        self.dir = TempDir()
        path = self.dir.sub("config.json")
        config.config_path = lambda: path

    def restore(self) -> None:
        self._config.config_path = self._orig
        self.dir.cleanup()


# ---------------------------------------------------------------- Qt
def qapp() -> QApplication:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


class QtTestCase(unittest.TestCase):
    """界面测试的基类：保证有一个 QApplication（self.app）。"""
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app = qapp()


def wait_until(cond: Callable[[], bool], timeout: float = 15.0) -> None:
    """一边处理 Qt 事件一边等，直到 cond() 为真（用来等后台 git 任务的结果回到界面线程）。"""
    app = qapp()
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("等待超时")
        app.processEvents()
        time.sleep(0.01)
