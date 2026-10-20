"""提交图列表里的条目：提交行 + （展开后）紧跟其后的文件行。纯 Python，不依赖界面库。"""
from __future__ import annotations

from dataclasses import dataclass

from .gitcore import Commit, CommitFile
from .graph import GraphRow


@dataclass(eq=False)
class Row:
    """一个提交。expanded=True 时，它修改的文件（FileRow）紧跟在它后面。"""
    commit: Commit
    graph: GraphRow
    expanded: bool = False


@dataclass(eq=False)
class FileRow:
    parent: Row
    file: CommitFile


class ItemList:
    def __init__(self) -> None:
        self.items: list = []
        self.lanes = 1          # 提交图用到的最大列数

    def __len__(self) -> int:
        return len(self.items)

    def reset(self, rows: list, files_by_oid: dict | None = None) -> None:
        self.items = []
        self.lanes = 1
        self.extend(rows, files_by_oid)

    def extend(self, rows: list, files_by_oid: dict | None = None) -> tuple:
        """追加提交行（files_by_oid 里有的提交直接展开）。返回追加的 (起, 止) 位置；没有追加返回 (0, -1)。"""
        first = len(self.items)
        for r in rows:
            self.lanes = max(self.lanes, r.graph.width)
            self.items.append(r)
            files = files_by_oid.get(r.commit.oid) if files_by_oid else None
            if files is not None:
                r.expanded = True
                self.items.extend(FileRow(r, f) for f in files)
        return (first, len(self.items) - 1) if len(self.items) > first else (0, -1)

    def pos_of(self, oid: str) -> int:
        for i, it in enumerate(self.items):
            if isinstance(it, Row) and it.commit.oid == oid:
                return i
        return -1

    def file_pos(self, oid: str, path: str) -> int:
        i = self.pos_of(oid)
        if i < 0:
            return -1
        j = i + 1
        while j < len(self.items) and isinstance(self.items[j], FileRow) and self.items[j].parent is self.items[i]:
            if self.items[j].file.path == path:
                return j
            j += 1
        return -1

    def expand(self, oid: str, files: list) -> tuple:
        """展开：在提交行后插入文件行。返回插入的 (起, 止) 位置；没有文件返回 (0, -1)。"""
        i = self.pos_of(oid)
        if i < 0 or self.items[i].expanded:
            return (0, -1)
        row = self.items[i]
        row.expanded = True
        new = [FileRow(row, f) for f in files]
        self.items[i + 1:i + 1] = new
        return (i + 1, i + len(new)) if new else (0, -1)

    def collapse(self, oid: str) -> tuple:
        """收起：删掉提交行后面属于它的文件行。返回删除的 (起, 止) 位置；没有删除返回 (0, -1)。"""
        i = self.pos_of(oid)
        if i < 0:
            return (0, -1)
        row = self.items[i]
        row.expanded = False
        j = i + 1
        while j < len(self.items) and isinstance(self.items[j], FileRow) and self.items[j].parent is row:
            j += 1
        if j == i + 1:
            return (0, -1)
        del self.items[i + 1:j]
        return (i + 1, j - 1)
