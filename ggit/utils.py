"""与界面库无关的小工具：时间格式、有上限的缓存。"""
from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Container, Hashable
from datetime import datetime
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def ago(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{int(d // 60)} 分钟前"
    if d < 86400:
        return f"{int(d // 3600)} 小时前"
    if d < 86400 * 60:
        return f"{int(d // 86400)} 天前"
    return fmt_time(ts)


class LruCache(Generic[K, V]):
    """最多保存 max_size 项的缓存，超出时丢掉最久没用过的。

    put() 可以传入 keep：这些键无论多久没用都不会被丢掉（比如正展开着的提交）。
    """

    def __init__(self, max_size: int) -> None:
        self.max_size = max_size
        self._data: OrderedDict[K, V] = OrderedDict()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: K) -> V | None:
        """取出一项（并把它标记为“刚用过”）；没有返回 None。"""
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def peek(self, key: K) -> V | None:
        """取出一项，但不改变它的新旧顺序。"""
        return self._data.get(key)

    def put(self, key: K, value: V, keep: Container[K] = ()) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        extra = len(self._data) - self.max_size
        if extra > 0:
            for k in [k for k in self._data if k not in keep and k != key][:extra]:
                del self._data[k]
