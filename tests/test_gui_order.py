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
    assert window.centralWidget().layout().contentsMargins().top() == 4
    connection_layout = window.auto_sync_button.parentWidget().layout()
    dry_position = connection_layout.getItemPosition(
        connection_layout.indexOf(window.dry_run_button)
    )
    auto_position = connection_layout.getItemPosition(
        connection_layout.indexOf(window.auto_sync_button)
    )
    assert auto_position[0] == dry_position[0] + 1
    assert auto_position[1] == dry_position[1]
    assert window.wordpress_setup_button.isEnabled()
    assert window.wordpress_setup_button.text() == "セットアップを開始…"
    assert window.wordpress_setup_action.text() == (
        "WordPressをXServerへ新規セットアップ…"
    )
    window.close()
