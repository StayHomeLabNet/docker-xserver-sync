import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog

from dxsync.auto_sync import (
    AutoSyncApprovalDialog,
    AutoSyncPreferences,
    AutoSyncSettingsDialog,
    auto_sync_policy_issues,
    load_auto_sync_preferences,
    save_auto_sync_preferences,
)
from dxsync.gui import MainWindow
from dxsync.models import (
    ChangeType,
    EntryKind,
    FileChange,
    FileRecord,
    Project,
    ScanResult,
    SiteType,
    SyncMapping,
    SyncPlan,
)
from dxsync.remote import RemoteScanResult
from dxsync.service import DryRunResult
from dxsync.revalidation import RevalidationResult
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_auto_sync_preferences_round_trip_per_project(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    preferences = AutoSyncPreferences(
        backup_mode="add_only",
        remote_only_policy="ask",
        health_check_enabled=True,
        health_check_path="/health.php",
        max_changed_files=25,
        max_update_files=10,
        max_upload_mib=50,
        require_domain_confirmation=False,
    )

    save_auto_sync_preferences(settings, "project-one", preferences)

    assert load_auto_sync_preferences(settings, "project-one") == preferences
    assert load_auto_sync_preferences(settings, "project-two") == AutoSyncPreferences()


def test_auto_sync_backup_skip_policy() -> None:
    add_only = AutoSyncPreferences(backup_mode="add_only")
    always = AutoSyncPreferences(backup_mode="always")
    skip = AutoSyncPreferences(backup_mode="skip")

    assert add_only.resolved_backup_skip(0)
    assert not add_only.resolved_backup_skip(1)
    assert not add_only.resolved_backup_skip(0, delete_count=1)
    assert not always.resolved_backup_skip(0)
    assert skip.resolved_backup_skip(10, delete_count=2)


def test_auto_sync_settings_dialog_includes_ask_remote_only_policy() -> None:
    app()
    dialog = AutoSyncSettingsDialog(AutoSyncPreferences(remote_only_policy="ask"))
    assert dialog.selected_preferences().remote_only_policy == "ask"


def test_auto_sync_policy_stops_at_configured_limits() -> None:
    preferences = AutoSyncPreferences(
        remote_only_policy="stop",
        max_changed_files=2,
        max_update_files=1,
        max_upload_mib=1,
    )

    issues = auto_sync_policy_issues(
        preferences,
        add_count=2,
        update_count=2,
        remote_only_count=1,
        upload_bytes=2 * 1024 * 1024,
    )

    assert len(issues) == 4
    assert any("REMOTE_ONLY" in issue for issue in issues)


def test_auto_sync_approval_requires_checks_and_exact_domain() -> None:
    app()
    dialog = AutoSyncApprovalDialog(
        domain="example.com",
        remote_root="/home/user/site",
        add_count=1,
        update_count=2,
        remote_only_count=3,
        upload_bytes=123,
        backup_skipped=False,
        health_check_path="/",
    )

    dialog.destination_check.setChecked(True)
    dialog.workflow_check.setChecked(True)
    dialog.domain_edit.setText("wrong.example.com")
    assert not dialog.apply_button.isEnabled()
    dialog.domain_edit.setText("example.com")
    assert dialog.apply_button.isEnabled()


def test_auto_sync_approval_can_skip_domain_reentry() -> None:
    app()
    dialog = AutoSyncApprovalDialog(
        domain="example.com",
        remote_root="/home/user/site",
        add_count=1,
        update_count=0,
        remote_only_count=0,
        upload_bytes=123,
        backup_skipped=False,
        health_check_path="/",
        require_domain_confirmation=False,
    )

    assert dialog.domain_edit.isHidden()
    dialog.destination_check.setChecked(True)
    assert not dialog.apply_button.isEnabled()
    dialog.workflow_check.setChecked(True)
    assert dialog.apply_button.isEnabled()


def test_auto_sync_approval_remote_only_selection_respects_preserved_folders_and_backup() -> None:
    app()
    dialog = AutoSyncApprovalDialog(
        domain="example.com",
        remote_root="/home/user/site",
        add_count=0,
        update_count=0,
        remote_only_count=2,
        upload_bytes=0,
        backup_skipped=True,
        health_check_path="/",
        require_domain_confirmation=False,
        remote_only_paths=("assets/old.js", "uploads/keep.jpg"),
        allow_remote_only_deletion=True,
        backup_mode="add_only",
    )

    dialog.destination_check.setChecked(True)
    dialog.workflow_check.setChecked(True)
    assert dialog.selected_delete_paths() == ()
    assert dialog.should_skip_backup()
    assert not dialog.apply_button.isEnabled()

    dialog.deletion_table.item(0, 0).setCheckState(Qt.Checked)
    dialog.deletion_table.item(1, 0).setCheckState(Qt.Checked)
    assert not dialog.should_skip_backup()
    assert not dialog.delete_check.isHidden()
    assert not dialog.apply_button.isEnabled()

    dialog.preserve_table.item(1, 0).setCheckState(Qt.Checked)
    assert dialog.selected_delete_paths() == ("assets/old.js",)
    assert dialog.deletion_table.item(1, 0).checkState() == Qt.Unchecked

    dialog.delete_check.setChecked(True)
    assert dialog.apply_button.isEnabled()


def test_auto_sync_approval_blocks_deletion_when_backup_skipped() -> None:
    app()
    dialog = AutoSyncApprovalDialog(
        domain="example.com",
        remote_root="/home/user/site",
        add_count=1,
        update_count=0,
        remote_only_count=1,
        upload_bytes=10,
        backup_skipped=True,
        health_check_path="/",
        require_domain_confirmation=False,
        remote_only_paths=("assets/old.js",),
        allow_remote_only_deletion=True,
        backup_mode="skip",
    )

    assert dialog.deletion_table.isHidden()
    assert dialog.selected_delete_paths() == ()
    assert dialog.should_skip_backup()


def test_prepare_auto_after_dry_run_propagates_selected_deletions_and_backup(
    tmp_path: Path, monkeypatch
) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    project = Project(
        project_id="proj-1",
        name="Static Site",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="sv1.example.com",
        ssh_port=10022,
        ssh_username="user",
        host_key_fingerprint="SHA256:abc",
        private_key_reference=str(tmp_path / "id_ed25519"),
        mappings=(
            SyncMapping(
                mapping_id="map-1",
                name="main",
                local_root=str(tmp_path / "src"),
                remote_root="/home/user/example.com/public_html",
            ),
        ),
    )
    store.save(project)
    window = MainWindow(store, settings)
    now = datetime.now(UTC)
    remote_record = FileRecord(
        relative_path="assets/old.js",
        kind=EntryKind.FILE,
        size=12,
        modified_ns=1_000_000_000,
        sha256="remote-hash",
    )
    plan = SyncPlan(
        plan_id="plan-ask-1",
        mapping_id="map-1",
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        changes=(
            FileChange(
                relative_path="assets/old.js",
                change_type=ChangeType.REMOTE_ONLY,
                local=None,
                remote=remote_record,
            ),
        ),
        local_snapshot_sha256="local-snap",
        remote_snapshot_sha256="remote-snap",
        config_sha256="cfg",
    )
    store.save_plan(project.project_id, plan, str(tmp_path / "staging"))
    window._current_plan_id = plan.plan_id
    window._auto_sync_active = True
    window._auto_sync_stage = "approval"
    window._auto_sync_preferences = AutoSyncPreferences(
        backup_mode="add_only",
        remote_only_policy="ask",
        require_domain_confirmation=False,
    )

    class FakeApprovalDialog:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def exec(self) -> int:
            return QDialog.Accepted

        def selected_delete_paths(self) -> tuple[str, ...]:
            return ("assets/old.js",)

        def should_skip_backup(self) -> bool:
            return False

    monkeypatch.setattr("dxsync.gui.AutoSyncApprovalDialog", FakeApprovalDialog)

    window._prepare_auto_after_dry_run(
        DryRunResult(
            local=ScanResult(root=tmp_path / "src", files=(), issues=(), excluded_count=0),
            remote=RemoteScanResult(
                root="/home/user/example.com/public_html",
                files=(remote_record,),
                issues=(),
                excluded_count=0,
            ),
            plan=plan,
        )
    )

    assert window._auto_sync_stage == "revalidation"
    assert window._auto_approved_delete_paths == ("assets/old.js",)
    assert window._approved_delete_paths == ("assets/old.js",)
    assert not window._auto_backup_skipped

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        window,
        "_run_backup",
        lambda _p, _m, _s: captured.setdefault("delete_paths", window._approved_delete_paths),
    )
    window._start_auto_backup()
    assert captured["delete_paths"] == ("assets/old.js",)

    window._stop_auto_sync()
    window.close()


