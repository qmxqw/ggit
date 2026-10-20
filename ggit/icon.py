"""程序图标：从内嵌的 PNG 数据生成 QIcon（打包成 exe 后也不依赖外部图片文件）。"""
import base64

from PySide6.QtGui import QIcon, QPixmap

from .icon_data import ICON_PNGS

_icon = None


def app_icon() -> QIcon:
    global _icon
    if _icon is None:
        icon = QIcon()
        for _size, b64 in sorted(ICON_PNGS.items()):
            pm = QPixmap()
            if pm.loadFromData(base64.b64decode(b64)):     # 格式从文件头自动识别（PNG）
                icon.addPixmap(pm)
        _icon = icon
    return _icon
