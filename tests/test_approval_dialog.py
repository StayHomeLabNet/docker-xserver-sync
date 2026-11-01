import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox

from dxsync.approval_dialog import (
    BackupConfirmationDialog,
    DeploymentApprovalDialog,
    RestoreApprovalDialog,
)


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_backup_confirmation_requires_checkbox() -> None:
    app()
    dialog = BackupConfirmationDialog(
        update_count=2,
        backup_root="/home/user/.dxsync/backups",
    )
    button = dialog.buttons.button(QDialogButtonBox.Ok)

    assert not button.isEnabled()
    dialog.confirm.setChecked(True)
    assert button.isEnabled()


def test_deployment_approval_requires_all_checks_and_exact_domain() -> None:
    app()
    dialog = DeploymentApprovalDialog(
        domain="example.com",
        remote_root="/home/user/example.com/public_html/test",
        add_count=1,
        update_count=2,
        upload_bytes=123,
        backup_id="backup-1",
    )

    dialog.destination_check.setChecked(True)
    dialog.changes_check.setChecked(True)
    dialog.backup_check.setChecked(True)
    dialog.domain_edit.setText("wrong.example.com")
    assert not dialog.apply_button.isEnabled()
    dialog.domain_edit.setText("example.com")
    assert dialog.apply_button.isEnabled()
    dialog.backup_check.setChecked(False)
    assert not dialog.apply_button.isEnabled()


def test_restore_approval_requires_confirmation_and_exact_domain() -> None:
    app()
    dialog = RestoreApprovalDialog(
        domain="example.com",
        remote_root="/home/user/site",
        backup_id="backup-1",
        file_count=2,
        total_bytes=123,
    )
    dialog.confirm.setChecked(True)
    dialog.domain_edit.setText("wrong.example.com")
    assert not dialog.restore_button.isEnabled()
    dialog.domain_edit.setText("example.com")
    assert dialog.restore_button.isEnabled()
