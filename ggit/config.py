r"""配置文件：项目列表、当前项目、窗口位置、显示选项。

Windows：%APPDATA%\ggit\config.json
其它系统：~/.config/ggit/config.json
"""
import json
import os
import sys


def config_path() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ggit", "config.json")


def load() -> dict:
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)       # 原子替换，避免写到一半崩溃留下坏文件
    except OSError:
        pass
