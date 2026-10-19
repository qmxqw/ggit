"""diff 的二次加工（纯 Python，不依赖界面库）。

gitcore 总是取“整个文件”的 diff（带全部上下文）。这里把它加工成两种显示：
  * trim_context：上下合并模式——只留改动处和前后各 n 行，并生成 @@ 标题行
  * build_split ：左右对照模式——旧内容在左、新内容在右，改动块两侧行数对齐（短的一侧补空行）
change_groups 把“哪些行有改动”归并成一段一段，供滚动条上的红/绿色块和“下一处改动”使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .gitcore import DiffLine


def trim_context(lines: list, n: int = 3) -> list:
    """只保留改动行及其前后各 n 行；每一段前面加 @@ 标题行。没有改动则返回空列表。

    note 行（分段标题、截断说明）原样保留在原来的位置。
    """
    out: list = []
    chunk: list = []
    for l in lines:
        if l.kind == "note":
            out.extend(_trim_chunk(chunk, n))
            chunk = []
            out.append(l)
        elif l.kind != "hunk":
            chunk.append(l)
    out.extend(_trim_chunk(chunk, n))
    return out if any(l.kind in ("add", "del") for l in out) else []


def _trim_chunk(body: list, n: int) -> list:
    keep = [False] * len(body)
    changed = False
    for i, l in enumerate(body):
        if l.kind in ("add", "del"):
            changed = True
            for j in range(max(0, i - n), min(len(body), i + n + 1)):
                keep[j] = True
    if not changed:
        return []
    out: list = []
    i = 0
    while i < len(body):
        if not keep[i]:
            i += 1
            continue
        j = i
        while j < len(body) and keep[j]:
            j += 1
        seg = body[i:j]
        old_start = next((l.old_no for l in seg if l.old_no is not None), 0)
        new_start = next((l.new_no for l in seg if l.new_no is not None), 0)
        old_cnt = sum(1 for l in seg if l.kind != "add")
        new_cnt = sum(1 for l in seg if l.kind != "del")
        out.append(DiffLine("hunk", None, None, f"@@ -{old_start},{old_cnt} +{new_start},{new_cnt} @@"))
        out.extend(seg)
        i = j
    return out


@dataclass
class SplitRow:
    left: Optional[DiffLine]     # None：这一侧是补出来的空行
    right: Optional[DiffLine]


def build_split(lines: list) -> list:
    """左右对照。连续的“删除 + 新增”算一个改动块，逐行配对，短的一侧补空行，保证两侧行数一致。
    hunk 标题行丢弃；note 行（分段标题、截断说明）两侧都保留。"""
    rows: list = []
    dels: list = []
    adds: list = []

    def flush():
        for k in range(max(len(dels), len(adds))):
            rows.append(SplitRow(dels[k] if k < len(dels) else None,
                                 adds[k] if k < len(adds) else None))
        dels.clear()
        adds.clear()

    for l in lines:
        if l.kind == "hunk":
            continue
        if l.kind == "del":
            dels.append(l)
        elif l.kind == "add":
            adds.append(l)
        else:                       # ctx，或 note（两侧都显示这句提示）
            flush()
            rows.append(SplitRow(l, l))
    flush()
    return rows


def change_groups(flags: list) -> list:
    """flags[i] = (这一行有删除, 这一行有新增)。返回 [(起始行, 结束行, 含删除, 含新增)]（连续有改动的行算一段）。"""
    groups: list = []
    start = None
    d = a = False
    for i, (has_del, has_add) in enumerate(flags):
        if has_del or has_add:
            if start is None:
                start, d, a = i, False, False
            d = d or has_del
            a = a or has_add
        elif start is not None:
            groups.append((start, i - 1, d, a))
            start = None
    if start is not None:
        groups.append((start, len(flags) - 1, d, a))
    return groups
