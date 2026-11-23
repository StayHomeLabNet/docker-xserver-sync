from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from .models import FileRecord, ScanResult
from .progress_tracker import format_size
from .rules import PathRules
from .service import DryRunResult


class FileTableModel(QAbstractTableModel):
    COLUMNS = ("状態", "相対パス", "サイズ", "SHA-256")

    def __init__(self) -> None:
        super().__init__()
        self._files: tuple[FileRecord, ...] = ()
        self._statuses: tuple[str, ...] = ()
        self._protected = PathRules(())

    def set_scan(self, result: ScanResult, protected_patterns: tuple[str, ...]) -> None:
        self.beginResetModel()
        self._files = result.files
        self._statuses = tuple(
            "保護" if PathRules(protected_patterns).matches(item.relative_path) else "ローカル"
            for item in result.files
        )
        self._protected = PathRules(protected_patterns)
        self.endResetModel()

    def set_plan(self, result: DryRunResult) -> None:
        self.beginResetModel()
        rows = [change for change in result.plan.changes]
        self._files = tuple(
            change.local or change.remote
            for change in rows
            if (change.local or change.remote) is not None
        )
        self._statuses = tuple(change.change_type.value.upper() for change in rows)
        self._protected = PathRules(())
        self.endResetModel()

    def row_entry(self, row: int) -> tuple[str, FileRecord] | None:
        if 0 <= row < len(self._files):
            return self._statuses[row], self._files[row]
        return None

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if (parent is not None and parent.isValid()) else len(self._files)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if (parent is not None and parent.isValid()) else len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self._files[index.row()]
        status = self._statuses[index.row()]
        is_protected = status in {"保護", "PROTECTED"}
        if role == Qt.DisplayRole:
            values = (
                status,
                record.relative_path,
                format_size(record.size),
                record.sha256 or "—",
            )
            return values[index.column()]
        if role == Qt.ForegroundRole and is_protected:
            return QApplication.palette().color(QPalette.PlaceholderText)
        if role == Qt.TextAlignmentRole and index.column() == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None
