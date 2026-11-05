import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from dxsync.gui import MainWindow
from dxsync.models import SiteType
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_main_window_site_order(tmp_path: Path) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )

    assert [window.site_combo.itemData(index) for index in range(3)] == [
        SiteType.CUSTOM_PHP.value,
        SiteType.WORDPRESS.value,
        SiteType.LARAVEL.value,
    ]
    window.close()
