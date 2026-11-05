from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
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
        preserved_folders = tuple(
            sorted(
                {
                    path.rsplit("/", 1)[0] + "/"
                    for path in remote_only_paths
                    if "/" in path
                }
            )
        )
        self.preserve_table = QTableWidget(len(preserved_folders), 1)
        self.preserve_table.setHorizontalHeaderLabels(("削除しないフォルダー",))
        self.preserve_table.horizontalHeader().setStretchLastSection(True)
        self.preserve_table.setMaximumHeight(140)
        self.preserve_table.setVisible(bool(preserved_folders))
        for row, path in enumerate(preserved_folders):
            item = QTableWidgetItem(path)
            item.setFlags((item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            item.setCheckState(Qt.Unchecked)
            self.preserve_table.setItem(row, 0, item)
        self.preserve_table.itemChanged.connect(self._preserved_folders_changed)
        if remote_only_paths:
            deletion_note = QLabel(
                "削除するファイルだけを選択してください。選択分は先にバックアップされ、"
                "最終承認後にのみ公開領域から削除されます。"
            )
            deletion_note.setWordWrap(True)
            layout.addWidget(deletion_note)
        layout.addWidget(self.deletion_table)
        if preserved_folders:
            preserve_note = QLabel(
                "チェックしたフォルダー以下は、上の削除候補で選択しても削除されません。"
            )
            preserve_note.setWordWrap(True)
            layout.addWidget(preserve_note)
        layout.addWidget(self.preserve_table)
        self.skip_backup = QCheckBox(
            "バックアップ作成をスキップする（上書き前の状態には復元できません）"
        )
        self.skip_backup.toggled.connect(self._skip_backup_changed)
        layout.addWidget(self.skip_backup)
        self.confirm = QCheckBox("XServerの専用バックアップ領域への書き込みを許可します")
        layout.addWidget(self.confirm)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("バックアップを作成")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.confirm.toggled.connect(self._update_enabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def selected_delete_paths(self) -> tuple[str, ...]:
        preserved = self.preserved_folder_paths()
        return tuple(
            item.text()
            for row in range(self.deletion_table.rowCount())
            if (item := self.deletion_table.item(row, 0)) is not None
            and item.checkState() == Qt.Checked
            and not any(item.text().startswith(folder) for folder in preserved)
        )

    def preserved_folder_paths(self) -> tuple[str, ...]:
        return tuple(
            item.text()
            for row in range(self.preserve_table.rowCount())
            if (item := self.preserve_table.item(row, 0)) is not None
            and item.checkState() == Qt.Checked
        )

    def should_skip_backup(self) -> bool:
        return self.skip_backup.isChecked()

    def _preserved_folders_changed(self, *_args) -> None:
        preserved = self.preserved_folder_paths()
        for row in range(self.deletion_table.rowCount()):
            item = self.deletion_table.item(row, 0)
            if item is None:
                continue
            blocked = any(item.text().startswith(folder) for folder in preserved)
            if blocked:
                item.setCheckState(Qt.Unchecked)
            flags = item.flags()
            item.setFlags(flags & ~Qt.ItemIsEnabled if blocked else flags | Qt.ItemIsEnabled)

    def _skip_backup_changed(self, skipped: bool) -> None:
        if skipped:
            for row in range(self.deletion_table.rowCount()):
                item = self.deletion_table.item(row, 0)
                if item is not None:
                    item.setCheckState(Qt.Unchecked)
            self.confirm.setText(
                "バックアップなしでは復元できないことを理解し、スキップを許可します"
            )
        else:
            self.confirm.setText("XServerの専用バックアップ領域への書き込みを許可します")
        self.deletion_table.setEnabled(not skipped)
        self.preserve_table.setEnabled(not skipped)
        self.buttons.button(QDialogButtonBox.Ok).setText(
            "バックアップをスキップ" if skipped else "バックアップを作成"
        )
        self.confirm.setChecked(False)
        self._update_enabled()

    def _update_enabled(self) -> None:
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self.confirm.isChecked())


class DeploymentApprovalDialog(QDialog):
    def __init__(
        self,
        *,
        domain: str,
        remote_root: str,
        add_count: int,
        update_count: int,
        upload_bytes: int,
        backup_id: str | None,
        backup_skipped: bool = False,
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
        summary.addRow(
            "バックアップID",
            QLabel(backup_id or "スキップ（復元不可）"),
        )
        summary.addRow("承認済み削除", QLabel(f"{delete_count}件"))
        self.health_check = QCheckBox("同期後にHTTPSで動作確認する")
        self.health_check.setChecked(True)
        self.health_path_edit = QLineEdit("/")
        self.health_path_edit.setPlaceholderText("/health.php または /")
        summary.addRow(self.health_check, self.health_path_edit)
        layout.addLayout(summary)

        self.destination_check = QCheckBox("転送先ドメインとディレクトリを確認しました")
        self.changes_check = QCheckBox("追加・更新されるファイル一覧を確認しました")
        self.backup_check = QCheckBox(
            "バックアップなしでは復元できないことを確認しました"
            if backup_skipped
            else "バックアップが作成済みであることを確認しました"
        )
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


class ForceOverwriteApprovalDialog(QDialog):
    def __init__(
        self,
        *,
        domain: str,
        remote_root: str,
        file_paths: tuple[str, ...],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.domain = domain
        self.setWindowTitle("無条件上書きの最終確認")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        warning = QLabel(
            "この操作は差分計画・リモート内容照合・バックアップ・転送後ハッシュ検証を行いません。"
            "ローカルの同期対象ファイルをXServer側へ無条件に上書きします。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("font-weight: bold; color: #b42318;")
        layout.addWidget(warning)
        summary = QFormLayout()
        summary.addRow("対象ドメイン", QLabel(domain))
        summary.addRow("転送先", QLabel(remote_root))
        summary.addRow("リモートのみのファイル", QLabel("削除せずそのまま残します"))
        layout.addLayout(summary)
        selection_note = QLabel(
            "上書きするファイルまたはフォルダーをチェックしてください。"
            "フォルダーのチェックを外すと、その配下もすべて対象外になります。"
        )
        selection_note.setWordWrap(True)
        layout.addWidget(selection_note)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(("上書き対象",))
        self.file_tree.setMinimumHeight(220)
        self._populate_file_tree(file_paths)
        self.file_tree.itemChanged.connect(self._selection_changed)
        self.file_tree.expandToDepth(1)
        layout.addWidget(self.file_tree)
        selection_buttons = QHBoxLayout()
        self.select_all_button = QPushButton("すべてチェック")
        self.clear_all_button = QPushButton("すべてチェック解除")
        self.select_all_button.clicked.connect(lambda: self._set_all(Qt.Checked))
        self.clear_all_button.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        selection_buttons.addWidget(self.select_all_button)
        selection_buttons.addWidget(self.clear_all_button)
        selection_buttons.addStretch(1)
        layout.addLayout(selection_buttons)
        self.selection_label = QLabel()
        layout.addWidget(self.selection_label)
        self.risk_check = QCheckBox(
            "バックアップと内容検証なしで上書きすることを理解しました"
        )
        self.destination_check = QCheckBox("転送先ドメインとディレクトリを確認しました")
        layout.addWidget(self.risk_check)
        layout.addWidget(self.destination_check)
        layout.addWidget(QLabel(f"確認のため「{domain}」と入力してください。"))
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText(domain)
        layout.addWidget(self.domain_edit)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("無条件上書きを実行")
        self.apply_button.setEnabled(False)
        self.risk_check.toggled.connect(self._update_enabled)
        self.destination_check.toggled.connect(self._update_enabled)
        self.domain_edit.textChanged.connect(self._update_enabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_selection_label()

    def _populate_file_tree(self, file_paths: tuple[str, ...]) -> None:
        folders: dict[str, QTreeWidgetItem] = {}
        for path in sorted(file_paths):
            parts = path.split("/")
            parent: QTreeWidgetItem | QTreeWidget = self.file_tree
            for index, part in enumerate(parts[:-1], start=1):
                folder_path = "/".join(parts[:index])
                folder = folders.get(folder_path)
                if folder is None:
                    folder = QTreeWidgetItem(parent, (part,))
                    folder.setFlags(
                        folder.flags()
                        | Qt.ItemIsUserCheckable
                        | Qt.ItemIsAutoTristate
                    )
                    folder.setCheckState(0, Qt.Checked)
                    folders[folder_path] = folder
                parent = folder
            file_item = QTreeWidgetItem(parent, (parts[-1],))
            file_item.setFlags(file_item.flags() | Qt.ItemIsUserCheckable)
            file_item.setData(0, Qt.UserRole, path)
            file_item.setCheckState(0, Qt.Checked)

    def _selection_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        self.file_tree.blockSignals(True)
        try:
            if item.childCount() and item.checkState(0) != Qt.PartiallyChecked:
                self._set_descendants(item, item.checkState(0))
            parent = item.parent()
            while parent is not None:
                states = {
                    parent.child(index).checkState(0)
                    for index in range(parent.childCount())
                }
                if states == {Qt.Checked}:
                    parent.setCheckState(0, Qt.Checked)
                elif states == {Qt.Unchecked}:
                    parent.setCheckState(0, Qt.Unchecked)
                else:
                    parent.setCheckState(0, Qt.PartiallyChecked)
                parent = parent.parent()
        finally:
            self.file_tree.blockSignals(False)
        self._update_selection_label()
        self._update_enabled()

    def _set_descendants(
        self, item: QTreeWidgetItem, state: Qt.CheckState
    ) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(0, state)
            self._set_descendants(child, state)

    def _set_all(self, state: Qt.CheckState) -> None:
        self.file_tree.blockSignals(True)
        try:
            for index in range(self.file_tree.topLevelItemCount()):
                item = self.file_tree.topLevelItem(index)
                item.setCheckState(0, state)
                self._set_descendants(item, state)
        finally:
            self.file_tree.blockSignals(False)
        self._update_selection_label()
        self._update_enabled()

    def selected_file_paths(self) -> tuple[str, ...]:
        selected: list[str] = []
        pending = [
            self.file_tree.topLevelItem(index)
            for index in range(self.file_tree.topLevelItemCount())
        ]
        while pending:
            item = pending.pop(0)
            path = item.data(0, Qt.UserRole)
            if path and item.checkState(0) == Qt.Checked:
                selected.append(str(path))
            pending[0:0] = [item.child(index) for index in range(item.childCount())]
        return tuple(sorted(selected))

    def _update_selection_label(self) -> None:
        selected = len(self.selected_file_paths())
        self.selection_label.setText(f"選択中: {selected}ファイル")

    def _update_enabled(self) -> None:
        self.apply_button.setEnabled(
            self.risk_check.isChecked()
            and self.destination_check.isChecked()
            and self.domain_edit.text().strip() == self.domain
            and bool(self.selected_file_paths())
        )


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
