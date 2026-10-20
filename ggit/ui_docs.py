"""内容区上方的“文档标签页”：同时打开多个文档（diff 或文件预览），用标签切换 / 关闭。

  * 点击一个文件 = 新开一个标签；已经打开的文件再点只会切换过去，旧的标签不会被关掉
  * 关闭：标签上的 ×、鼠标中键、Ctrl+W；右键标签有“关闭其他 / 关闭全部”；标签可以拖动换顺序
每个项目、每种工作模式（git / file）都有自己的一套标签页，切换项目或模式时各自保留。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QStackedWidget, QTabBar, QVBoxLayout, QWidget

from .async_runner import AsyncRunner
from .options import ViewOptions


class DocView(QWidget):
    """一个标签页里显示的内容（DiffDocument、FileDocument）都要实现的接口。"""
    modeToggleRequested = Signal()      # diff 视图里“切换模式”按钮被点了（文件预览不会发出）

    def set_title(self, text: str) -> None:
        raise NotImplementedError

    def set_message(self, text: str) -> None:
        """不显示内容，只在中间显示一句话（加载中、出错了…）。"""
        raise NotImplementedError

    def show_result(self, result: Any, keep_scroll: bool = False) -> None:
        """显示后台读到的结果（FileDiff / FileContent）。"""
        raise NotImplementedError

    @staticmethod
    def result_signature(result: Any) -> object:
        """结果的签名：自动刷新时签名没变就不重绘。"""
        raise NotImplementedError

    def apply_font_size(self, size: int) -> None:
        raise NotImplementedError

    def set_mode(self, mode: str) -> None:
        """切换 diff 模式（上下合并 / 左右对照）；没有模式的视图什么也不做。"""

    def goto_change(self, forward: bool = True) -> None:
        """跳到下一处 / 上一处改动；没有改动概念的视图什么也不做。"""


@dataclass(eq=False)
class Doc:
    key: str
    title: str
    tip: str
    kind: str                      # commit / work / file
    fn: Callable[[ViewOptions], Any] | None     # fn(options) -> 结果对象，在后台线程里调用
    info: str                      # 显示在内容区上方的说明（HTML，只有 git 模式用）
    view: DocView                  # DiffDocument 或 FileDocument
    data: object = None
    token: int = 0                 # 每次加载 +1，用来丢弃过期的结果
    sig: object = None             # 上次显示内容的签名：自动刷新时内容没变就不重绘
    task_id: int | None = None  # 正在运行的后台任务 ID（读完后清空）
    loaded_at: float = 0.0         # 最近一次读取完成的时间（time.monotonic）


RELOAD_GAP = 1.0                   # 再次打开同一个文档时，距上次读取不到这么久就不重读（比如双击）


class DocTabBar(QTabBar):
    middleClicked = Signal(int)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            i = self.tabAt(event.position().toPoint())
            if i >= 0:
                self.middleClicked.emit(i)
                return
        super().mouseReleaseEvent(event)


class DocArea(QWidget):
    infoChanged = Signal(str)          # 当前文档变了：把它的说明交给上层显示
    modeToggleRequested = Signal()

    def __init__(self, runner: AsyncRunner, options: ViewOptions, view_factory: Callable[[], DocView],
                 empty_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.options = options
        self._view_factory = view_factory
        self._docs: dict[str, Doc] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bar_frame = QFrame()
        self.bar_frame.setObjectName("docBar")
        bl = QHBoxLayout(self.bar_frame)
        bl.setContentsMargins(4, 4, 4, 0)
        self.tabbar = DocTabBar()
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setDrawBase(False)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tabbar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabbar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bl.addWidget(self.tabbar)
        bl.addStretch(1)
        lay.addWidget(self.bar_frame)
        self.bar_frame.setVisible(False)

        self.stack = QStackedWidget()
        self.empty = QLabel(empty_text)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("placeholder")
        self.stack.addWidget(self.empty)
        lay.addWidget(self.stack, 1)

        self.tabbar.currentChanged.connect(self._on_current)
        self.tabbar.tabCloseRequested.connect(self.close_index)
        self.tabbar.middleClicked.connect(self.close_index)
        self.tabbar.customContextMenuRequested.connect(self._menu)

    # ------------------------------------------------------------ 查询
    def _key_at(self, i: int) -> str | None:
        return self.tabbar.tabData(i) if 0 <= i < self.tabbar.count() else None

    def _index_of(self, key: str) -> int:
        for i in range(self.tabbar.count()):
            if self.tabbar.tabData(i) == key:
                return i
        return -1

    def doc_at(self, i: int) -> Doc | None:
        key = self._key_at(i)
        return self._docs.get(key) if key is not None else None

    def current_doc(self) -> Doc | None:
        return self.doc_at(self.tabbar.currentIndex())

    def get(self, key: str) -> Doc | None:
        return self._docs.get(key)

    def all_docs(self) -> list[Doc]:
        return list(self._docs.values())

    def docs_of_kind(self, kind: str) -> list[Doc]:
        return [d for d in self._docs.values() if d.kind == kind]

    def count(self) -> int:
        return len(self._docs)

    # ------------------------------------------------------------ 打开
    def open_doc(self, key: str, title: str, tip: str, kind: str, fn: Callable[[ViewOptions], Any] | None,
                 info: str, data: object = None) -> Doc:
        """打开一个文档：已经开着就切过去，没开就新开一个标签。从不关闭别的标签。"""
        doc = self._docs.get(key)
        if doc is not None:
            if fn is not None:
                doc.fn = fn
            doc.info = info
            if self._needs_reload(doc):
                self.load(doc, quiet=True)
            i = self._index_of(key)
            self.tabbar.setCurrentIndex(i)
            self._on_current(i)
            return doc

        view = self._view_factory()
        view.modeToggleRequested.connect(self.modeToggleRequested)
        doc = Doc(key, title, tip, kind, fn, info, view, data)
        view.set_title(tip)
        self.stack.addWidget(view)
        i = self.tabbar.addTab(title)
        self.tabbar.setTabData(i, key)
        self.tabbar.setTabToolTip(i, tip)
        self._docs[key] = doc
        self.bar_frame.setVisible(True)
        self.tabbar.setCurrentIndex(i)
        self._on_current(i)
        self.load(doc, quiet=False)
        return doc

    # ------------------------------------------------------------ 加载
    @staticmethod
    def _needs_reload(doc: Doc) -> bool:
        """已经打开的文档被再次打开时要不要重读。

        正在读、或刚读完（双击会连着触发两次打开）：不重读；
        没读成功过的：重读；工作区 / 磁盘文件：内容可能变了，重读；提交里的文件：永远不变，不重读。
        """
        if doc.task_id is not None or time.monotonic() - doc.loaded_at < RELOAD_GAP:
            return False
        return doc.sig is None or doc.kind in ("work", "file")

    def load(self, doc: Doc, quiet: bool) -> None:
        """在后台读取内容并显示。quiet=True（自动刷新）：不显示“加载中”，内容没变就不重绘，保持滚动位置。"""
        if doc.fn is None:
            return
        if doc.task_id is not None:
            self.runner.cancel(doc.task_id)
            doc.task_id = None
        doc.token += 1
        tok, fn, opts = doc.token, doc.fn, self.options.copy()
        if not quiet:
            doc.sig = None
            doc.view.set_message("加载中…")
        doc.task_id = self.runner.submit(
            lambda: fn(opts),
            lambda res: self._on_loaded(doc, tok, res, quiet),
            lambda e: self._on_error(doc, tok, e),
        )

    def _alive(self, doc: Doc, tok: int) -> bool:
        return self._docs.get(doc.key) is doc and doc.token == tok

    def _on_loaded(self, doc: Doc, tok: int, result: Any, quiet: bool) -> None:
        if not self._alive(doc, tok):
            return
        doc.task_id = None
        doc.loaded_at = time.monotonic()
        sig = doc.view.result_signature(result)
        if quiet and sig == doc.sig:
            return
        doc.sig = sig
        doc.view.show_result(result, keep_scroll=quiet)

    def _on_error(self, doc: Doc, tok: int, err: BaseException) -> None:
        if self._alive(doc, tok):
            doc.task_id = None
            doc.sig = None                  # 再次打开时重试
            doc.view.set_message(f"出错了：{err}")

    def reload_all(self) -> None:
        """显示选项（忽略空白、编码…）变化，或需要刷新时：重新读取所有已打开的文档。"""
        for doc in list(self._docs.values()):
            self.load(doc, quiet=True)

    def cancel_all(self) -> None:
        """取消所有文档正在进行的后台读取（项目被移除时调用）。"""
        for doc in self._docs.values():
            if doc.task_id is not None:
                self.runner.cancel(doc.task_id)
                doc.task_id = None
            doc.token += 1

    def set_mode(self, mode: str) -> None:
        for doc in self._docs.values():
            doc.view.set_mode(mode)

    # ------------------------------------------------------------ 关闭 / 切换
    def close_index(self, i: int) -> None:
        key = self._key_at(i)
        if key is None:
            return
        doc = self._docs.pop(key, None)
        self.tabbar.removeTab(i)
        if doc is not None:
            if doc.task_id is not None:
                self.runner.cancel(doc.task_id)
                doc.task_id = None
            doc.token += 1
            self.stack.removeWidget(doc.view)
            doc.view.deleteLater()
        if self.tabbar.count() == 0:
            self.bar_frame.setVisible(False)
            self.stack.setCurrentWidget(self.empty)
            self.infoChanged.emit("")

    def close_current(self) -> None:
        if self.tabbar.count():
            self.close_index(self.tabbar.currentIndex())

    def close_others(self, keep: int) -> None:
        keep_key = self._key_at(keep)
        for i in range(self.tabbar.count() - 1, -1, -1):
            if self._key_at(i) != keep_key:
                self.close_index(i)

    def close_all(self) -> None:
        for i in range(self.tabbar.count() - 1, -1, -1):
            self.close_index(i)

    def step(self, delta: int) -> None:
        n = self.tabbar.count()
        if n:
            self.tabbar.setCurrentIndex((self.tabbar.currentIndex() + delta) % n)

    def goto_change(self, forward: bool) -> None:
        doc = self.current_doc()
        if doc is not None:
            doc.view.goto_change(forward)

    def _on_current(self, i: int) -> None:
        doc = self.doc_at(i)
        if doc is None:
            self.stack.setCurrentWidget(self.empty)
            self.infoChanged.emit("")
            return
        self.stack.setCurrentWidget(doc.view)
        self.infoChanged.emit(doc.info)

    def _menu(self, pos) -> None:
        i = self.tabbar.tabAt(pos)
        doc = self.doc_at(i)
        if doc is None:
            return
        menu = QMenu(self)
        a_close = menu.addAction("关闭")
        a_others = menu.addAction("关闭其他")
        a_all = menu.addAction("关闭全部")
        menu.addSeparator()
        a_copy = menu.addAction("复制路径")
        chosen = menu.exec(self.tabbar.mapToGlobal(pos))
        if chosen == a_close:
            self.close_index(i)
        elif chosen == a_others:
            self.close_others(i)
        elif chosen == a_all:
            self.close_all()
        elif chosen == a_copy:
            QGuiApplication.clipboard().setText(doc.tip)
