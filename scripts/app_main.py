import sys


def run() -> int:
    if "--smoke-test" in sys.argv:
        from PySide6.QtWidgets import QApplication

        from dxsync.gui import MainWindow

        app = QApplication([])
        window = MainWindow()
        print(window.windowTitle())
        window.close()
        app.quit()
        return 0

    from dxsync.gui import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run())