def test_valid_revalidation_advances_auto_sync_to_configured_next_stage(
    tmp_path: Path,
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    window._auto_sync_active = True
    window._auto_sync_stage = "revalidation"
    window._current_plan_id = "plan-1"
    window._auto_backup_skipped = False

    window._revalidation_completed(RevalidationResult(True, ()))
    assert window._auto_sync_stage == "backup"

    window._auto_sync_stage = "revalidation"
    window._auto_backup_skipped = True
    window._revalidation_completed(RevalidationResult(True, ()))
    assert window._auto_sync_stage == "deployment"
    assert window._backup_skipped
    window._stop_auto_sync()
    window.close()


def test_auto_sync_settings_dialog_skip_backup_warning_and_selection() -> None:
    app()
    dialog = AutoSyncSettingsDialog(
        AutoSyncPreferences(backup_mode="skip", remote_only_policy="ask")
    )
    assert dialog.selected_preferences().backup_mode == "skip"
    assert "常にスキップ（復元不可）" in dialog.warning_label.text()
    assert "REMOTE_ONLY削除は無効" in dialog.warning_label.text()


def test_auto_sync_approval_can_toggle_skip_backup_and_save_preference() -> None:
    app()
    dialog = AutoSyncApprovalDialog(
        domain="example.com",
        remote_root="/home/user/site",
        add_count=1,
        update_count=2,
        remote_only_count=1,
        upload_bytes=200,
        backup_skipped=False,
        health_check_path="/",
        require_domain_confirmation=False,
        remote_only_paths=("assets/old.js",),
        allow_remote_only_deletion=True,
        backup_mode="always",
    )

    assert not dialog.should_skip_backup()
    assert not dialog.should_save_skip_backup_preference()
    assert dialog.save_skip_backup_check.isHidden()

    dialog.skip_backup_check.setChecked(True)
    assert dialog.should_skip_backup()
    assert not dialog.save_skip_backup_check.isHidden()
    assert "スキップ" in dialog.backup_label.text()

    dialog.save_skip_backup_check.setChecked(True)
    assert dialog.should_save_skip_backup_preference()

    dialog.deletion_table.item(0, 0).setCheckState(Qt.Checked)
    assert not dialog.skip_backup_check.isEnabled()
    assert not dialog.should_skip_backup()
    assert not dialog.should_save_skip_backup_preference()
    assert "作成する" in dialog.backup_label.text()

    dialog.deletion_table.item(0, 0).setCheckState(Qt.Unchecked)
    assert dialog.skip_backup_check.isEnabled()
    assert not dialog.should_skip_backup()

    dialog.skip_backup_check.setChecked(True)
    assert dialog.should_skip_backup()


def test_auto_sync_approval_skip_mode_allows_one_off_backup_override() -> None:
    app()
    dialog = AutoSyncApprovalDialog(
        domain="example.com",
        remote_root="/home/user/site",
        add_count=1,
        update_count=1,
        remote_only_count=0,
        upload_bytes=100,
        backup_skipped=True,
        health_check_path=None,
        require_domain_confirmation=False,
        backup_mode="skip",
    )

    assert dialog.should_skip_backup()
    assert dialog.save_skip_backup_check.isHidden()
    dialog.skip_backup_check.setChecked(False)
    assert not dialog.should_skip_backup()
    assert "作成する" in dialog.backup_label.text()


def test_main_window_auto_sync_settings_button_and_summary_update(
    tmp_path: Path, monkeypatch
) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    project = Project(
        project_id="proj-ui-1",
        name="Static Site",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="sv1.example.com",
        ssh_port=10022,
        ssh_username="user",
        host_key_fingerprint="SHA256:abc",
        private_key_reference=str(tmp_path / "id_ed25519"),
        mappings=(
            SyncMapping(
                mapping_id="map-1",
                name="main",
                local_root=str(tmp_path / "src"),
                remote_root="/home/user/example.com/public_html",
            ),
        ),
    )
    store.save(project)
    window = MainWindow(store, settings)

    assert window.auto_sync_settings_button.isEnabled()
    assert "必ず作成" in window.auto_sync_policy_label.text()

    class FakeSettingsDialog:
        def __init__(self, _prefs, _parent=None) -> None:
            pass

        def exec(self) -> int:
            return QDialog.Accepted

        def selected_preferences(self) -> AutoSyncPreferences:
            return AutoSyncPreferences(backup_mode="skip", remote_only_policy="keep")

    monkeypatch.setattr("dxsync.gui.AutoSyncSettingsDialog", FakeSettingsDialog)
    monkeypatch.setattr("dxsync.gui.QMessageBox.information", lambda *a, **kw: None)

    window.auto_sync_settings_button.click()

    assert load_auto_sync_preferences(settings, project.project_id).backup_mode == "skip"
    assert "常にスキップ（復元不可）" in window.auto_sync_policy_label.text()
    window.close()


def test_main_window_auto_sync_skip_backup_saves_pref_and_deploys_without_backup(
    tmp_path: Path, monkeypatch
) -> None:
    app()
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    project = Project(
        project_id="proj-skip-1",
        name="Static Site",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="sv1.example.com",
        ssh_port=10022,
        ssh_username="user",
        host_key_fingerprint="SHA256:abc",
        private_key_reference=str(tmp_path / "id_ed25519"),
        mappings=(
            SyncMapping(
                mapping_id="map-1",
                name="main",
                local_root=str(tmp_path / "src"),
                remote_root="/home/user/example.com/public_html",
            ),
        ),
    )
    store.save(project)
    window = MainWindow(store, settings)
    now = datetime.now(UTC)
    local_record = FileRecord(
        relative_path="index.html",
        kind=EntryKind.FILE,
        size=42,
        modified_ns=2_000_000_000,
        sha256="local-hash",
    )
    remote_record = FileRecord(
        relative_path="index.html",
        kind=EntryKind.FILE,
        size=40,
        modified_ns=1_000_000_000,
        sha256="remote-hash",
    )
    plan = SyncPlan(
        plan_id="plan-skip-1",
        mapping_id="map-1",
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        changes=(
            FileChange(
                relative_path="index.html",
                change_type=ChangeType.UPDATE,
                local=local_record,
                remote=remote_record,
            ),
        ),
        local_snapshot_sha256="local-snap",
        remote_snapshot_sha256="remote-snap",
        config_sha256="cfg",
    )
    store.save_plan(project.project_id, plan, str(tmp_path / "staging"))
    window._current_plan_id = plan.plan_id
    window._auto_sync_active = True
    window._auto_sync_stage = "approval"
    window._auto_sync_preferences = AutoSyncPreferences(
        backup_mode="always",
        remote_only_policy="keep",
        require_domain_confirmation=False,
    )

    class FakeApprovalDialog:
        def __init__(self, **_kwargs) -> None:
            pass

        def exec(self) -> int:
            return QDialog.Accepted

        def selected_delete_paths(self) -> tuple[str, ...]:
            return ()

        def should_skip_backup(self) -> bool:
            return True

        def should_save_skip_backup_preference(self) -> bool:
            return True

    monkeypatch.setattr("dxsync.gui.AutoSyncApprovalDialog", FakeApprovalDialog)

    window._prepare_auto_after_dry_run(
        DryRunResult(
            local=ScanResult(
                root=tmp_path / "src",
                files=(local_record,),
                issues=(),
                excluded_count=0,
            ),
            remote=RemoteScanResult(
                root="/home/user/example.com/public_html",
                files=(remote_record,),
                issues=(),
                excluded_count=0,
            ),
            plan=plan,
        )
    )

    assert load_auto_sync_preferences(settings, project.project_id).backup_mode == "skip"
    assert "常にスキップ（復元不可）" in window.auto_sync_policy_label.text()
    assert window._auto_sync_stage == "revalidation"
    assert window._auto_backup_skipped

    window._revalidation_completed(RevalidationResult(True, ()))
    assert window._auto_sync_stage == "deployment"
    assert window._backup_skipped

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        window,
        "_run_backup",
        lambda *_args: (_ for _ in ()).throw(AssertionError("_run_backup must not run")),
    )
    monkeypatch.setattr(
        window,
        "_run_deployment",
        lambda p, m, s, backup, health_path: captured.update(
            {
                "project": p,
                "mapping": m,
                "stored": s,
                "backup": backup,
                "health_path": health_path,
                "backup_skipped": window._backup_skipped,
                "delete_paths": window._approved_delete_paths,
            }
        ),
    )

    window._start_auto_deployment()
    assert captured["backup"] is None
    assert captured["backup_skipped"] is True
    assert captured["delete_paths"] == ()

    window._stop_auto_sync()
    window.close()
