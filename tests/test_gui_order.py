import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from dxsync.gui import DryRunWorker, MainWindow, OperationProgress
from dxsync.health import HealthCheckResult
from dxsync.laravel_setup import LaravelSetupProgress
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

    assert [window.site_combo.itemData(index) for index in range(window.site_combo.count())] == [
        SiteType.CUSTOM_PHP.value,
        SiteType.WORDPRESS.value,
        SiteType.LARAVEL.value,
        SiteType.STATIC_SITE.value,
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
    assert window.laravel_resume_button.isHidden()
    assert window.wordpress_setup_button.text() == "セットアップを開始…"
    assert window.wordpress_setup_action.text() == (
        "WordPressをXServerへ新規セットアップ…"
    )
    assert not window.wordpress_setup_action.isVisible()
    assert not window.laravel_setup_action.isVisible()

    # Verify project bar button order: combo -> new -> duplicate -> edit -> delete -> recovery -> history
    pb = window.project_bar
    assert pb.indexOf(window.project_combo) < pb.indexOf(window.new_project_button)
    assert pb.indexOf(window.new_project_button) < pb.indexOf(window.duplicate_project_button)
    assert pb.indexOf(window.duplicate_project_button) < pb.indexOf(window.edit_project_button)
    assert pb.indexOf(window.edit_project_button) < pb.indexOf(window.delete_project_button)
    assert pb.indexOf(window.delete_project_button) < pb.indexOf(window.recovery_button)
    assert pb.indexOf(window.recovery_button) < pb.indexOf(window.history_button)
    assert not window.duplicate_project_button.isEnabled()
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
    assert not window.wordpress_resume_button.isHidden()
    assert window.laravel_setup_button.isHidden()
    assert window.laravel_resume_button.isHidden()
    assert window.wordpress_setup_action.isVisible()
    assert not window.laravel_setup_action.isVisible()
    assert "WordPress初回セットアップ" in window.setup_text.text()

    window.project_combo.setCurrentIndex(
        window.project_combo.findData(laravel.project_id)
    )
    assert not window.setup_panel.isHidden()
    assert window.wordpress_setup_button.isHidden()
    assert window.wordpress_resume_button.isHidden()
    assert not window.laravel_setup_button.isHidden()
    assert not window.laravel_resume_button.isHidden()
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

    def fake_exec(self):
        return shown.append(self) or QMessageBox.Ok
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


def test_deployment_progress_does_not_duplicate_phase_or_prefix(
    tmp_path: Path,
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window._auto_sync_active = True
    window._deployment_progress(56, 130, "転送準備: data/backups/manual_data.json")

    # The progress bar shows only current/total and percentage (clean metric)
    assert window.progress_bar.format() == "56/130 (43.1%)"
    assert "転送準備" not in window.progress_bar.format()
    assert "自動実行" not in window.progress_bar.format()

    # The summary label shows the full stage and phase detail
    assert "自動実行 4/4 — 承認済み同期を実行中… 転送準備: data/backups/manual_data.json" in window.summary.text()
    window.close()


def test_laravel_resumable_operation_lifecycle(tmp_path: Path) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    laravel_mapping = SyncMapping.create(
        "Laravel",
        str(tmp_path / "laravel"),
        "/home/sample/laravel.example.com",
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
    store.save(laravel)
    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window.project_combo.setCurrentIndex(
        window.project_combo.findData(laravel.project_id)
    )

    # Initially no resumable operation
    assert window._latest_resumable_operation() is None

    # Simulate an interrupted laravel_setup (e.g. app was closed while running)
    op = store.start_operation(
        project_id=laravel.project_id,
        mapping_id=laravel_mapping.mapping_id,
        kind="laravel_setup",
        details={"resumable": True, "database_mode": "create"},
    )
    store.recover_interrupted_operations()

    resumable = window._latest_resumable_operation()
    assert resumable is not None
    assert resumable.operation_id == op.operation_id
    assert resumable.kind == "laravel_setup"

    # Retiring marks it not resumable
    window._retire_resumable_operations("laravel_setup")
    assert window._latest_resumable_operation() is None
    window.close()


def test_main_window_duplicate_project_flow(tmp_path: Path, monkeypatch) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    key_file = tmp_path / "id_rsa"
    key_file.write_text("dummy key")
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    mapping = replace(
        SyncMapping.create(
            "site",
            str(site_dir),
            "/home/sample/example.com/public_html",
        ),
        runtime_paths_confirmed=True,
    )
    project = Project.create(
        name="original.example.com",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference=str(key_file),
        mappings=(mapping,),
    )
    store.save(project)

    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    # The saved project is selected on startup, so duplicate is enabled
    assert window.duplicate_project_button.isEnabled()

    # When duplicate dialog executes and saves:
    from dxsync.project_dialog import ProjectDialog

    def mock_exec(dialog_self):
        dialog_self._save()
        return QDialog.Accepted

    monkeypatch.setattr(ProjectDialog, "exec", mock_exec)
    window._duplicate_project()

    # MainWindow should have automatically reloaded and selected the duplicated project
    assert window._current_project is not None
    assert window._current_project.name == "original.example.com のコピー"
    assert window._current_project.project_id != project.project_id
    window.close()


def test_main_window_show_about_includes_github_link(tmp_path: Path, monkeypatch) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    shown_boxes = []

    def mock_exec(box_self):
        shown_boxes.append(box_self)
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "exec", mock_exec)
    window._show_about()

    assert len(shown_boxes) == 1
    assert "https://github.com/StayHomeLabNet/docker-xserver-sync" in shown_boxes[0].text()
    assert any(label.openExternalLinks() for label in shown_boxes[0].findChildren(QLabel))
    window.close()


def test_main_window_help_menu_has_online_manual(tmp_path: Path, monkeypatch) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    assert window.manual_action.text() == "オンラインマニュアル"

    opened_urls = []
    from PySide6.QtGui import QDesktopServices
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened_urls.append(url.toString()))
    window._open_online_manual()

    assert len(opened_urls) == 1
    assert opened_urls[0] == "https://stayhomelab.net/help/docker-xserver-sync/"
    window.close()


def test_auto_sync_dry_run_progress_shows_stage_eta_and_heartbeat(
    tmp_path: Path,
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window._auto_sync_active = True
    window._auto_sync_stage = "dry_run"

    window._dry_run_progress(
        DryRunProgress(
            stage=3,
            total_stages=4,
            message="XServerファイルを読み取り中",
            current=0,
            total=1200,
            unit="files",
            detail="assets/app.js",
        )
    )
    assert "自動実行 1/4 — 手順 3/4" in window.summary.text()
    assert "0/1,200" in window.summary.text()
    assert "残り時間を計算中" in window.summary.text()

    now = time.monotonic()
    window._dry_run_progress_started_at = now - 25
    window._dry_run_progress_phase_started_at = now - 10
    window._dry_run_progress(
        DryRunProgress(
            stage=3,
            total_stages=4,
            message="XServerファイルを読み取り中",
            current=300,
            total=1200,
            unit="files",
            detail="assets/app.css",
        )
    )
    assert "300/1,200" in window.summary.text()
    assert "25.0%" in window.summary.text()
    assert "残り約" in window.summary.text()
    assert "経過" in window.summary.text()
    assert "assets/app.css" in window.summary.text()

    window._dry_run_progress_last_update_at = time.monotonic() - 3
    window._refresh_dry_run_progress()
    assert "サーバー応答待ち" in window.summary.text()
    window._stop_dry_run_progress()
    window.close()


def test_start_dry_run_for_static_site_wires_progress_timer_and_stops_cleanly(
    tmp_path: Path, monkeypatch
) -> None:
    app()
    site_dir = tmp_path / "static"
    site_dir.mkdir()
    (site_dir / "index.html").write_text("<h1>hello</h1>")
    key_file = tmp_path / "id_ed25519"
    key_file.write_text("dummy")

    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    mapping = SyncMapping.create(
        "static",
        str(site_dir),
        "/home/sample/example.com/public_html",
    )
    project = Project.create(
        name="Static Site",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference=str(key_file),
        mappings=(mapping,),
    )
    store.save(project)

    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )

    import threading

    from dxsync import gui as gui_module

    entered_execute = threading.Event()
    proceed_execute = threading.Event()

    class FakeCloseable:
        def close(self) -> None:
            pass

    def fake_connect_sftp(**_kwargs):
        return FakeCloseable(), FakeCloseable()

    def fake_execute(**kwargs):
        progress_callback = kwargs["progress_callback"]
        cancellation_token = kwargs["cancellation_token"]
        progress_callback(
            DryRunProgress(
                stage=2,
                total_stages=4,
                message="ローカルファイルを確認中",
                current=1,
                total=10,
                unit="files",
                detail="index.html",
            )
        )
        entered_execute.set()
        assert proceed_execute.wait(timeout=5.0)
        cancellation_token.raise_if_cancelled()
        raise AssertionError("Expected cancellation")

    monkeypatch.setattr(gui_module, "connect_sftp", fake_connect_sftp)
    monkeypatch.setattr(gui_module.DryRunService, "execute", staticmethod(fake_execute))

    window._auto_sync_active = True
    window._auto_sync_stage = "dry_run"
    window._start_dry_run()

    assert window._dry_run_progress_timer.isActive()
    assert not window.stop_button.isHidden()
    assert "自動実行 1/4 — 手順 1/4" in window.summary.text()
    assert "XServerへSSH接続中" in window.summary.text()

    assert entered_execute.wait(timeout=5.0)
    app().processEvents()
    assert "1/10" in window.summary.text()
    assert "index.html" in window.summary.text()

    assert isinstance(window._worker, DryRunWorker)
    window._worker.cancel()
    proceed_execute.set()

    deadline = time.monotonic() + 5.0
    while window._thread is not None and time.monotonic() < deadline:
        app().processEvents()
        time.sleep(0.01)

    assert window._thread is None
    assert not window._dry_run_progress_timer.isActive()
    assert not window._auto_sync_active
    window.close()


def test_operation_progress_covers_sync_and_local_scan_states(
    tmp_path: Path,
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )

    # Local scan: remote_wait=False should show 処理継続中 rather than サーバー応答待ち
    now = time.monotonic()
    window._op_progress_started_at = now - 10
    window._op_progress_phase_started_at = now - 5
    window._op_progress_phase_key = (1, "SHA-256を計算中", "files", 100)
    window._update_op_progress(
        OperationProgress(
            stage=1,
            total_stages=1,
            message="SHA-256を計算中",
            current=25,
            total=100,
            unit="files",
            detail="images/hero.png",
            summary_prefix="ローカル検査中…",
            remote_wait=False,
        )
    )
    assert "25/100" in window.progress_bar.format()
    assert "残り約" in window.summary.text()
    assert "images/hero.png" in window.summary.text()

    window._op_progress_last_update_at = time.monotonic() - 3
    window._refresh_op_progress()
    assert "処理継続中 (最終更新から" in window.summary.text()
    assert "サーバー応答待ち" not in window.summary.text()

    # Revalidation during auto-sync
    window._auto_sync_active = True
    window._revalidation_progress(
        OperationProgress(
            stage=2,
            total_stages=4,
            message="ローカルファイルを読み取り中",
            current=5,
            total=20,
            unit="files",
            detail="index.html",
            progress_label="ローカルファイルを読み取り中",
            remote_wait=False,
        )
    )
    window._op_progress_last_update_at = time.monotonic() - 3
    window._refresh_op_progress()
    assert "自動実行 2/4 — 計画を再検証中…" in window.summary.text()
    assert "手順 2/4" in window.summary.text()
    assert "5/20" in window.summary.text()
    assert "index.html" in window.summary.text()
    assert "処理継続中 (最終更新から" in window.summary.text()
    assert "サーバー応答待ち" not in window.summary.text()
    assert "5/20 (25.0%)" == window.progress_label.text()

    window._revalidation_progress(
        OperationProgress(
            stage=3,
            total_stages=4,
            message="XServerファイルを読み取り中",
            current=4,
            total=20,
            unit="files",
            detail="about.html",
            progress_label="XServerファイルを読み取り中",
            remote_wait=True,
        )
    )
    assert "自動実行 2/4 — 計画を再検証中…" in window.summary.text()
    assert "手順 3/4" in window.summary.text()
    assert "4/20" in window.summary.text()
    assert "about.html" in window.summary.text()

    # Deployment preparation -> publish pass resets phase ETA cleanly
    window._deployment_progress(10, 10, "転送準備: app.js")
    assert "この工程は完了" in window.summary.text()
    previous_phase_started = (window._op_progress_phase_started_at or time.monotonic()) - 20
    window._op_progress_phase_started_at = previous_phase_started

    window._deployment_progress(1, 10, "公開切り替え: app.js")
    assert window._op_progress_phase_started_at > previous_phase_started
    assert "公開切り替え: app.js" in window.summary.text()

    # Restore progress with server heartbeat
    window._auto_sync_active = False
    window._update_op_progress(
        OperationProgress(
            stage=3,
            total_stages=3,
            message="復元準備: index.html",
            current=2,
            total=8,
            unit="files",
            phase_key=("restore", "復元準備", 8),
            summary_prefix="バックアップから復元中…",
        )
    )
    window._op_progress_last_update_at = time.monotonic() - 3
    window._refresh_op_progress()
    assert "手順 3/3" in window.summary.text()
    assert "バックアップから復元中…" in window.summary.text()
    assert "サーバー応答待ち" in window.summary.text()

    window._stop_op_progress()
    window.close()


def test_laravel_progress_shows_mib_eta_and_heartbeat(tmp_path: Path) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    now = time.monotonic()
    window._laravel_progress_started_at = now - 20
    window._laravel_progress_phase_started_at = now - 10
    window._laravel_progress_last_update_at = now
    window._laravel_progress_phase_key = (
        6,
        "Laravelアプリ本体を転送中",
        "bytes",
        10 * 1024 * 1024,
    )

    window._laravel_setup_progress(
        LaravelSetupProgress(
            step=6,
            total_steps=10,
            message="Laravelアプリ本体を転送中",
            current=2 * 1024 * 1024,
            total=10 * 1024 * 1024,
            unit="bytes",
            detail="vendor/autoload.php (5/40件)",
        )
    )

    assert "2.0/10.0 MiB" in window.progress_bar.format()
    assert "20.0%" in window.progress_bar.format()
    assert "残り約" in window.summary.text()
    assert "経過" in window.summary.text()
    assert "vendor/autoload.php (5/40件)" in window.summary.text()

    window._laravel_progress_last_update_at = time.monotonic() - 3
    window._refresh_laravel_progress()
    assert "サーバー応答待ち" in window.summary.text()
    window._stop_laravel_progress()
    window.close()


def test_throttled_progress_emitter_keeps_boundaries_and_phase_changes() -> None:
    from dxsync.gui import _ThrottledProgressEmitter

    emitted: list[DryRunProgress] = []
    emitter = _ThrottledProgressEmitter(emitted.append, min_interval=10.0)

    emitter(DryRunProgress(2, 4, "ローカルファイルを列挙中", 0, None, "directories", ""))
    emitter(DryRunProgress(2, 4, "ローカルファイルを列挙中", 1, None, "directories", "."))
    emitter(DryRunProgress(2, 4, "ローカルファイルを列挙中", 2, None, "directories", "a"))
    # Phase change + current == 0 boundary both emit immediately
    emitter(DryRunProgress(2, 4, "ローカルファイルを読み取り中", 0, 5, "files", "f1.txt"))
    # Intermediate within min_interval is throttled
    emitter(DryRunProgress(2, 4, "ローカルファイルを読み取り中", 1, 5, "files", "f1.txt"))
    emitter(DryRunProgress(2, 4, "ローカルファイルを読み取り中", 2, 5, "files", "f2.txt"))
    # Completion boundary (current >= total) emits immediately
    emitter(DryRunProgress(2, 4, "ローカルファイルを読み取り中", 5, 5, "files", "f5.txt"))

    assert [(e.message, e.current, e.total) for e in emitted] == [
        ("ローカルファイルを列挙中", 0, None),
        ("ローカルファイルを読み取り中", 0, 5),
        ("ローカルファイルを読み取り中", 5, 5),
    ]


def test_dry_run_progress_updates_inline_label_and_avoids_redundant_range_reset(
    tmp_path: Path, monkeypatch
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )

    range_calls: list[tuple[int, int]] = []
    original_set_range = window.progress_bar.setRange

    def tracked_set_range(minimum: int, maximum: int) -> None:
        range_calls.append((minimum, maximum))
        original_set_range(minimum, maximum)

    monkeypatch.setattr(window.progress_bar, "setRange", tracked_set_range)

    # 1) Directory enumeration phase (two updates should call setRange(0, 0) only once)
    window._dry_run_progress(
        DryRunProgress(2, 4, "ローカルファイルを列挙中", 0, None, "directories", "")
    )
    window._dry_run_progress(
        DryRunProgress(2, 4, "ローカルファイルを列挙中", 12, None, "directories", "assets")
    )
    assert not window.progress_label.isHidden()
    assert window.progress_label.text() == "12フォルダ"
    assert "12フォルダ確認済み" in window.summary.text()
    assert range_calls == [(0, 0)]

    # 2) Early determinate phase (< 1s) with current > 0 shows ETA calculation placeholder
    window._dry_run_progress(
        DryRunProgress(2, 4, "ローカルファイルを読み取り中", 5, 100, "files", "assets/app.js")
    )
    assert window.progress_label.text() == "5/100 (5.0%)"
    assert "残り時間を計算中" in window.summary.text()
    assert range_calls == [(0, 0), (0, 1000)]

    # 3) Stage 4 planning (non-directory indeterminate) should show 処理継続中, not 全体数を確認中
    window._dry_run_progress(DryRunProgress(4, 4, "差分計画を作成中"))
    assert window.progress_label.text() == "処理中…"
    assert "処理継続中" in window.summary.text()
    assert "全体数を確認中" not in window.summary.text()

    # 4) Zero-total determinate-style fallback shows 0/0 cleanly
    window._dry_run_progress(DryRunProgress(4, 4, "承認用ファイルを準備中", 0, 0, "files", ""))
    assert window.progress_label.text() == "0/0"
    assert "0/0" in window.summary.text()

    window._stop_dry_run_progress()
    window._set_progress_visible(False)
    assert window.progress_label.isHidden()
    window.close()


def test_revalidation_worker_emits_detailed_progress_and_remote_wait(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from dxsync import gui as gui_module
    from dxsync.gui import RevalidationWorker
    from dxsync.models import SyncPlan
    from dxsync.revalidation import RevalidationResult
    from dxsync.service import DryRunProgress
    from dxsync.storage import StoredPlan
    from datetime import datetime, timedelta, timezone

    app()
    local_dir = tmp_path / "site"
    local_dir.mkdir()
    key_file = tmp_path / "id_ed25519"
    key_file.write_text("dummy", encoding="utf-8")
    mapping = SyncMapping.create("public", str(local_dir), "/home/user/site")
    project = Project.create(
        name="Static Site",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference=str(key_file),
        mappings=(mapping,),
    )
    now = datetime.now(timezone.utc)
    plan = SyncPlan(
        plan_id="plan-1",
        mapping_id=mapping.mapping_id,
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        changes=(),
        local_snapshot_sha256="a",
        remote_snapshot_sha256="b",
        config_sha256="c",
    )
    stored = StoredPlan(
        plan=plan,
        project_id=project.project_id,
        status="ready",
        staging_path=str(tmp_path / "staging"),
    )

    class FakeCloseable:
        def close(self) -> None:
            pass

    def fake_connect_sftp(**_kwargs):
        return FakeCloseable(), FakeCloseable()

    def fake_revalidate(self, **kwargs):
        cb = kwargs["progress_callback"]
        cb(DryRunProgress(1, 4, "ステージングを確認中", 2, 5, "files", "index.html"))
        cb(DryRunProgress(2, 4, "ローカルファイルを読み取り中", 3, 10, "files", "app.js"))
        cb(DryRunProgress(3, 4, "XServerファイルを読み取り中", 4, 8, "files", "style.css"))
        cb(DryRunProgress(4, 4, "差分と設定の一致を確認中"))
        return RevalidationResult(valid=True, reasons=(), current=None)

    monkeypatch.setattr(gui_module, "connect_sftp", fake_connect_sftp)
    monkeypatch.setattr(gui_module.PlanRevalidator, "revalidate", fake_revalidate)

    worker = RevalidationWorker(
        stored=stored,
        project=project,
        mapping=mapping,
        passphrase=None,
    )
    events: list[OperationProgress] = []
    worker.progress.connect(events.append)
    worker.run()

    staging_event = next(e for e in events if e.message == "ステージングを確認中")
    assert staging_event.current == 2
    assert staging_event.total == 5
    assert staging_event.detail == "index.html"
    assert staging_event.progress_label == "ステージングを確認中"
    assert staging_event.remote_wait is False

    local_event = next(e for e in events if e.message == "ローカルファイルを読み取り中")
    assert local_event.remote_wait is False
    assert local_event.progress_label == "ローカルファイルを読み取り中"

    remote_event = next(e for e in events if e.message == "XServerファイルを読み取り中")
    assert remote_event.remote_wait is True
    assert remote_event.detail == "style.css"

    compare_event = next(e for e in events if e.message == "差分と設定の一致を確認中")
    assert compare_event.stage == 4
    assert compare_event.remote_wait is False


def test_saved_project_locks_main_window_fields_and_keeps_lock_after_auto_sync(
    tmp_path: Path,
) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    site_dir = tmp_path / "wp"
    site_dir.mkdir()
    mapping = SyncMapping.create(
        "WordPress",
        str(site_dir),
        "/home/sample/wp.example.com/public_html",
    )
    project = Project.create(
        name="WordPress",
        site_type=SiteType.WORDPRESS,
        domain="wp.example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/test.key",
        mappings=(mapping,),
    )
    store.save(project)

    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )

    assert window._current_project is not None
    assert not window.site_combo.isEnabled()
    assert not window.browse_button.isEnabled()
    assert not window.port_spin.isEnabled()
    assert not window.key_browse_button.isEnabled()
    assert window.path_edit.isReadOnly()
    assert window.host_edit.isReadOnly()
    assert window.user_edit.isReadOnly()
    assert window.remote_root_edit.isReadOnly()
    assert window.fingerprint_edit.isReadOnly()
    assert window.key_edit.isReadOnly()

    # Unlocking after auto-sync / setup keeps saved-project lock intact
    window._set_auto_sync_controls_locked(True)
    window._set_auto_sync_controls_locked(False)
    assert not window.site_combo.isEnabled()
    assert window.path_edit.isReadOnly()

    # Switching to 手動設定（未保存） re-enables direct editing
    window.project_combo.setCurrentIndex(0)
    assert window._current_project is None
    assert window.site_combo.isEnabled()
    assert window.browse_button.isEnabled()
    assert not window.path_edit.isReadOnly()
    assert not window.host_edit.isReadOnly()

    # Switching back to the saved project restores the lock
    window.project_combo.setCurrentIndex(
        window.project_combo.findData(project.project_id)
    )
    assert not window.site_combo.isEnabled()
    assert window.path_edit.isReadOnly()
    window.close()


def test_static_site_manual_flow_dry_run_revalidation_backup_and_deploy(
    tmp_path: Path, monkeypatch
) -> None:
    import io
    import stat
    from dataclasses import dataclass
    from PySide6.QtWidgets import QDialogButtonBox
    from dxsync import gui as gui_module
    from dxsync.approval_dialog import (
        BackupConfirmationDialog,
        DeploymentApprovalDialog,
    )

    app()
    site_dir = tmp_path / "static_site"
    site_dir.mkdir()
    (site_dir / "index.html").write_bytes(b"<h1>updated</h1>")
    (site_dir / "new.html").write_bytes(b"<p>added</p>")
    key_file = tmp_path / "id_ed25519"
    key_file.write_text("dummy", encoding="utf-8")

    remote_root = "/home/sample/example.com/public_html"
    mapping = SyncMapping.create("public", str(site_dir), remote_root)
    project = Project.create(
        name="Static Example",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference=str(key_file),
        mappings=(mapping,),
    )
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.save(project)

    @dataclass
    class FakeAttr:
        filename: str
        st_mode: int
        st_size: int = 0
        st_mtime: int = 1

    class FakeWriter(io.BytesIO):
        def __init__(self, sftp_fs, path: str) -> None:
            super().__init__()
            self._sftp_fs = sftp_fs
            self._path = path

        def close(self) -> None:
            if not self.closed:
                self._sftp_fs.files[self._path] = self.getvalue()
            super().close()

    class FakeSftp:
        def __init__(self) -> None:
            self.directories = {
                "/",
                "/home",
                "/home/sample",
                "/home/sample/example.com",
                remote_root,
            }
            self.files: dict[str, bytes] = {
                f"{remote_root}/index.html": b"<h1>old</h1>",
                f"{remote_root}/stale.html": b"<p>stale</p>",
            }
            self.modes: dict[str, int] = {}

        def listdir_attr(self, path: str):
            norm = path.rstrip("/") or "/"
            prefix = "/" if norm == "/" else norm + "/"
            entries = []
            seen_dirs: set[str] = set()
            for directory in self.directories:
                if directory.startswith(prefix) and directory != norm:
                    rest = directory[len(prefix) :]
                    if "/" not in rest and rest and rest not in seen_dirs:
                        seen_dirs.add(rest)
                        entries.append(FakeAttr(rest, stat.S_IFDIR | 0o755))
            for file_path, data in self.files.items():
                if file_path.startswith(prefix):
                    rest = file_path[len(prefix) :]
                    if "/" not in rest and rest:
                        entries.append(
                            FakeAttr(
                                rest,
                                stat.S_IFREG | self.modes.get(file_path, 0o644),
                                len(data),
                                1,
                            )
                        )
            return entries

        def stat(self, path: str):
            if path in self.directories:
                return FakeAttr(Path(path).name or "/", stat.S_IFDIR | 0o755)
            if path in self.files:
                return FakeAttr(
                    Path(path).name,
                    stat.S_IFREG | self.modes.get(path, 0o644),
                    len(self.files[path]),
                    1,
                )
            raise FileNotFoundError(path)

        def mkdir(self, path: str, mode: int = 0o755) -> None:
            self.directories.add(path)

        def rmdir(self, path: str) -> None:
            self.directories.discard(path)

        def open(self, path: str, mode: str = "r"):
            if "w" in mode:
                return FakeWriter(self, path)
            if path not in self.files:
                raise FileNotFoundError(path)
            return io.BytesIO(self.files[path])

        def remove(self, path: str) -> None:
            self.files.pop(path, None)
            self.modes.pop(path, None)

        def rename(self, oldpath: str, newpath: str) -> None:
            self.files[newpath] = self.files.pop(oldpath)
            if oldpath in self.modes:
                self.modes[newpath] = self.modes.pop(oldpath)

        def chmod(self, path: str, mode: int) -> None:
            if path not in self.files:
                raise FileNotFoundError(path)
            self.modes[path] = mode

        def close(self) -> None:
            pass

    class FakeClient:
        def close(self) -> None:
            pass

    fake_sftp = FakeSftp()
    monkeypatch.setattr(
        gui_module,
        "connect_sftp",
        lambda **_kwargs: (FakeClient(), fake_sftp),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text, *_args: (_ for _ in ()).throw(AssertionError(f"Unexpected warning: {title}: {text}"))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda _parent, title, text, *_args: (_ for _ in ()).throw(AssertionError(f"Unexpected critical: {title}: {text}"))),
    )

    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )

    def wait_idle() -> None:
        deadline = time.monotonic() + 5.0
        while window._thread is not None and time.monotonic() < deadline:
            app().processEvents()
            time.sleep(0.01)
        app().processEvents()
        assert window._thread is None

    # 1) Dry run
    window._start_dry_run()
    wait_idle()
    assert window._current_plan_id is not None
    assert window.revalidate_button.isEnabled()
    assert not window.backup_button.isEnabled()
    assert not window.deploy_button.isEnabled()

    # 2) Revalidation
    window._start_revalidation()
    wait_idle()
    assert window._validated_plan_id == window._current_plan_id
    assert window.backup_button.isEnabled()
    assert not window.deploy_button.isEnabled()
    assert "次は「バックアップを作成」へ進んでください。" in window.summary.text()

    # 3) Skip backup option unlocks deploy while leaving backup option available
    def skip_backup_exec(dialog_self):
        dialog_self.skip_backup.setChecked(True)
        dialog_self.confirm.setChecked(True)
        assert dialog_self.buttons.button(QDialogButtonBox.Ok).isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(BackupConfirmationDialog, "exec", skip_backup_exec)
    window._start_backup()
    assert window._backup_skipped is True
    assert window.deploy_button.isEnabled()
    assert window.backup_button.isEnabled()

    # 4) Actual backup creation
    def run_backup_exec(dialog_self):
        dialog_self.skip_backup.setChecked(False)
        dialog_self.confirm.setChecked(True)
        assert dialog_self.buttons.button(QDialogButtonBox.Ok).isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(BackupConfirmationDialog, "exec", run_backup_exec)
    window._start_backup()
    wait_idle()
    assert window._current_backup_id is not None
    assert not window.backup_button.isEnabled()
    assert window.deploy_button.isEnabled()

    # 5) Deployment approval and execution
    def deploy_exec(dialog_self):
        dialog_self.health_check.setChecked(False)
        dialog_self.destination_check.setChecked(True)
        dialog_self.changes_check.setChecked(True)
        dialog_self.backup_check.setChecked(True)
        dialog_self.domain_edit.setText("example.com")
        assert dialog_self.apply_button.isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(DeploymentApprovalDialog, "exec", deploy_exec)
    window._start_deployment()
    wait_idle()

    assert window._current_plan_id is None
    assert not window.revalidate_button.isEnabled()
    assert not window.backup_button.isEnabled()
    assert not window.deploy_button.isEnabled()
    assert fake_sftp.files[f"{remote_root}/index.html"] == b"<h1>updated</h1>"
    assert fake_sftp.files[f"{remote_root}/new.html"] == b"<p>added</p>"
    window.close()


