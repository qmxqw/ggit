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


def main() -> int:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox

    _set_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("ggit")

    from .icon import app_icon
    app.setWindowIcon(app_icon())

    # ---- 单实例：已经有一个在运行，就通知它显示出来，然后退出 ----
    name = "ggit-single-instance"
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
            "未找到 git 命令。\n\n请先安装 Git for Windows（https://git-scm.com），\n并确保 git 在 PATH 中，然后重新打开 ggit。")
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
