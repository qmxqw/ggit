"""内容区上方的“文档标签页”：同时打开多个文档（diff 或文件预览），用标签切换 / 关闭。

  * 点击一个文件 = 新开一个标签；已经打开的文件再点只会切换过去，旧的标签不会被关掉
  * 关闭：标签上的 ×、鼠标中键、Ctrl+W；右键标签有“关闭其他 / 关闭全部”；标签可以拖动换顺序
每个项目、每种工作模式（git / file）都有自己的一套标签页，切换项目或模式时各自保留。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QStackedWidget, QTabBar,
                               QVBoxLayout, QWidget)


@dataclass(eq=False)
class Doc:
    key: str
    title: str
    tip: str
    kind: str                      # commit / work / file
    fn: Optional[Callable]         # fn(options) -> 结果对象，在后台线程里调用
    info: str                      # 显示在内容区上方的说明（HTML，只有 git 模式用）
    view: QWidget                  # DiffDocument 或 FileDocument
    data: object = None
    token: int = 0                 # 每次加载 +1，用来丢弃过期的结果
    sig: object = None             # 上次显示内容的签名：自动刷新时内容没变就不重绘
    task_id: Optional[int] = None  # 正在运行的后台任务 ID


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

    def __init__(self, runner, options, view_factory: Callable, empty_text: str, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.options = options
        self._view_factory = view_factory
        self._docs: dict = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bar_frame = QFrame()
        self.bar_frame.setObjectName("docBar")
        self.bar_frame.setStyleSheet("QFrame#docBar{background:#2d2d30;}")
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
        self.empty.setStyleSheet("color:#8a8a8a; font-size:13px;")
        self.stack.addWidget(self.empty)
        lay.addWidget(self.stack, 1)

        self.tabbar.currentChanged.connect(self._on_current)
        self.tabbar.tabCloseRequested.connect(self.close_index)
        self.tabbar.middleClicked.connect(self.close_index)
        self.tabbar.customContextMenuRequested.connect(self._menu)

    # ------------------------------------------------------------ 查询
    def _key_at(self, i: int) -> Optional[str]:
        return self.tabbar.tabData(i) if 0 <= i < self.tabbar.count() else None

    def _index_of(self, key: str) -> int:
        for i in range(self.tabbar.count()):
            if self.tabbar.tabData(i) == key:
                return i
        return -1

    def doc_at(self, i: int) -> Optional[Doc]:
        return self._docs.get(self._key_at(i))

    def current_doc(self) -> Optional[Doc]:
        return self.doc_at(self.tabbar.currentIndex())

    def get(self, key: str) -> Optional[Doc]:
        return self._docs.get(key)

    def docs_of_kind(self, kind: str) -> list:
        return [d for d in self._docs.values() if d.kind == kind]

    def count(self) -> int:
        return len(self._docs)

    # ------------------------------------------------------------ 打开
    def open_doc(self, key: str, title: str, tip: str, kind: str, fn, info: str, data=None) -> Doc:
        """打开一个文档：已经开着就切过去，没开就新开一个标签。从不关闭别的标签。"""
        doc = self._docs.get(key)
        if doc is not None:
            if fn is not None:
                doc.fn = fn
            doc.info = info
            if kind in ("work", "file") or doc.sig is None:     # 磁盘上的内容会变；没加载成功的也重试
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

    def _on_loaded(self, doc: Doc, tok: int, result, quiet: bool) -> None:
        if not self._alive(doc, tok):
            return
        sig = doc.view.result_signature(result)
        if quiet and sig == doc.sig:
            return
        doc.sig = sig
        doc.view.show_result(result, keep_scroll=quiet)

    def _on_error(self, doc: Doc, tok: int, err) -> None:
        if self._alive(doc, tok):
            doc.view.set_message(f"出错了：{err}")

    def reload_all(self) -> None:
        """显示选项（忽略空白、编码…）变化，或需要刷新时：重新读取所有已打开的文档。"""
        for doc in list(self._docs.values()):
            self.load(doc, quiet=True)

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
