"""Windows 全局热键（RegisterHotKey + Qt 原生事件过滤器），以及“把窗口弄到最前面”。

在其它系统上这些函数什么也不做、安全地返回 False。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtWidgets import QApplication

IS_WIN = sys.platform == "win32"

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040
SW_RESTORE = 9

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.IsIconic.argtypes = [wintypes.HWND]


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id: int, callback):
        super().__init__()
        self._id = hotkey_id
        self._callback = callback

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == self._id:
                    QTimer.singleShot(0, self._callback)      # 不要在原生事件回调里直接操作窗口
                    return True, 0
        except Exception:                                       # noqa: BLE001
            pass
        return False, 0


class GlobalHotkey:
    """全局热键。register() 成功后，无论 ggit 是否在前台，按键都会调用 callback。"""

    def __init__(self, callback, vk: int = ord("G"), modifiers: int = MOD_ALT, hotkey_id: int = 0x6767):
        self._callback = callback
        self._vk = vk
        self._mods = modifiers
        self._id = hotkey_id
        self._filter = None
        self.registered = False

    def register(self) -> bool:
        if not IS_WIN or self.registered:
            return self.registered
        if not _user32.RegisterHotKey(None, self._id, self._mods | MOD_NOREPEAT, self._vk):
            return False        # 多半是被别的程序占用了
        self._filter = _Filter(self._id, self._callback)
        QApplication.instance().installNativeEventFilter(self._filter)
        self.registered = True
        return True

    def unregister(self) -> None:
        if not self.registered:
            return
        _user32.UnregisterHotKey(None, self._id)
        if self._filter is not None:
            QApplication.instance().removeNativeEventFilter(self._filter)
            self._filter = None
        self.registered = False


def bring_to_front(hwnd: int) -> None:
    """把窗口显示到所有窗口最前面并激活（先临时置顶再取消，绕开前台窗口限制）。"""
    if not IS_WIN:
        return
    h = wintypes.HWND(hwnd)
    if _user32.IsIconic(h):
        _user32.ShowWindow(h, SW_RESTORE)
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
    _user32.SetWindowPos(h, wintypes.HWND(-1), 0, 0, 0, 0, flags)    # HWND_TOPMOST
    _user32.SetWindowPos(h, wintypes.HWND(-2), 0, 0, 0, 0, flags)    # HWND_NOTOPMOST
    _user32.SetForegroundWindow(h)
