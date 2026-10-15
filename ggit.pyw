# 双击运行（Windows 下 .pyw 不会弹出黑色控制台窗口）
import sys

try:
    from ggit.__main__ import main
    raise SystemExit(main())
except SystemExit:
    raise
except Exception:
    import ctypes
    import traceback
    err = traceback.format_exc()
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(0, f"ggit 启动失败：\n\n{err}", "ggit 错误", 0x10)
        except Exception:
            pass
    raise