def test_refresh_ready_plan_updates_summary_when_validated_plan_expires(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timedelta, timezone
    from dxsync.models import SyncPlan

    app()
    local_dir = tmp_path / "site"
    local_dir.mkdir()
    key_file = tmp_path / "id_ed25519"
    key_file.write_text("dummy", encoding="utf-8")
    mapping = SyncMapping.create("public", str(local_dir), "/home/user/site")
    project = Project.create(
        name="Static Site",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="sample.xsrv.jp",
        ssh_username="sample",
        host_key_fingerprint="SHA256:test",
        private_key_reference=str(key_file),
        mappings=(mapping,),
    )
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.save(project)

    now = datetime.now(timezone.utc)
    plan = SyncPlan(
        plan_id="plan-expiring",
        mapping_id=mapping.mapping_id,
        created_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
        changes=(),
        local_snapshot_sha256="a",
        remote_snapshot_sha256="b",
        config_sha256="c",
    )
    store.save_plan(project.project_id, plan, str(tmp_path / "staging"))

    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window._current_plan_id = plan.plan_id
    window._validated_plan_id = plan.plan_id
    window._refresh_ready_plan()

    assert window._current_plan_id is None
    assert window._validated_plan_id is None
    assert not window.revalidate_button.isEnabled()
    assert not window.backup_button.isEnabled()
    assert not window.deploy_button.isEnabled()
    assert "計画の有効期限が切れました" in window.summary.text()
    window.close()


def test_passphrase_session_cache_and_opt_in_keyring(tmp_path: Path) -> None:
    app()
    secure_store = MemoryHostKeyStore()
    store = ProjectStore(tmp_path / "state.db", secure_store)
    mapping_a = SyncMapping.create("a", str(tmp_path), "/home/user/a")
    project_a = Project.create(
        name="Alpha",
        site_type=SiteType.STATIC_SITE,
        domain="a.example.com",
        ssh_host="sv1.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:aaa",
        private_key_reference="/tmp/key_a",
        mappings=(mapping_a,),
    )
    mapping_b = SyncMapping.create("b", str(tmp_path), "/home/user/b")
    project_b = Project.create(
        name="Beta",
        site_type=SiteType.STATIC_SITE,
        domain="b.example.com",
        ssh_host="sv1.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:bbb",
        private_key_reference="/tmp/key_b",
        mappings=(mapping_b,),
    )
    store.save(project_a)
    store.save(project_b)

    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    idx_a = window.project_combo.findData(project_a.project_id)
    idx_b = window.project_combo.findData(project_b.project_id)

    window.project_combo.setCurrentIndex(idx_a)
    assert not window.save_passphrase_check.isChecked()
    assert window.save_passphrase_check.isEnabled()

    window.passphrase_edit.setText("alpha-session")
    assert window._resolve_gui_passphrase() == "alpha-session"
    assert not store.has_saved_ssh_passphrase(project_a.project_id)

    # Switch to Beta and back to Alpha: session cache restores Alpha without saving to keyring
    window.project_combo.setCurrentIndex(idx_b)
    assert window.passphrase_edit.text() == ""
    window.project_combo.setCurrentIndex(idx_a)
    assert window.passphrase_edit.text() == "alpha-session"
    assert not window.save_passphrase_check.isChecked()

    # Opt in to OS credential store
    window.save_passphrase_check.setChecked(True)
    assert store.has_saved_ssh_passphrase(project_a.project_id)
    assert store.get_ssh_passphrase(project_a.project_id) == "alpha-session"

    # Re-open window: persisted passphrase loads automatically when opted in
    window.close()
    assert window._session_passphrases == {}

    window2 = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window2.project_combo.setCurrentIndex(window2.project_combo.findData(project_a.project_id))
    assert window2.save_passphrase_check.isChecked()
    assert window2.passphrase_edit.text() == "alpha-session"

    # Opt out deletes from OS credential store
    window2.save_passphrase_check.setChecked(False)
    assert not store.has_saved_ssh_passphrase(project_a.project_id)
    assert store.get_ssh_passphrase(project_a.project_id) is None
    window2.close()


def test_history_dialog_retention_selection_and_backup_delete_handler(
    tmp_path: Path, monkeypatch
) -> None:
    from datetime import datetime, timedelta, timezone
    from dxsync.history_dialog import HistoryDialog
    from dxsync.storage import BackupRecord

    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    mapping = SyncMapping.create("main", str(tmp_path), "/home/user/site")
    project = Project.create(
        name="Static Site",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="sv1.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:aaa",
        private_key_reference="/tmp/key",
        mappings=(mapping,),
    )
    store.save(project)

    now = datetime.now(timezone.utc)
    recent_bk = BackupRecord(
        "bk-recent",
        project.project_id,
        mapping.mapping_id,
        "plan-1",
        "/home/user/.dxsync/backups/p/bk-recent",
        "/home/user/.dxsync/backups/p/bk-recent/manifest.json",
        now - timedelta(days=2),
        1,
        100,
    )
    old_bk = BackupRecord(
        "bk-old",
        project.project_id,
        mapping.mapping_id,
        "plan-2",
        "/home/user/.dxsync/backups/p/bk-old",
        "/home/user/.dxsync/backups/p/bk-old/manifest.json",
        now - timedelta(days=45),
        2,
        200,
    )
    store.save_backup(recent_bk)
    store.save_backup(old_bk)

    dialog = HistoryDialog(store, project.project_id)
    assert dialog.backups_table.rowCount() == 2
    assert not dialog.delete_backups_button.isEnabled()

    dialog.keep_latest_spin.setValue(1)
    dialog.max_age_days_spin.setValue(30)
    dialog.select_prune_button.click()

    assert dialog.checked_delete_backup_ids() == ("bk-old",)
    assert dialog.delete_backups_button.isEnabled()

    monkeypatch.setattr(
        "dxsync.history_dialog.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    dialog.delete_backups_button.click()
    assert dialog.selected_delete_backup_ids == ("bk-old",)
    assert dialog.result() == QDialog.Accepted

    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window._current_backup_id = "bk-old"
    window._backup_delete_completed((("bk-old",), ()))
    assert store.get_backup("bk-old").status == "deleted"
    assert window._current_backup_id is None
    assert "1件 削除しました" in window.summary.text()
    window.close()


def test_auto_sync_deployment_completed_stops_before_health_warning_modal(
    tmp_path: Path, monkeypatch
) -> None:
    from dxsync.deployment import DeploymentResult
    from dxsync.health import HealthCheckResult

    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    window = MainWindow(
        store,
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window._auto_sync_active = True
    window._auto_sync_stage = "deployment"
    window._backup_skipped = True
    window._current_plan_id = "plan-1"

    warnings: list[tuple[str, str]] = []

    def fake_warning(_parent, title: str, text: str):
        warnings.append((title, text))
        window._scan_finished()
        app().processEvents()
        return QMessageBox.Ok

    monkeypatch.setattr("dxsync.gui.QMessageBox.warning", fake_warning)

    result = DeploymentResult(
        plan_id="plan-1",
        remote_root="/home/user/example.com/public_html",
        files=(),
        total_bytes=0,
        health_check=HealthCheckResult(
            url="https://example.com/",
            ok=False,
            error="HTTP 500",
        ),
    )
    window._deployment_completed(result)

    assert not window._auto_sync_active
    assert len(warnings) == 1
    assert warnings[0][0] == "同期完了・動作確認失敗"
    assert "履歴からの復元はできません" in warnings[0][1]
    window.close()



