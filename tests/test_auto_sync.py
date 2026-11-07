import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from dxsync.auto_sync import (
    AutoSyncApprovalDialog,
    AutoSyncPreferences,
    auto_sync_policy_issues,
    load_auto_sync_preferences,
    save_auto_sync_preferences,
)
from dxsync.gui import MainWindow
from dxsync.revalidation import RevalidationResult
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_auto_sync_preferences_round_trip_per_project(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    preferences = AutoSyncPreferences(
        backup_mode="add_only",
        remote_only_policy="stop",
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
    assert not always.resolved_backup_skip(0)
    assert skip.resolved_backup_skip(10)


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
