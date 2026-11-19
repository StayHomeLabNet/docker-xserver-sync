from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .auto_sync_policy import AutoSyncPreferences, auto_sync_policy_issues
from .health import normalize_health_check_path


def _prefix(project_id: str) -> str:
    return f"auto_sync/{project_id}"


def load_auto_sync_preferences(
    settings: QSettings, project_id: str
) -> AutoSyncPreferences:
    prefix = _prefix(project_id)
    return AutoSyncPreferences(
        backup_mode=str(settings.value(f"{prefix}/backup_mode", "always")),
        remote_only_policy=str(
            settings.value(f"{prefix}/remote_only_policy", "keep")
        ),
        health_check_enabled=settings.value(
            f"{prefix}/health_check_enabled", True, type=bool
        ),
        health_check_path=str(
            settings.value(f"{prefix}/health_check_path", "/")
        ),
        max_changed_files=settings.value(
            f"{prefix}/max_changed_files", 1000, type=int
        ),
        max_update_files=settings.value(
            f"{prefix}/max_update_files", 500, type=int
        ),
        max_upload_mib=settings.value(
            f"{prefix}/max_upload_mib", 1024, type=int
        ),
        require_domain_confirmation=settings.value(
            f"{prefix}/require_domain_confirmation", True, type=bool
        ),
    )


def save_auto_sync_preferences(
    settings: QSettings,
    project_id: str,
    preferences: AutoSyncPreferences,
) -> None:
    prefix = _prefix(project_id)
    for key, value in (
        ("backup_mode", preferences.backup_mode),
        ("remote_only_policy", preferences.remote_only_policy),
        ("health_check_enabled", preferences.health_check_enabled),
        ("health_check_path", preferences.health_check_path),
        ("max_changed_files", preferences.max_changed_files),
        ("max_update_files", preferences.max_update_files),
        ("max_upload_mib", preferences.max_upload_mib),
        (
            "require_domain_confirmation",
            preferences.require_domain_confirmation,
        ),
    ):
        settings.setValue(f"{prefix}/{key}", value)
    settings.sync()


def format_auto_sync_summary(preferences: AutoSyncPreferences) -> str:
    backup_labels = {
        "always": "必ず作成",
        "add_only": "追加のみ時はスキップ",
        "skip": "常にスキップ（復元不可）",
    }
    remote_only_labels = {
        "keep": "削除せず残す",
        "ask": "確認画面で選択",
        "stop": "存在時は停止",
    }
    backup_text = backup_labels.get(preferences.backup_mode, preferences.backup_mode)
    if preferences.remote_only_policy == "ask" and preferences.backup_mode == "skip":
        remote_text = "削除不可（スキップ設定中）"
    else:
        remote_text = remote_only_labels.get(
            preferences.remote_only_policy, preferences.remote_only_policy
        )
    return f"自動実行設定 — バックアップ: {backup_text} / REMOTE_ONLY: {remote_text}"


