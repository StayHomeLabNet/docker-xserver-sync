from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .health import normalize_health_check_path


class BackupConfirmationDialog(QDialog):
    def __init__(
        self,
        *,
        update_count: int,
        backup_root: str,
        remote_only_paths: tuple[str, ...] = (),
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
        self.deletion_table = QTableWidget(0, 1)
        self.deletion_table.setHorizontalHeaderLabels(("削除候補（初期状態はすべて未選択）",))
        self.deletion_table.horizontalHeader().setStretchLastSection(True)
        self.deletion_table.setMaximumHeight(180)
        self.deletion_table.setVisible(bool(remote_only_paths))
        self.deletion_table.setRowCount(len(remote_only_paths))
        for row, path in enumerate(remote_only_paths):
            item = QTableWidgetItem(path)
            item.setFlags((item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            item.setCheckState(Qt.Unchecked)
            self.deletion_table.setItem(row, 0, item)
        if remote_only_paths:
            deletion_note = QLabel(
                "削除するファイルだけを選択してください。選択分は先にバックアップされ、"
                "最終承認後にのみ公開領域から削除されます。"
            )
            deletion_note.setWordWrap(True)
            layout.addWidget(deletion_note)
        layout.addWidget(self.deletion_table)
        self.confirm = QCheckBox("XServerの専用バックアップ領域への書き込みを許可します")
        layout.addWidget(self.confirm)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("バックアップを作成")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.confirm.toggled.connect(self.buttons.button(QDialogButtonBox.Ok).setEnabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def selected_delete_paths(self) -> tuple[str, ...]:
        return tuple(
            item.text()
            for row in range(self.deletion_table.rowCount())
            if (item := self.deletion_table.item(row, 0)) is not None
            and item.checkState() == Qt.Checked
        )


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
        delete_count: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.domain = domain
        self.delete_count = delete_count
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
        summary.addRow("承認済み削除", QLabel(f"{delete_count}件"))
        self.health_check = QCheckBox("同期後にHTTPSで動作確認する")
        self.health_check.setChecked(True)
        self.health_path_edit = QLineEdit("/")
        self.health_path_edit.setPlaceholderText("/health.php または /")
        summary.addRow(self.health_check, self.health_path_edit)
        layout.addLayout(summary)

        self.destination_check = QCheckBox("転送先ドメインとディレクトリを確認しました")
        self.changes_check = QCheckBox("追加・更新されるファイル一覧を確認しました")
        self.backup_check = QCheckBox("バックアップが作成済みであることを確認しました")
        self.delete_check = QCheckBox(f"選択した{delete_count}件を削除することを確認しました")
        self.delete_check.setVisible(delete_count > 0)
        layout.addWidget(self.destination_check)
        layout.addWidget(self.changes_check)
        layout.addWidget(self.backup_check)
        layout.addWidget(self.delete_check)

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
            self.delete_check,
        ):
            checkbox.toggled.connect(self._update_enabled)
        self.domain_edit.textChanged.connect(self._update_enabled)
        self.health_check.toggled.connect(self._update_enabled)
        self.health_check.toggled.connect(self.health_path_edit.setEnabled)
        self.health_path_edit.textChanged.connect(self._update_enabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _update_enabled(self) -> None:
        health_path_valid = True
        if self.health_check.isChecked():
            try:
                normalize_health_check_path(self.health_path_edit.text())
            except ValueError:
                health_path_valid = False
        self.apply_button.setEnabled(
            self.destination_check.isChecked()
            and self.changes_check.isChecked()
            and self.backup_check.isChecked()
            and self.domain_edit.text().strip() == self.domain
            and health_path_valid
            and (self.delete_count == 0 or self.delete_check.isChecked())
        )

    def selected_health_check_path(self) -> str | None:
        if not self.health_check.isChecked():
            return None
        return normalize_health_check_path(self.health_path_edit.text())


class RestoreApprovalDialog(QDialog):
    def __init__(
        self,
        *,
        domain: str,
        remote_root: str,
        backup_id: str,
        file_count: int,
        total_bytes: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.domain = domain
        self.setWindowTitle("バックアップから復元")
        layout = QVBoxLayout(self)
        warning = QLabel(
            "復元すると、対象ファイルを検証済みバックアップの内容へ戻します。"
            "現在ファイルが同期完了時から変更されている場合は実行しません。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("font-weight: bold;")
        layout.addWidget(warning)
        summary = QFormLayout()
        summary.addRow("対象ドメイン", QLabel(domain))
        summary.addRow("復元先", QLabel(remote_root))
        summary.addRow("バックアップID", QLabel(backup_id))
        summary.addRow("ファイル数", QLabel(str(file_count)))
        summary.addRow("サイズ", QLabel(f"{total_bytes:,} bytes"))
        layout.addLayout(summary)
        self.confirm = QCheckBox("現在ファイルをバックアップ内容へ戻すことを確認しました")
        layout.addWidget(self.confirm)
        layout.addWidget(QLabel(f"確認のため「{domain}」と入力してください。"))
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText(domain)
        layout.addWidget(self.domain_edit)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.restore_button = self.buttons.button(QDialogButtonBox.Ok)
        self.restore_button.setText("承認して復元")
        self.restore_button.setEnabled(False)
        self.confirm.toggled.connect(self._update_enabled)
        self.domain_edit.textChanged.connect(self._update_enabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _update_enabled(self) -> None:
        self.restore_button.setEnabled(
            self.confirm.isChecked() and self.domain_edit.text().strip() == self.domain
        )
