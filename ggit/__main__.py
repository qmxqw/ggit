"""入口：python -m ggit  （Windows 下也可以双击 ggit.pyw）

ggit 是常驻后台的程序：窗口的 × 只是隐藏到托盘；Alt+G（全局热键）显示 / 隐藏；
再次启动只会把已经在运行的那个窗口调出来。开机自启时可加参数 --background（启动后不显示窗口）。
"""
import sys


def _set_app_id() -> None:
    """让 Windows 任务栏把 ggit 当成独立的程序（用 ggit 的图标，而不是 python 的）。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ggit.app")
        except Exception:      # noqa: BLE001
            pass


def _user_tag() -> str:
    """当前用户的短标识（用户名的哈希：中文用户名也不会互相撞上）。"""
    import getpass
    import hashlib
    try:
        user = getpass.getuser()
    except Exception:      # noqa: BLE001
        user = ""
    return hashlib.sha1(user.encode("utf-8", "replace")).hexdigest()[:12]


def _install_excepthook() -> None:
    """未捕获异常时记录日志并弹出提示，避免静默闪退。"""
    def _hook(exc_type, exc_value, exc_tb):
        import os
        import traceback
        from datetime import datetime

        from . import config

        log_file = config.crash_log_path()
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*25} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {'='*25}\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass

        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance():
                lines = traceback.format_exception(exc_type, exc_value, exc_tb)
                detail = "".join(lines)
                if len(detail) > 1000:
                    detail = detail[:1000] + "\n..."
                QMessageBox.critical(
                    None, "ggit 运行异常",
                    f"程序发生未捕获的异常：\n{exc_type.__name__}: {exc_value}\n\n"
                    f"详细日志已保存至：\n{log_file}\n\n{detail}"
                )
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def main() -> int:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox

    _set_app_id()
    _install_excepthook()
    app = QApplication(sys.argv)
    app.setApplicationName("ggit")

    from .icon import app_icon
    app.setWindowIcon(app_icon())

    from PySide6.QtCore import Qt
    QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)
    QApplication.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, False)

    # ---- 单实例：已经有一个在运行，就通知它显示出来，然后退出 ----
    # Windows 的命名管道是整台机器共用的：名字里带上用户名，多个用户同时登录时互不干扰
    name = "ggit-single-instance-" + _user_tag()
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    from . import gitcore, theme
    theme.apply_dark_theme(app)
    if not gitcore.find_git():
        QMessageBox.critical(
            None, "ggit",
            "未找到 git 命令。\n\n请先安装 Git for Windows（https://git-scm.com），\n"
            "并确保 git 在 PATH 中，然后重新打开 ggit。")
        return 1

    from .ui_main import MainWindow
    win = MainWindow()

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnectFromServer()
            conn.deleteLater()
        win.bring_to_front()

    server.newConnection.connect(on_connection)
    if win.tray is not None:
        app.setQuitOnLastWindowClosed(False)       # 关窗口 = 隐藏到托盘，进程继续在后台
    if "--background" not in sys.argv:
        win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