class AutoSyncSettingsDialog(QDialog):
    def __init__(self, preferences: AutoSyncPreferences, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自動実行設定")
        self.resize(650, 490)
        layout = QVBoxLayout(self)
        note = QLabel(
            "この設定は選択中のプロジェクトに保存されます。自動実行でも、"
            "ドライラン後の統合確認と明示承認は省略できません。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self.backup_mode = QComboBox()
        self.backup_mode.addItem("必ず作成する（推奨）", "always")
        self.backup_mode.addItem("追加ファイルだけならスキップ", "add_only")
        self.backup_mode.addItem("常にスキップ（復元不可）", "skip")
        self.backup_mode.setCurrentIndex(
            max(self.backup_mode.findData(preferences.backup_mode), 0)
        )
        form.addRow("バックアップ", self.backup_mode)

        self.remote_only_policy = QComboBox()
        self.remote_only_policy.addItem("削除せず残す（推奨）", "keep")
        self.remote_only_policy.addItem(
            "確認画面で削除候補を選択する（削除時は自動バックアップ）",
            "ask",
        )
        self.remote_only_policy.addItem("存在したら自動実行を停止", "stop")
        self.remote_only_policy.setCurrentIndex(
            max(
                self.remote_only_policy.findData(
                    preferences.remote_only_policy
                ),
                0,
            )
        )
        form.addRow("リモートのみのファイル", self.remote_only_policy)

        self.health_check = QCheckBox("同期後にHTTPSで動作確認する")
        self.health_check.setChecked(preferences.health_check_enabled)
        self.health_path = QLineEdit(preferences.health_check_path)
        self.health_path.setPlaceholderText("/ または /health.php")
        self.health_path.setEnabled(preferences.health_check_enabled)
        self.health_check.toggled.connect(self.health_path.setEnabled)
        form.addRow(self.health_check, self.health_path)

        self.max_changed_files = self._limit_spin(preferences.max_changed_files)
        self.max_update_files = self._limit_spin(preferences.max_update_files)
        self.max_upload_mib = self._limit_spin(preferences.max_upload_mib)
        form.addRow("最大ADD＋UPDATE数", self.max_changed_files)
        form.addRow("最大UPDATE数", self.max_update_files)
        form.addRow("最大転送量（MiB）", self.max_upload_mib)

        self.require_domain_confirmation = QCheckBox(
            "最終確認で対象ドメインの再入力を要求する（推奨）"
        )
        self.require_domain_confirmation.setChecked(
            preferences.require_domain_confirmation
        )
        form.addRow("実行時の確認", self.require_domain_confirmation)
        layout.addLayout(form)

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #8a4b08;")
        layout.addWidget(self.warning_label)
        self.backup_mode.currentIndexChanged.connect(self._update_warning_text)
        self.remote_only_policy.currentIndexChanged.connect(
            self._update_warning_text
        )
        self._update_warning_text()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Save).setText("保存")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _update_warning_text(self) -> None:
        backup_mode = str(self.backup_mode.currentData())
        remote_only_policy = str(self.remote_only_policy.currentData())
        parts: list[str] = []
        if backup_mode == "skip":
            parts.append(
                "「常にスキップ（復元不可）」設定時はバックアップを作成せずに同期します"
                "（上書き前の状態へは復元できません。確認画面でその回だけバックアップを作成することも可能です）。"
            )
            if remote_only_policy == "ask":
                parts.append(
                    "ファイル削除にはバックアップが必須のため、確認画面でのREMOTE_ONLY削除は無効になります。"
                )
        elif backup_mode == "add_only":
            parts.append(
                "「追加ファイルだけならスキップ」設定時は、ADDのみでUPDATEや削除選択がない場合にバックアップをスキップします。"
            )
        if remote_only_policy == "keep":
            parts.append("「削除せず残す」設定時はREMOTE_ONLYを削除しません。")
        parts.append(
            "設定上限を超えた場合、バックアップ・再検証・HTTPS確認に失敗した場合は自動的に停止します。"
        )
        self.warning_label.setText(" ".join(parts))

    @staticmethod
    def _limit_spin(value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 2_000_000)
        spin.setSpecialValueText("制限なし")
        spin.setValue(value)
        return spin

    def _accept(self) -> None:
        if self.health_check.isChecked():
            try:
                normalize_health_check_path(self.health_path.text())
            except ValueError as exc:
                QMessageBox.warning(self, "HTTPS確認パスを確認", str(exc))
                return
        self.accept()

    def selected_preferences(self) -> AutoSyncPreferences:
        health_path = self.health_path.text().strip() or "/"
        if self.health_check.isChecked():
            health_path = normalize_health_check_path(health_path)
        return AutoSyncPreferences(
            backup_mode=str(self.backup_mode.currentData()),
            remote_only_policy=str(self.remote_only_policy.currentData()),
            health_check_enabled=self.health_check.isChecked(),
            health_check_path=health_path,
            max_changed_files=self.max_changed_files.value(),
            max_update_files=self.max_update_files.value(),
            max_upload_mib=self.max_upload_mib.value(),
            require_domain_confirmation=(
                self.require_domain_confirmation.isChecked()
            ),
        )


class AutoSyncApprovalDialog(QDialog):
    def __init__(
        self,
        *,
        domain: str,
        remote_root: str,
        add_count: int,
        update_count: int,
        remote_only_count: int,
        upload_bytes: int,
        backup_skipped: bool,
        health_check_path: str | None,
        require_domain_confirmation: bool = True,
        remote_only_paths: tuple[str, ...] = (),
        allow_remote_only_deletion: bool = False,
        backup_mode: str = "always",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.domain = domain
        self._add_count = add_count
        self._update_count = update_count
        self._remote_only_count = remote_only_count
        self._backup_skipped = backup_skipped
        self._backup_mode = backup_mode
        self._allow_remote_only_deletion = (
            allow_remote_only_deletion
            and bool(remote_only_paths)
            and backup_mode != "skip"
        )
        self.require_domain_confirmation = require_domain_confirmation
        self.setWindowTitle("安全同期の自動実行を確認")
        self.resize(700, 720 if self._allow_remote_only_deletion else 510)
        layout = QVBoxLayout(self)
        title = QLabel(
            "ドライランが完了しました。承認後は再検証・バックアップ・同期を"
            "設定どおり自動実行します。"
        )
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()
        form.addRow("対象ドメイン", QLabel(domain))
        form.addRow("転送先", QLabel(remote_root))
        form.addRow("ADD", QLabel(f"{add_count}件"))
        form.addRow("UPDATE", QLabel(f"{update_count}件"))
        self.remote_only_label = QLabel(f"{remote_only_count}件（削除しません）")
        form.addRow("REMOTE_ONLY", self.remote_only_label)
        form.addRow("転送量", QLabel(f"{upload_bytes:,} bytes"))
        self.backup_label = QLabel()
        form.addRow("バックアップ", self.backup_label)
        form.addRow(
            "HTTPS確認",
            QLabel(health_check_path or "実行しない"),
        )
        layout.addLayout(form)

        self.skip_backup_check = QCheckBox(
            "バックアップ作成をスキップする（上書き前の状態には復元できません）"
        )
        self.skip_backup_check.setChecked(backup_skipped)
        self.skip_backup_check.toggled.connect(self._skip_backup_toggled)
        layout.addWidget(self.skip_backup_check)

        self.save_skip_backup_check = QCheckBox(
            "次回以降もこのプロジェクトの自動実行設定でバックアップを常にスキップする"
        )
        self.save_skip_backup_check.setChecked(False)
        self.save_skip_backup_check.setVisible(
            backup_skipped and backup_mode != "skip"
        )
        layout.addWidget(self.save_skip_backup_check)

        self.deletion_table = QTableWidget(0, 1)
        self.deletion_table.setHorizontalHeaderLabels(
            ("削除候補（初期状態はすべて未選択）",)
        )
        self.deletion_table.horizontalHeader().setStretchLastSection(True)
        self.deletion_table.setMaximumHeight(160)
        self.deletion_table.setVisible(self._allow_remote_only_deletion)
        if self._allow_remote_only_deletion:
            self.deletion_table.setRowCount(len(remote_only_paths))
            for row, path in enumerate(remote_only_paths):
                item = QTableWidgetItem(path)
                item.setFlags(
                    (item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable
                )
                item.setCheckState(Qt.Unchecked)
                self.deletion_table.setItem(row, 0, item)
        self.deletion_table.itemChanged.connect(self._deletion_selection_changed)

        preserved_folders = (
            tuple(
                sorted(
                    {
                        path.rsplit("/", 1)[0] + "/"
                        for path in remote_only_paths
                        if "/" in path
                    }
                )
            )
            if self._allow_remote_only_deletion
            else ()
        )
        self.preserve_table = QTableWidget(len(preserved_folders), 1)
        self.preserve_table.setHorizontalHeaderLabels(("削除しないフォルダー",))
        self.preserve_table.horizontalHeader().setStretchLastSection(True)
        self.preserve_table.setMaximumHeight(120)
        self.preserve_table.setVisible(bool(preserved_folders))
        for row, path in enumerate(preserved_folders):
            item = QTableWidgetItem(path)
            item.setFlags(
                (item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable
            )
            item.setCheckState(Qt.Unchecked)
            self.preserve_table.setItem(row, 0, item)
        self.preserve_table.itemChanged.connect(self._preserved_folders_changed)

        if self._allow_remote_only_deletion:
            deletion_note = QLabel(
                "削除するファイルだけを選択してください。選択したファイルは先にバックアップされ、"
                "再検証と最終同期で公開領域から削除されます（削除選択時はバックアップを自動作成します）。"
            )
            deletion_note.setWordWrap(True)
            layout.addWidget(deletion_note)
        elif allow_remote_only_deletion and remote_only_paths and backup_mode == "skip":
            skip_note = QLabel(
                "バックアップ設定が「常にスキップ」のため、自動実行ではREMOTE_ONLYを削除できません。"
            )
            skip_note.setWordWrap(True)
            skip_note.setStyleSheet("color: #8a4b08;")
            layout.addWidget(skip_note)

        layout.addWidget(self.deletion_table)
        deletion_buttons = QHBoxLayout()
        self.select_all_deletions_button = QPushButton("削除候補をすべて選択")
        self.clear_all_deletions_button = QPushButton("削除候補をすべて解除")
        self.select_all_deletions_button.clicked.connect(
            self._select_all_deletions
        )
        self.clear_all_deletions_button.clicked.connect(
            self._clear_all_deletions
        )
        self.select_all_deletions_button.setVisible(self._allow_remote_only_deletion)
        self.clear_all_deletions_button.setVisible(self._allow_remote_only_deletion)
        deletion_buttons.addWidget(self.select_all_deletions_button)
        deletion_buttons.addWidget(self.clear_all_deletions_button)
        deletion_buttons.addStretch(1)
        layout.addLayout(deletion_buttons)

        if preserved_folders:
            preserve_note = QLabel(
                "チェックしたフォルダー以下は、上の削除候補で選択しても削除されません。"
            )
            preserve_note.setWordWrap(True)
            layout.addWidget(preserve_note)
        layout.addWidget(self.preserve_table)

        self.destination_check = QCheckBox(
            "転送先と変更件数を確認しました"
        )
        self.workflow_check = QCheckBox(
            "承認後は各段階を自動実行し、途中の確認画面を表示しないことを理解しました"
        )
        self.delete_check = QCheckBox("選択したファイルを削除することを確認しました")
        self.delete_check.setVisible(False)
        layout.addWidget(self.destination_check)
        layout.addWidget(self.workflow_check)
        layout.addWidget(self.delete_check)
        self.domain_prompt = QLabel(
            f"確認のため「{domain}」と入力してください。"
        )
        layout.addWidget(self.domain_prompt)
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText(domain)
        layout.addWidget(self.domain_edit)
        if not require_domain_confirmation:
            self.domain_prompt.setText(
                "設定により対象ドメインの再入力を省略します。"
            )
            self.domain_prompt.setStyleSheet("color: #8a4b08;")
            self.domain_edit.setVisible(False)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.apply_button = self.buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("承認して自動実行を続ける")
        self.apply_button.setEnabled(False)
        self.destination_check.toggled.connect(self._update_enabled)
        self.workflow_check.toggled.connect(self._update_enabled)
        self.delete_check.toggled.connect(self._update_enabled)
        self.domain_edit.textChanged.connect(self._update_enabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._refresh_backup_ui()

    def selected_delete_paths(self) -> tuple[str, ...]:
        if not self._allow_remote_only_deletion:
            return ()
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
        if self.selected_delete_paths():
            return False
        return self.skip_backup_check.isChecked()

    def should_save_skip_backup_preference(self) -> bool:
        return (
            self.should_skip_backup()
            and self._backup_mode != "skip"
            and self.save_skip_backup_check.isChecked()
        )

    def _refresh_backup_ui(self) -> None:
        if self.selected_delete_paths():
            self.backup_label.setText("作成する（削除対象の退避が必要）")
            self.backup_label.setStyleSheet("")
            self.save_skip_backup_check.setChecked(False)
            self.save_skip_backup_check.setVisible(False)
        elif self.skip_backup_check.isChecked():
            self.backup_label.setText("スキップ（復元不可）")
            self.backup_label.setStyleSheet("color: #8a4b08; font-weight: bold;")
            can_save = self._backup_mode != "skip"
            self.save_skip_backup_check.setVisible(can_save)
            if not can_save:
                self.save_skip_backup_check.setChecked(False)
        else:
            self.backup_label.setText("作成する")
            self.backup_label.setStyleSheet("")
            self.save_skip_backup_check.setChecked(False)
            self.save_skip_backup_check.setVisible(False)

    def _skip_backup_toggled(self, checked: bool) -> None:
        if checked and self.selected_delete_paths():
            self.deletion_table.blockSignals(True)
            try:
                for row in range(self.deletion_table.rowCount()):
                    item = self.deletion_table.item(row, 0)
                    if item is not None:
                        item.setCheckState(Qt.Unchecked)
            finally:
                self.deletion_table.blockSignals(False)
            self.remote_only_label.setText(
                f"{self._remote_only_count}件（削除しません）"
            )
            self.delete_check.setChecked(False)
            self.delete_check.setVisible(False)
        self._refresh_backup_ui()
        self._update_enabled()

    def _select_all_deletions(self) -> None:
        self.deletion_table.blockSignals(True)
        try:
            for row in range(self.deletion_table.rowCount()):
                item = self.deletion_table.item(row, 0)
                if item is not None and (item.flags() & Qt.ItemIsEnabled):
                    item.setCheckState(Qt.Checked)
        finally:
            self.deletion_table.blockSignals(False)
        self._deletion_selection_changed()

    def _clear_all_deletions(self) -> None:
        self.deletion_table.blockSignals(True)
        try:
            for row in range(self.deletion_table.rowCount()):
                item = self.deletion_table.item(row, 0)
                if item is not None:
                    item.setCheckState(Qt.Unchecked)
        finally:
            self.deletion_table.blockSignals(False)
        self._deletion_selection_changed()

    def _preserved_folders_changed(self, *_args) -> None:
        preserved = self.preserved_folder_paths()
        self.deletion_table.blockSignals(True)
        try:
            for row in range(self.deletion_table.rowCount()):
                item = self.deletion_table.item(row, 0)
                if item is None:
                    continue
                blocked = any(item.text().startswith(folder) for folder in preserved)
                if blocked:
                    item.setCheckState(Qt.Unchecked)
                flags = item.flags()
                item.setFlags(
                    flags & ~Qt.ItemIsEnabled
                    if blocked
                    else flags | Qt.ItemIsEnabled
                )
        finally:
            self.deletion_table.blockSignals(False)
        self._deletion_selection_changed()

    def _deletion_selection_changed(self, *_args) -> None:
        selected = self.selected_delete_paths()
        delete_count = len(selected)
        if delete_count > 0:
            self.remote_only_label.setText(
                f"{self._remote_only_count}件（うち {delete_count}件を削除）"
            )
            self.delete_check.setText(
                f"選択した{delete_count}件を削除することを確認しました"
            )
            self.delete_check.setVisible(True)
            self.skip_backup_check.blockSignals(True)
            self.skip_backup_check.setChecked(False)
            self.skip_backup_check.setEnabled(False)
            self.skip_backup_check.blockSignals(False)
        else:
            self.remote_only_label.setText(
                f"{self._remote_only_count}件（削除しません）"
            )
            self.delete_check.setChecked(False)
            self.delete_check.setVisible(False)
            was_disabled = not self.skip_backup_check.isEnabled()
            self.skip_backup_check.setEnabled(True)
            if was_disabled:
                self.skip_backup_check.blockSignals(True)
                self.skip_backup_check.setChecked(self._backup_skipped)
                self.skip_backup_check.blockSignals(False)
        self._refresh_backup_ui()
        self._update_enabled()

    def _update_enabled(self) -> None:
        selected_count = len(self.selected_delete_paths())
        has_work = (self._add_count + self._update_count + selected_count) > 0
        self.apply_button.setEnabled(
            has_work
            and self.destination_check.isChecked()
            and self.workflow_check.isChecked()
            and (selected_count == 0 or self.delete_check.isChecked())
            and (
                not self.require_domain_confirmation
                or self.domain_edit.text().strip() == self.domain
            )
        )
