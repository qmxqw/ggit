"""diff 的二次加工（纯 Python，不依赖界面库）。

gitcore 总是取“整个文件”的 diff（带全部上下文）。这里把它加工成两种显示：
  * trim_context：上下合并模式——只留改动处和前后各 n 行，并生成 @@ 标题行
  * build_split ：左右对照模式——旧内容在左、新内容在右，改动块两侧行数对齐（短的一侧补空行）
change_groups 把“哪些行有改动”归并成一段一段，供滚动条上的红/绿色块和“下一处改动”使用。
"""
from __future__ import annotations

from dataclasses import dataclass

from .gitcore import DiffLine


def trim_context(lines: list[DiffLine], n: int = 3) -> list[DiffLine]:
    """只保留改动行及其前后各 n 行；每一段前面加 @@ 标题行。没有改动则返回空列表。

    note 行（分段标题、截断说明）原样保留在原来的位置。
    """
    out: list[DiffLine] = []
    chunk: list[DiffLine] = []
    for dl in lines:
        if dl.kind == "note":
            out.extend(_trim_chunk(chunk, n))
            chunk = []
            out.append(dl)
        elif dl.kind != "hunk":
            chunk.append(dl)
    out.extend(_trim_chunk(chunk, n))
    return out if any(dl.kind in ("add", "del") for dl in out) else []


def _trim_chunk(body: list[DiffLine], n: int) -> list[DiffLine]:
    keep = [False] * len(body)
    changed = False
    for i, dl in enumerate(body):
        if dl.kind in ("add", "del"):
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
        old_start = next((dl.old_no for dl in seg if dl.old_no is not None), 0)
        new_start = next((dl.new_no for dl in seg if dl.new_no is not None), 0)
        old_cnt = sum(1 for dl in seg if dl.kind != "add")
        new_cnt = sum(1 for dl in seg if dl.kind != "del")
        out.append(DiffLine("hunk", None, None, f"@@ -{old_start},{old_cnt} +{new_start},{new_cnt} @@"))
        out.extend(seg)
        i = j
    return out


@dataclass
class SplitRow:
    left: DiffLine | None     # None：这一侧是补出来的空行
    right: DiffLine | None


def build_split(lines: list[DiffLine]) -> list[SplitRow]:
    """左右对照。连续的“删除 + 新增”算一个改动块，逐行配对，短的一侧补空行，保证两侧行数一致。
    hunk 标题行丢弃；note 行（分段标题、截断说明）两侧都保留。"""
    rows: list[SplitRow] = []
    dels: list[DiffLine] = []
    adds: list[DiffLine] = []

    def flush() -> None:
        for k in range(max(len(dels), len(adds))):
            rows.append(SplitRow(dels[k] if k < len(dels) else None,
                                 adds[k] if k < len(adds) else None))
        dels.clear()
        adds.clear()

    for dl in lines:
        if dl.kind == "hunk":
            continue
        if dl.kind == "del":
            dels.append(dl)
        elif dl.kind == "add":
            adds.append(dl)
        else:                       # ctx，或 note（两侧都显示这句提示）
            flush()
            rows.append(SplitRow(dl, dl))
    flush()
    return rows


def change_groups(flags: list[tuple[bool, bool]]) -> list[tuple[int, int, bool, bool]]:
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
