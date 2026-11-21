from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .backup import select_prune_candidates
from .storage import BackupRecord, ProjectStore
from .ui_styles import MUTED_TEXT_STYLE


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


class HistoryDialog(QDialog):
    def __init__(
        self,
        store: ProjectStore,
        project_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.project_id = project_id
        self.selected_backup_id: str | None = None
        self.selected_delete_backup_ids: tuple[str, ...] = ()
        self._backup_ids: list[str] = []
        self._backups_by_id: dict[str, BackupRecord] = {}
        self.setWindowTitle("操作履歴とバックアップ")
        self.resize(1100, 640)

        layout = QVBoxLayout(self)
        note = QLabel(
            "操作履歴はローカルSQLiteに保存されます。復元前に現在ファイルとバックアップを再検証します。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.tabs = QTabWidget()
        operations_page = QWidget()
        operations_layout = QVBoxLayout(operations_page)
        self.operations_table = QTableWidget(0, 6)
        self.operations_table.setHorizontalHeaderLabels(
            ("開始日時", "操作", "状態", "マッピング", "詳細", "エラー")
        )
        operations_header = self.operations_table.horizontalHeader()
        operations_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        operations_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        operations_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        operations_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        operations_header.setSectionResizeMode(4, QHeaderView.Stretch)
        operations_header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.operations_table.setEditTriggers(QTableWidget.NoEditTriggers)
        operations_layout.addWidget(self.operations_table)
        self.tabs.addTab(operations_page, "操作履歴")

        backups_page = QWidget()
        backups_layout = QVBoxLayout(backups_page)

        retention_bar = QHBoxLayout()
        retention_bar.addWidget(QLabel("保持件数（最新）"))
        self.keep_latest_spin = QSpinBox()
        self.keep_latest_spin.setRange(0, 500)
        self.keep_latest_spin.setValue(10)
        self.keep_latest_spin.setSpecialValueText("保護なし (0)")
        retention_bar.addWidget(self.keep_latest_spin)

        retention_bar.addWidget(QLabel("保持日数"))
        self.max_age_days_spin = QSpinBox()
        self.max_age_days_spin.setRange(0, 3650)
        self.max_age_days_spin.setValue(30)
        self.max_age_days_spin.setSuffix("日")
        self.max_age_days_spin.setSpecialValueText("日数制限なし")
        retention_bar.addWidget(self.max_age_days_spin)

        self.select_prune_button = QPushButton("保持ルールで整理候補を選択")
        self.select_prune_button.setToolTip(
            "最新の保持件数と保持日数の両方を満たさない古いバックアップの削除チェックを付けます"
        )
        self.select_prune_button.clicked.connect(self._select_prune_candidates)
        retention_bar.addWidget(self.select_prune_button)

        self.clear_prune_button = QPushButton("削除選択を解除")
        self.clear_prune_button.clicked.connect(self._clear_delete_checks)
        retention_bar.addWidget(self.clear_prune_button)
        retention_bar.addStretch(1)
        backups_layout.addLayout(retention_bar)

        self.backups_table = QTableWidget(0, 7)
        self.backups_table.setHorizontalHeaderLabels(
            ("削除", "作成日時", "状態", "ファイル数", "サイズ", "バックアップID", "保存先")
        )
        backups_header = self.backups_table.horizontalHeader()
        for column in range(6):
            backups_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        backups_header.setSectionResizeMode(6, QHeaderView.Stretch)
        self.backups_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.backups_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.backups_table.setSelectionMode(QTableWidget.SingleSelection)
        self.backups_table.itemSelectionChanged.connect(self._update_action_buttons)
        self.backups_table.itemChanged.connect(self._update_action_buttons)
        self.backups_table.itemDoubleClicked.connect(lambda _item: self._choose_restore())
        backups_layout.addWidget(self.backups_table)
        restore_note = QLabel(
            "復元できるのは既存ファイルを退避した利用可能なバックアップだけです。"
            "バックアップ削除は明示確認後にXServer上の退避先を削除します。"
        )
        restore_note.setStyleSheet(MUTED_TEXT_STYLE)
        backups_layout.addWidget(restore_note)
        self.tabs.addTab(backups_page, "バックアップ")
        layout.addWidget(self.tabs, 1)

        refresh = QPushButton("更新")
        refresh.clicked.connect(self.refresh)
        self.delete_backups_button = QPushButton("チェックしたバックアップを削除…")
        self.delete_backups_button.setEnabled(False)
        self.delete_backups_button.clicked.connect(self._choose_delete_backups)
        self.restore_button = QPushButton("選択したバックアップを復元…")
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self._choose_restore)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.addButton(refresh, QDialogButtonBox.ActionRole)
        buttons.addButton(self.delete_backups_button, QDialogButtonBox.ActionRole)
        buttons.addButton(self.restore_button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        operations = self.store.list_operations(self.project_id)
        self.operations_table.setRowCount(len(operations))
        for row, operation in enumerate(operations):
            values = (
                operation.started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                operation.kind,
                operation.status,
                operation.mapping_id,
                json.dumps(operation.details, ensure_ascii=False, sort_keys=True),
                operation.error or "",
            )
            for column, value in enumerate(values):
                self.operations_table.setItem(row, column, QTableWidgetItem(str(value)))

        backups = self.store.list_backups(self.project_id)
        self._backup_ids = [backup.backup_id for backup in backups]
        self._backups_by_id = {backup.backup_id: backup for backup in backups}
        self.backups_table.blockSignals(True)
        try:
            self.backups_table.setRowCount(len(backups))
            for row, backup in enumerate(backups):
                check_item = QTableWidgetItem("")
                flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
                if backup.status != "deleted":
                    flags |= Qt.ItemIsUserCheckable
                check_item.setFlags(flags)
                check_item.setCheckState(Qt.Unchecked)
                self.backups_table.setItem(row, 0, check_item)

                values = (
                    backup.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    backup.status,
                    backup.file_count,
                    _format_bytes(backup.total_bytes),
                    backup.backup_id,
                    backup.backup_directory,
                )
                for column, value in enumerate(values, start=1):
                    self.backups_table.setItem(
                        row, column, QTableWidgetItem(str(value))
                    )
        finally:
            self.backups_table.blockSignals(False)
        self._update_action_buttons()

    def checked_delete_backup_ids(self) -> tuple[str, ...]:
        selected: list[str] = []
        for row, backup_id in enumerate(self._backup_ids):
            item = self.backups_table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                selected.append(backup_id)
        return tuple(selected)

    def _select_prune_candidates(self) -> None:
        backups = [
            self._backups_by_id[backup_id]
            for backup_id in self._backup_ids
            if backup_id in self._backups_by_id
        ]
        candidates = {
            backup.backup_id
            for backup in select_prune_candidates(
                backups,
                keep_latest=self.keep_latest_spin.value(),
                max_age_days=self.max_age_days_spin.value(),
            )
        }
        self.backups_table.blockSignals(True)
        try:
            for row, backup_id in enumerate(self._backup_ids):
                item = self.backups_table.item(row, 0)
                if item is None or not (item.flags() & Qt.ItemIsUserCheckable):
                    continue
                item.setCheckState(
                    Qt.Checked if backup_id in candidates else Qt.Unchecked
                )
        finally:
            self.backups_table.blockSignals(False)
        self._update_action_buttons()

    def _clear_delete_checks(self) -> None:
        self.backups_table.blockSignals(True)
        try:
            for row in range(len(self._backup_ids)):
                item = self.backups_table.item(row, 0)
                if item is not None:
                    item.setCheckState(Qt.Unchecked)
        finally:
            self.backups_table.blockSignals(False)
        self._update_action_buttons()

    def _update_action_buttons(self, *_args) -> None:
        row = self.backups_table.currentRow()
        restore_enabled = False
        if 0 <= row < len(self._backup_ids):
            backup = self.store.get_backup(self._backup_ids[row])
            restore_enabled = bool(
                backup
                and backup.status == "available"
                and backup.file_count > 0
                and self.store.get_plan(backup.plan_id) is not None
            )
        self.restore_button.setEnabled(restore_enabled)
        checked_ids = self.checked_delete_backup_ids()
        self.delete_backups_button.setEnabled(
            bool(checked_ids) and self.project_id is not None
        )

    def _choose_restore(self) -> None:
        if not self.restore_button.isEnabled():
            return
        row = self.backups_table.currentRow()
        self.selected_backup_id = self._backup_ids[row]
        self.selected_delete_backup_ids = ()
        self.accept()

    def _choose_delete_backups(self) -> None:
        checked_ids = self.checked_delete_backup_ids()
        if not checked_ids or self.project_id is None:
            return
        total_bytes = sum(
            self._backups_by_id[backup_id].total_bytes
            for backup_id in checked_ids
            if backup_id in self._backups_by_id
        )
        preview = "\n".join(f"• {backup_id}" for backup_id in checked_ids[:10])
        if len(checked_ids) > 10:
            preview += f"\n…ほか {len(checked_ids) - 10} 件"
        answer = QMessageBox.question(
            self,
            "バックアップの削除を確認",
            f"選択した {len(checked_ids)} 件のバックアップ"
            f"（合計 {_format_bytes(total_bytes)}）をXServer上から削除します。\n"
            "この操作は取り消せません。続行しますか？\n\n"
            f"{preview}",
        )
        if answer != QMessageBox.Yes:
            return
        self.selected_backup_id = None
        self.selected_delete_backup_ids = checked_ids
        self.accept()
