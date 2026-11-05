import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLineEdit

from dxsync.gui import MainWindow
from dxsync.models import Project, SiteType, SyncMapping
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.settings_dialog import MaskSettingsDialog
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
