import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from dxsync.gui import MainWindow
from dxsync.health import HealthCheckResult
from dxsync.models import Project, SiteType, SyncMapping
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.service import DryRunProgress
from dxsync.storage import ProjectStore
from dxsync.wordpress_setup import WordPressSetupProgress, WordPressSetupResult


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


def test_dry_run_progress_shows_count_percent_eta_and_heartbeat(
    tmp_path: Path,
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    now = time.monotonic()
    window._dry_run_progress_started_at = now - 30
    window._dry_run_progress_phase_started_at = now - 15
    window._dry_run_progress_last_update_at = now
    window._dry_run_progress_phase_key = (
        3,
        "XServerファイルを読み取り中",
        "files",
        457,
    )

    window._dry_run_progress(
        DryRunProgress(
            stage=3,
            total_stages=4,
            message="XServerファイルを読み取り中",
            current=15,
            total=457,
            unit="files",
            detail="wp-content/plugins/example.php",
        )
    )

    assert "15/457" in window.progress_bar.format()
    assert "%" in window.progress_bar.format()
    assert "残り約" in window.summary.text()
    assert "経過" in window.summary.text()
    assert "wp-content/plugins/example.php" in window.summary.text()
    window._dry_run_progress_last_update_at = time.monotonic() - 3
    window._refresh_dry_run_progress()
    assert "サーバー応答待ち" in window.summary.text()
    window._stop_dry_run_progress()
    window.close()


def test_interrupted_auto_dry_run_can_resume_after_reopen(tmp_path: Path) -> None:
    app()
    mapping = SyncMapping.create(
        "WordPress", str(tmp_path), "/home/sample/example.com/public_html"
    )
    project = Project.create(
        name="Example",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/test.key",
        mappings=(mapping,),
    )
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.save(project)
    store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        kind="dry_run",
        details={"resumable": True, "auto_sync": True},
    )

    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window.project_combo.setCurrentIndex(
        window.project_combo.findData(project.project_id)
    )
    window._project_selected(window.project_combo.currentIndex())
    operation = window._latest_resumable_operation()
    assert operation is not None
    assert operation.status == "interrupted"
    assert operation.kind == "dry_run"

    resumed: list[bool] = []
    window._start_auto_sync = lambda: resumed.append(True)
    window._resume_operation()
    assert resumed == [True]
    window.close()


def test_wordpress_completion_dialog_contains_clickable_urls(
    tmp_path: Path, monkeypatch
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    shown: list[QMessageBox] = []
    fake_exec = lambda self: shown.append(self) or QMessageBox.Ok
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    result = WordPressSetupResult(
        db_name="sample_wp",
        db_user="sample_user",
        transferred_files=0,
        transferred_bytes=0,
        table_count=12,
        url_replaced=True,
        public_url="https://demo.example.com",
        detected_home_url="http://localhost:9999",
        detected_site_url="http://localhost:9999",
        replaced_urls=("http://localhost:9999",),
    )
    window._wordpress_setup_completed(
        (
            result,
            HealthCheckResult("https://demo.example.com/", True, 200),
            HealthCheckResult(
                "https://demo.example.com/wp-login.php", True, 200
            ),
        )
    )

    assert len(shown) == 1
    assert 'href="https://demo.example.com/"' in shown[0].text()
    assert 'href="https://demo.example.com/wp-login.php"' in shown[0].text()
    assert any(label.openExternalLinks() for label in shown[0].findChildren(QLabel))
    window.close()
