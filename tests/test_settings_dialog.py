import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import timedelta

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit

import dxsync.gui as gui_module
from dxsync.gui import DryRunWorker, MainWindow
from dxsync.models import Project, ScanResult, SiteType, SyncMapping
from dxsync.planner import PlanBuilder
from dxsync.remote import RemoteScanResult
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.service import DryRunResult
from dxsync.settings_dialog import (
    MaskSettingsDialog,
    PlanValiditySettingsDialog,
    load_plan_validity_minutes,
    save_plan_validity_minutes,
)
from dxsync.storage import ProjectStore


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_mask_settings_returns_only_boolean_preferences() -> None:
    app()
    dialog = MaskSettingsDialog({"host": False, "username": True})

    values = dialog.selected_values()

    assert values["host"] is False
    assert values["username"] is True
    assert set(values) == {
        "host",
        "username",
        "remote_root",
        "fingerprint",
        "private_key",
    }


def test_main_window_masks_configured_fields_and_can_temporarily_reveal(
    tmp_path: Path,
) -> None:
    app()
    secure_store = MemoryHostKeyStore()
    store = ProjectStore(tmp_path / "state.db", secure_store)
    mapping = SyncMapping.create("public", str(tmp_path), "/home/user/site")
    project = Project.create(
        name="Example",
        site_type=SiteType.CUSTOM_PHP,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/private-key",
        mappings=(mapping,),
    )
    store.save(project)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    settings.setValue("display_mask/host", False)

    window = MainWindow(store, settings)

    assert window.host_edit.echoMode() == QLineEdit.Normal
    assert window.user_edit.echoMode() == QLineEdit.Password
    assert window.fingerprint_edit.echoMode() == QLineEdit.Password
    assert window.passphrase_edit.echoMode() == QLineEdit.Password

    window.mask_toggle.setChecked(True)
    assert window.user_edit.echoMode() == QLineEdit.Normal
    assert window.fingerprint_edit.echoMode() == QLineEdit.Normal
    assert window.passphrase_edit.echoMode() == QLineEdit.Normal
    window.close()


def test_plan_validity_settings_dialog_presets_custom_and_reset() -> None:
    app()
    dialog = PlanValiditySettingsDialog(15)

    assert dialog.selected_minutes() == 15
    assert dialog.minutes_spin.minimum() == 1
    assert dialog.minutes_spin.maximum() == 60
    assert dialog.preset_combo.currentData() == 15

    # Select 60-minute maximum preset from dropdown
    idx_60 = dialog.preset_combo.findData(60)
    assert idx_60 >= 0
    dialog.preset_combo.setCurrentIndex(idx_60)
    assert dialog.minutes_spin.value() == 60
    assert dialog.selected_minutes() == 60

    # Custom spinbox value switches preset combo to custom
    dialog.minutes_spin.setValue(25)
    assert dialog.preset_combo.currentData() is None
    assert dialog.selected_minutes() == 25

    # Out-of-range values are clamped by spinbox
    dialog.minutes_spin.setValue(99)
    assert dialog.selected_minutes() == 60

    # Restore defaults resets to 15
    restore_button = dialog.findChild(QDialogButtonBox).button(
        QDialogButtonBox.RestoreDefaults
    )
    restore_button.click()
    assert dialog.selected_minutes() == 15
    assert dialog.preset_combo.currentData() == 15


def test_plan_validity_settings_persistence_and_clamping(tmp_path: Path) -> None:
    app()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)

    assert load_plan_validity_minutes(settings) == 15

    assert save_plan_validity_minutes(settings, 45) == 45
    assert load_plan_validity_minutes(settings) == 45

    # Tampered/out-of-range persisted value is safely clamped
    settings.setValue("plan/validity_minutes", 120)
    assert load_plan_validity_minutes(settings) == 60
    settings.setValue("plan/validity_minutes", "invalid")
    assert load_plan_validity_minutes(settings) == 15


def test_main_window_applies_configured_plan_validity_and_displays_minutes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app()
    key_file = tmp_path / "id_ed25519"
    key_file.write_text("dummy-key", encoding="utf-8")
    local_dir = tmp_path / "site"
    local_dir.mkdir()

    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    mapping = SyncMapping.create("public", str(local_dir), "/home/user/site")
    project = Project.create(
        name="Example",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:test",
        private_key_reference=str(key_file),
        mappings=(mapping,),
    )
    store.save(project)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    save_plan_validity_minutes(settings, 60)

    window = MainWindow(store, settings)
    try:
        assert window.plan_validity_action.text() == "計画の有効時間設定…"
        assert window._plan_validity_minutes() == 60

        # Verify _start_dry_run passes 60 minutes to DryRunWorker without running threads
        captured_workers: list[DryRunWorker] = []

        class DummyThread:
            def __init__(self, _parent=None) -> None:
                self.started = self
                self.finished = self

            def connect(self, _slot) -> None:
                pass

            def start(self) -> None:
                pass

            def quit(self) -> None:
                pass

        monkeypatch.setattr(gui_module, "QThread", DummyThread)
        original_init = DryRunWorker.__init__

        def spy_init(self, **kwargs):
            original_init(self, **kwargs)
            captured_workers.append(self)

        monkeypatch.setattr(DryRunWorker, "__init__", spy_init)
        monkeypatch.setattr(DryRunWorker, "moveToThread", lambda self, _thread: None)

        window._start_dry_run()
        assert len(captured_workers) == 1
        assert captured_workers[0].plan_validity == timedelta(minutes=60)
        window._thread = None
        window._worker = None
        window._dry_run_progress_timer.stop()

        # Verify _dry_run_completed shows actual validity minutes in summary
        local_scan = ScanResult(root=local_dir, files=(), issues=(), excluded_count=0)
        remote_scan = RemoteScanResult(
            root="/home/user/site", files=(), issues=(), excluded_count=0
        )
        plan = PlanBuilder().build(
            mapping_id=mapping.mapping_id,
            local=local_scan,
            remote_records=(),
            validity=timedelta(minutes=60),
        )
        window._dry_run_completed(
            DryRunResult(local=local_scan, remote=remote_scan, plan=plan)
        )
        assert "計画保存済み（60分間有効）" in window.summary.text()
    finally:
        window.close()
