from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem


class CheckableFileTree(QTreeWidget):
    """Hierarchical, checkable file list with folder propagation."""

    selectionChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(("アップロード対象",))
        self.itemChanged.connect(self._item_changed)

    def set_file_paths(self, file_paths: tuple[str, ...]) -> None:
        self.blockSignals(True)
        try:
            self.clear()
            folders: dict[str, QTreeWidgetItem] = {}
            for path in sorted(file_paths):
                parts = path.split("/")
                parent: QTreeWidgetItem | CheckableFileTree = self
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
                item = QTreeWidgetItem(parent, (parts[-1],))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setData(0, Qt.UserRole, path)
                item.setCheckState(0, Qt.Checked)
            self.expandToDepth(1)
        finally:
            self.blockSignals(False)
        self.selectionChanged.emit()

    def selected_file_paths(self) -> tuple[str, ...]:
        selected: list[str] = []

        def collect(item: QTreeWidgetItem) -> None:
            path = item.data(0, Qt.UserRole)
            if path and item.checkState(0) == Qt.Checked:
                selected.append(path)
            for index in range(item.childCount()):
                collect(item.child(index))

        for index in range(self.topLevelItemCount()):
            collect(self.topLevelItem(index))
        return tuple(sorted(selected))

    def set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        self.blockSignals(True)
        try:
            for index in range(self.topLevelItemCount()):
                item = self.topLevelItem(index)
                item.setCheckState(0, state)
                self._set_descendants(item, state)
        finally:
            self.blockSignals(False)
        self.selectionChanged.emit()

    def _item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        self.blockSignals(True)
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
            self.blockSignals(False)
        self.selectionChanged.emit()

    def _set_descendants(
        self, item: QTreeWidgetItem, state: Qt.CheckState
    ) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(0, state)
            self._set_descendants(child, state)
