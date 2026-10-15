"""提交图的分支线布局算法（纯 Python，不依赖界面库）。

输入：按拓扑顺序（子提交在前）排列的提交序列。
输出：每个提交一行 GraphRow，描述这一行里节点所在的列，以及
上半格 / 下半格各有哪些线（起点列、终点列、颜色编号）。
界面只需要照着画就行。

可以分页增量使用：同一个 GraphLayout 连续 add() 即可，状态会保留。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GraphRow:
    col: int                 # 节点所在列
    color: int               # 节点颜色编号
    top: list = field(default_factory=list)      # [(从列, 到列, 颜色)]：上半格（从行顶到节点所在高度）
    bottom: list = field(default_factory=list)   # [(从列, 到列, 颜色)]：下半格（从节点高度到行底）
    width: int = 1           # 这一行用到的列数


class GraphLayout:
    def __init__(self) -> None:
        self.lanes: list = []      # 每一列：(正在等待的提交 id, 颜色) 或 None
        self._next_color = 0

    def _new_color(self) -> int:
        c = self._next_color
        self._next_color += 1
        return c

    def _free_slot(self) -> int:
        for i, lane in enumerate(self.lanes):
            if lane is None:
                return i
        self.lanes.append(None)
        return len(self.lanes) - 1

    def add(self, oid: str, parents: list) -> GraphRow:
        before = list(self.lanes)
        matches = [i for i, lane in enumerate(self.lanes) if lane is not None and lane[0] == oid]
        if matches:
            col = matches[0]
            color = self.lanes[col][1]
        else:                       # 没有任何子提交在等它：一个新的分支末端
            col = self._free_slot()
            color = self._new_color()

        # 上半格：原来的每条线要么直通，要么汇入本提交的节点
        top = []
        for i, lane in enumerate(before):
            if lane is None:
                continue
            top.append((i, col if lane[0] == oid else i, lane[1]))

        for i in matches:           # 汇入本提交的线到此为止
            self.lanes[i] = None

        # 下半格：没被本提交占用的线继续直通
        bottom = [(i, i, lane[1]) for i, lane in enumerate(self.lanes) if lane is not None]

        if parents:
            self.lanes[col] = (parents[0], color)     # 第一父提交沿用同一条线
            bottom.append((col, col, color))
            for p in parents[1:]:                      # 其余父提交（合并）
                existing = next((i for i, lane in enumerate(self.lanes)
                                 if lane is not None and lane[0] == p), None)
                if existing is None:
                    k = self._free_slot()
                    c2 = self._new_color()
                    self.lanes[k] = (p, c2)
                    bottom.append((col, k, c2))
                else:
                    bottom.append((col, existing, self.lanes[existing][1]))

        while self.lanes and self.lanes[-1] is None:
            self.lanes.pop()
        width = max(len(before), len(self.lanes), col + 1)
        return GraphRow(col, color, top, bottom, width)
