"""与界面库无关的小工具。"""
import time
from datetime import datetime

# 文件状态字母的颜色
STATUS_COLORS = {
    "M": "#e2c08d", "A": "#73c991", "D": "#f14c4c", "R": "#4fc1ff",
    "U": "#73c991", "C": "#ff8800", "T": "#e2c08d",
}


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
