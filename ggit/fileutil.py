"""文件模式用到的纯 Python 工具：读取文本文件做预览、遍历项目里的文件。不依赖界面库。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .gitcore import ENC_AUTO, decode_line

MAX_PREVIEW_BYTES = 3 * 1024 * 1024      # 预览最多读这么多，更大的文件只显示开头
SKIP_DIRS = {".git"}


@dataclass
class FileContent:
    path: str
    lines: list = field(default_factory=list)
    size: int = 0
    is_binary: bool = False
    truncated: bool = False
    encoding: str = ""
    message: str = ""        # 非空：无法预览的原因（界面直接显示这句话）


def _encoding_label(data: bytes, encoding: str) -> str:
    if encoding and encoding != ENC_AUTO:
        return {"utf-8": "UTF-8", "gb18030": "GBK"}.get(encoding, encoding)
    sample = data[:65536]
    if len(sample) == 65536:
        sample = sample[:-3]                 # 别把末尾被截断的多字节字符当成解码失败
    if sample.isascii():
        return "ASCII"
    try:
        sample.decode("utf-8")
        return "UTF-8"
    except UnicodeDecodeError:
        return "GBK（自动识别）"


def read_text_file(path: str, encoding: str = ENC_AUTO) -> FileContent:
    """读取文本 / 代码文件。逐行解码：先 UTF-8，失败再 GBK（encoding 指定了就按指定的）。"""
    fc = FileContent(path=path)
    try:
        if os.path.isdir(path):
            fc.message = "这是一个目录。"
            return fc
        fc.size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read(MAX_PREVIEW_BYTES + 1)
    except FileNotFoundError:
        fc.message = "文件已不存在。"
        return fc
    except OSError as e:
        fc.message = f"无法读取文件：{e}"
        return fc
    if len(data) > MAX_PREVIEW_BYTES:
        data = data[:MAX_PREVIEW_BYTES]
        fc.truncated = True
    if b"\0" in data[:8192]:
        fc.is_binary = True
        fc.message = "二进制文件，不预览。"
        return fc
    if not data:
        fc.message = "空文件。"
        return fc
    raw_lines = data.split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    if fc.truncated and raw_lines:
        raw_lines.pop()                      # 最后一行很可能被截断了
    fc.lines = [decode_line(l[:-1] if l.endswith(b"\r") else l, encoding) for l in raw_lines]
    fc.encoding = _encoding_label(data, encoding)
    return fc


def walk_files(root: str, limit: int = 50000) -> list:
    """项目里的所有文件（相对路径，用 / 分隔，已排序）；不进入 .git。最多返回 limit 个。"""
    out: list = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            rel = name if rel_dir == "." else f"{rel_dir}/{name}".replace("\\", "/")
            out.append(rel)
            if len(out) >= limit:
                return out
    return out
