"""界面上可切换的显示选项（所有项目共用）。"""
from dataclasses import dataclass, replace


@dataclass
class ViewOptions:
    ignore_ws: bool = False      # 忽略空白差异
    ignore_eol: bool = False     # 忽略换行符（CRLF / LF）差异
    encoding: str = "auto"       # auto / utf-8 / gb18030
    diff_mode: str = "unified"   # unified：上下合并；split：左右对照
    work_mode: str = "git"       # git：提交图 + diff；file：文件树 + 文件预览
    font_size: int = 9           # 全局字体大小 (pt)

    def copy(self) -> "ViewOptions":
        return replace(self)
