import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from dxsync.gui import MainWindow
from dxsync.models import Project, SiteType, SyncMapping
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore
from dxsync.wordpress_setup import WordPressSetupProgress


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
    assert window.setup_panel.isHidden()
    assert window.wordpress_setup_button.isHidden()
    assert window.laravel_setup_button.isHidden()
    assert window.wordpress_setup_button.text() == "セットアップを開始…"
    assert window.wordpress_setup_action.text() == (
        "WordPressをXServerへ新規セットアップ…"
    )
    assert not window.wordpress_setup_action.isVisible()
    assert not window.laravel_setup_action.isVisible()
    window.close()


def test_setup_panel_follows_selected_project_site_type(tmp_path: Path) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    wordpress_mapping = SyncMapping.create(
        "WordPress",
        str(tmp_path / "wordpress"),
        "/home/sample/wp.example.com/public_html",
    )
    laravel_mapping = SyncMapping.create(
        "Laravel",
        str(tmp_path / "laravel"),
        "/home/sample/laravel.example.com",
    )
    wordpress = Project.create(
        name="WordPress",
        site_type=SiteType.WORDPRESS,
        domain="wp.example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/test.key",
        mappings=(wordpress_mapping,),
    )
    laravel = Project.create(
        name="Laravel",
        site_type=SiteType.LARAVEL,
        domain="laravel.example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/test.key",
        mappings=(laravel_mapping,),
    )
    store.save(wordpress)
    store.save(laravel)
    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )

    window.project_combo.setCurrentIndex(
        window.project_combo.findData(wordpress.project_id)
    )
    assert not window.setup_panel.isHidden()
    assert not window.wordpress_setup_button.isHidden()
    assert window.laravel_setup_button.isHidden()
    assert window.wordpress_setup_action.isVisible()
    assert not window.laravel_setup_action.isVisible()
    assert "WordPress初回セットアップ" in window.setup_text.text()

    window.project_combo.setCurrentIndex(
        window.project_combo.findData(laravel.project_id)
    )
    assert not window.setup_panel.isHidden()
    assert window.wordpress_setup_button.isHidden()
    assert not window.laravel_setup_button.isHidden()
    assert not window.wordpress_setup_action.isVisible()
    assert window.laravel_setup_action.isVisible()
    assert "Laravel初回セットアップ" in window.setup_text.text()

    window.project_combo.setCurrentIndex(0)
    assert window.setup_panel.isHidden()
    assert not window.wordpress_setup_action.isVisible()
    assert not window.laravel_setup_action.isVisible()
    window.close()


def test_wordpress_progress_shows_count_percent_eta_and_heartbeat(
    tmp_path: Path,
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    now = time.monotonic()
    window._wordpress_progress_started_at = now - 30
    window._wordpress_progress_phase_started_at = now - 15
    window._wordpress_progress_last_update_at = now
    window._wordpress_progress_phase_key = (8, "WordPressファイルを転送中", "files", 457)

    window._wordpress_setup_progress(
        WordPressSetupProgress(
            step=8,
            total_steps=10,
            message="WordPressファイルを転送中",
            current=15,
            total=457,
            unit="files",
            detail="wp-content/uploads/example.jpg",
        )
    )

    assert window.progress_bar.maximum() == 1000
    assert "15/457" in window.progress_bar.format()
    assert "%" in window.progress_bar.format()
    assert "残り約" in window.summary.text()
    assert "経過" in window.summary.text()
    assert "wp-content/uploads/example.jpg" in window.summary.text()

    window._wordpress_progress_last_update_at = time.monotonic() - 3
    window._refresh_wordpress_progress()
    assert "サーバー応答待ち" in window.summary.text()
    window._stop_wordpress_progress()
    window.close()
