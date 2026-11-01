from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .storage import ProjectStore


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
        self._backup_ids: list[str] = []
        self.setWindowTitle("操作履歴とバックアップ")
        self.resize(1050, 620)

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
        self.backups_table = QTableWidget(0, 6)
        self.backups_table.setHorizontalHeaderLabels(
            ("作成日時", "状態", "ファイル数", "サイズ", "バックアップID", "保存先")
        )
        backups_header = self.backups_table.horizontalHeader()
        for column in range(5):
            backups_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        backups_header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.backups_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.backups_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.backups_table.setSelectionMode(QTableWidget.SingleSelection)
        self.backups_table.itemSelectionChanged.connect(self._update_restore_enabled)
        self.backups_table.itemDoubleClicked.connect(lambda _item: self._choose_restore())
        backups_layout.addWidget(self.backups_table)
        restore_note = QLabel("復元できるのは既存ファイルを退避した利用可能なバックアップだけです。")
        restore_note.setStyleSheet("color: #57606a;")
        backups_layout.addWidget(restore_note)
        self.tabs.addTab(backups_page, "バックアップ")
        layout.addWidget(self.tabs, 1)

        refresh = QPushButton("更新")
        refresh.clicked.connect(self.refresh)
        self.restore_button = QPushButton("選択したバックアップを復元…")
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self._choose_restore)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.addButton(refresh, QDialogButtonBox.ActionRole)
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
        self.backups_table.setRowCount(len(backups))
        for row, backup in enumerate(backups):
            values = (
                backup.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                backup.status,
                backup.file_count,
                _format_bytes(backup.total_bytes),
                backup.backup_id,
                backup.backup_directory,
            )
            for column, value in enumerate(values):
                self.backups_table.setItem(row, column, QTableWidgetItem(str(value)))
        self._update_restore_enabled()

    def _update_restore_enabled(self) -> None:
        row = self.backups_table.currentRow()
        enabled = False
        if 0 <= row < len(self._backup_ids):
            backup = self.store.get_backup(self._backup_ids[row])
            enabled = bool(
                backup
                and backup.status == "available"
                and backup.file_count > 0
                and self.store.get_plan(backup.plan_id) is not None
            )
        self.restore_button.setEnabled(enabled)

    def _choose_restore(self) -> None:
        if not self.restore_button.isEnabled():
            return
        row = self.backups_table.currentRow()
        self.selected_backup_id = self._backup_ids[row]
        self.accept()
