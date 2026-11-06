from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from .health import normalize_health_check_path


@dataclass(frozen=True)
class AutoSyncPreferences:
    backup_mode: str = "always"
    remote_only_policy: str = "keep"
    health_check_enabled: bool = True
    health_check_path: str = "/"
    max_changed_files: int = 1000
    max_update_files: int = 500
    max_upload_mib: int = 1024

    def resolved_backup_skip(self, update_count: int) -> bool:
        if self.backup_mode == "skip":
            return True
        return self.backup_mode == "add_only" and update_count == 0


def auto_sync_policy_issues(
    preferences: AutoSyncPreferences,
    *,
    add_count: int,
    update_count: int,
    remote_only_count: int,
    upload_bytes: int,
) -> tuple[str, ...]:
    issues: list[str] = []
    changed_count = add_count + update_count
    if preferences.remote_only_policy == "stop" and remote_only_count:
        issues.append(
            f"REMOTE_ONLYが{remote_only_count}件あるため停止設定に該当しました。"
        )
    if preferences.max_changed_files and changed_count > preferences.max_changed_files:
        issues.append(
            f"ADD＋UPDATE {changed_count}件が上限"
            f"{preferences.max_changed_files}件を超えています。"
        )
    if preferences.max_update_files and update_count > preferences.max_update_files:
        issues.append(
            f"UPDATE {update_count}件が上限"
            f"{preferences.max_update_files}件を超えています。"
        )
    max_upload_bytes = preferences.max_upload_mib * 1024 * 1024
    if max_upload_bytes and upload_bytes > max_upload_bytes:
        issues.append(
            f"転送量{upload_bytes:,} bytesが上限"
            f"{preferences.max_upload_mib} MiBを超えています。"
        )
    return tuple(issues)


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
    ):
        settings.setValue(f"{prefix}/{key}", value)
    settings.sync()


class AutoSyncSettingsDialog(QDialog):
    def __init__(self, preferences: AutoSyncPreferences, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自動実行設定")
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        note = QLabel(
            "この設定は選択中のプロジェクトに保存されます。自動実行でも、"
            "ドライラン後の統合確認と対象ドメインの再入力は省略できません。"
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
        layout.addLayout(form)

        warning = QLabel(
            "自動実行ではREMOTE_ONLYを削除しません。設定上限を超えた場合、"
            "バックアップ・再検証・HTTPS確認に失敗した場合は自動的に停止します。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a4b08;")
        layout.addWidget(warning)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Save).setText("保存")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.domain = domain
        self.setWindowTitle("安全同期の自動実行を確認")
        self.resize(640, 460)
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
        form.addRow("REMOTE_ONLY", QLabel(f"{remote_only_count}件（削除しません）"))
        form.addRow("転送量", QLabel(f"{upload_bytes:,} bytes"))
        form.addRow(
            "バックアップ",
            QLabel("スキップ（復元不可）" if backup_skipped else "作成する"),
        )
        form.addRow(
            "HTTPS確認",
            QLabel(health_check_path or "実行しない"),
        )
        layout.addLayout(form)

        self.destination_check = QCheckBox(
            "転送先と変更件数を確認しました"
        )
        self.workflow_check = QCheckBox(
            "承認後は各段階を自動実行し、途中の確認画面を表示しないことを理解しました"
        )
        layout.addWidget(self.destination_check)
        layout.addWidget(self.workflow_check)
        layout.addWidget(QLabel(f"確認のため「{domain}」と入力してください。"))
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText(domain)
        layout.addWidget(self.domain_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.apply_button = self.buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("承認して自動実行を続ける")
        self.apply_button.setEnabled(False)
        self.destination_check.toggled.connect(self._update_enabled)
        self.workflow_check.toggled.connect(self._update_enabled)
        self.domain_edit.textChanged.connect(self._update_enabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _update_enabled(self) -> None:
        self.apply_button.setEnabled(
            self.destination_check.isChecked()
            and self.workflow_check.isChecked()
            and self.domain_edit.text().strip() == self.domain
        )
