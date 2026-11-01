from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class BackupConfirmationDialog(QDialog):
    def __init__(
        self,
        *,
        update_count: int,
        backup_root: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("バックアップを作成")
        layout = QVBoxLayout(self)
        message = QLabel(
            f"上書き対象 {update_count}件を、公開領域とは別の次の場所へ退避します。\n\n"
            f"{backup_root}\n\n"
            "公開ファイルはまだ変更しません。"
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        self.confirm = QCheckBox("XServerの専用バックアップ領域への書き込みを許可します")
        layout.addWidget(self.confirm)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("バックアップを作成")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.confirm.toggled.connect(self.buttons.button(QDialogButtonBox.Ok).setEnabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)


class DeploymentApprovalDialog(QDialog):
    def __init__(
        self,
        *,
        domain: str,
        remote_root: str,
        add_count: int,
        update_count: int,
        upload_bytes: int,
        backup_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.domain = domain
        self.setWindowTitle("本番同期の最終確認")
        self.resize(620, 430)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "この操作はXServerの指定ディレクトリへ実際にファイルを書き込みます。"
            "計画をもう一度再検証してから、一時ファイル転送と安全な切り替えを行います。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("font-weight: bold;")
        layout.addWidget(warning)

        summary = QFormLayout()
        summary.addRow("対象ドメイン", QLabel(domain))
        summary.addRow("転送先", QLabel(remote_root))
        summary.addRow("追加", QLabel(f"{add_count}件"))
        summary.addRow("更新", QLabel(f"{update_count}件"))
        summary.addRow("転送量", QLabel(f"{upload_bytes:,} bytes"))
        summary.addRow("バックアップID", QLabel(backup_id))
        layout.addLayout(summary)

        self.destination_check = QCheckBox("転送先ドメインとディレクトリを確認しました")
        self.changes_check = QCheckBox("追加・更新されるファイル一覧を確認しました")
        self.backup_check = QCheckBox("バックアップが作成済みであることを確認しました")
        layout.addWidget(self.destination_check)
        layout.addWidget(self.changes_check)
        layout.addWidget(self.backup_check)

        domain_help = QLabel(f"確認のため「{domain}」と入力してください。")
        layout.addWidget(domain_help)
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText(domain)
        layout.addWidget(self.domain_edit)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("承認して同期を実行")
        self.apply_button.setEnabled(False)
        for checkbox in (
            self.destination_check,
            self.changes_check,
            self.backup_check,
        ):
            checkbox.toggled.connect(self._update_enabled)
        self.domain_edit.textChanged.connect(self._update_enabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _update_enabled(self) -> None:
        self.apply_button.setEnabled(
            self.destination_check.isChecked()
            and self.changes_check.isChecked()
            and self.backup_check.isChecked()
            and self.domain_edit.text().strip() == self.domain
        )
