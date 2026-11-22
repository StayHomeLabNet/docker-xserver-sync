from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStyleFactory,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .models import SiteType
from .presets import get_preset
from .remote_listing import RemoteProtectionEntry
from .ui_styles import MUTED_NOTE_STYLE, PANEL_STYLE
from .validation import normalize_protected_file, normalize_runtime_directory


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.PlainText)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    label.setStyleSheet(MUTED_NOTE_STYLE)
    return label


class RemoteListingWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        provider: Callable[[], tuple[RemoteProtectionEntry, ...]],
    ) -> None:
        super().__init__()
        self.provider = provider

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self.provider())
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class RemoteProtectionPickerDialog(QDialog):
    def __init__(
        self,
        entries: tuple[RemoteProtectionEntry, ...],
        selected_patterns: tuple[str, ...],
        recursive_listing_provider: Callable[
            [], tuple[RemoteProtectionEntry, ...]
        ]
        | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.entries = entries
        self._selected_patterns = selected_patterns
        self.recursive_listing_provider = recursive_listing_provider
        self._listing_thread: QThread | None = None
        self._listing_worker: RemoteListingWorker | None = None
        self.setWindowTitle("XServer側の保護対象を選択")
        self.resize(620, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _hint(
                "保護するファイルまたはフォルダにチェックを入れてください。"
                "フォルダを選ぶと、その中のすべての項目が保護されます。"
            )
        )
        self.tree = QTreeWidget()
        self._tree_style = QStyleFactory.create("Fusion")
        if self._tree_style is not None:
            self.tree.setStyle(self._tree_style)
        self.tree.setHeaderLabels(("XServer側のファイルとフォルダ",))
        self.tree.itemChanged.connect(self._item_changed)
        layout.addWidget(self.tree, 1)
        self._populate(entries, selected_patterns)

        recursive_row = QHBoxLayout()
        recursive_row.addWidget(
            _hint(
                "フォルダを選ぶだけなら、配下の一覧取得は不要です。"
                "配下の項目を個別に選ぶ場合のみ取得してください。"
            ),
            1,
        )
        self.load_recursive_button = QPushButton("サブフォルダも含めて取得…")
        self.load_recursive_button.setEnabled(recursive_listing_provider is not None)
        self.load_recursive_button.clicked.connect(self._load_recursive_entries)
        recursive_row.addWidget(self.load_recursive_button)
        layout.addLayout(recursive_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("選択内容を反映")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _populate(
        self,
        entries: tuple[RemoteProtectionEntry, ...],
        selected_patterns: tuple[str, ...],
    ) -> None:
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            items: dict[str, QTreeWidgetItem] = {}
            entry_types = {entry.relative_path: entry.is_directory for entry in entries}

            def ensure_item(path: str, is_directory: bool) -> QTreeWidgetItem:
                existing = items.get(path)
                if existing is not None:
                    return existing
                parent_path, _, name = path.rpartition("/")
                parent = (
                    ensure_item(parent_path, True)
                    if parent_path
                    else self.tree
                )
                item = QTreeWidgetItem(parent, (name or path,))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                if is_directory:
                    item.setFlags(item.flags() | Qt.ItemIsAutoTristate)
                item.setData(0, Qt.UserRole, path)
                item.setData(0, Qt.UserRole + 1, is_directory)
                item.setCheckState(0, Qt.Unchecked)
                items[path] = item
                return item

            for entry in entries:
                ensure_item(entry.relative_path, entry.is_directory)

            selected = set(selected_patterns)
            for path, item in items.items():
                pattern = f"{path}/" if entry_types.get(path, True) else path
                if pattern in selected:
                    item.setCheckState(0, Qt.Checked)
                    if item.data(0, Qt.UserRole + 1):
                        self._set_descendants(item, Qt.Checked)
            self.tree.expandToDepth(1)
        finally:
            self.tree.blockSignals(False)

    @Slot()
    def _load_recursive_entries(self) -> None:
        if self.recursive_listing_provider is None or self._listing_thread is not None:
            return
        available = {entry.protection_pattern for entry in self.entries}
        preserved = tuple(
            pattern for pattern in self._selected_patterns if pattern not in available
        )
        self._selected_patterns = tuple(
            dict.fromkeys((*preserved, *self.selected_patterns()))
        )
        self.load_recursive_button.setEnabled(False)
        self.load_recursive_button.setText("サブフォルダを取得中…")
        self.buttons.setEnabled(False)

        thread = QThread(self)
        worker = RemoteListingWorker(self.recursive_listing_provider)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._recursive_entries_loaded)
        worker.failed.connect(self._recursive_entries_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._recursive_listing_finished)
        self._listing_thread = thread
        self._listing_worker = worker
        thread.start()

    @Slot(object)
    def _recursive_entries_loaded(
        self,
        entries: tuple[RemoteProtectionEntry, ...],
    ) -> None:
        self.entries = entries
        self._populate(entries, self._selected_patterns)
        self.recursive_listing_provider = None
        self.load_recursive_button.setText("サブフォルダ取得済み")
        self.load_recursive_button.setEnabled(False)

    @Slot(str)
    def _recursive_entries_failed(self, message: str) -> None:
        QMessageBox.warning(self, "サブフォルダを取得できません", message)

    @Slot()
    def _recursive_listing_finished(self) -> None:
        self._listing_thread = None
        self._listing_worker = None
        self.buttons.setEnabled(True)
        if self.recursive_listing_provider is not None:
            self.load_recursive_button.setText("サブフォルダも含めて取得…")
            self.load_recursive_button.setEnabled(True)
        else:
            self.load_recursive_button.setEnabled(False)

    def selected_patterns(self) -> tuple[str, ...]:
        result: list[str] = []

        def collect(item: QTreeWidgetItem) -> None:
            state = item.checkState(0)
            is_directory = bool(item.data(0, Qt.UserRole + 1))
            path = str(item.data(0, Qt.UserRole))
            if state == Qt.Checked:
                result.append(f"{path}/" if is_directory else path)
                return
            if state == Qt.PartiallyChecked:
                for index in range(item.childCount()):
                    collect(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            collect(self.tree.topLevelItem(index))
        return tuple(sorted(result))

    def _item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.checkState(0) == Qt.PartiallyChecked:
            return
        self.tree.blockSignals(True)
        try:
            self._set_descendants(item, item.checkState(0))
        finally:
            self.tree.blockSignals(False)

    def _set_descendants(
        self,
        item: QTreeWidgetItem,
        state: Qt.CheckState,
    ) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(0, state)
            self._set_descendants(child, state)


class RuntimePathsDialog(QDialog):
    def __init__(
        self,
        *,
        patterns: tuple[str, ...] = (),
        confirmed: bool = False,
        require_confirmation: bool = True,
        remote_listing_provider: Callable[
            [bool, tuple[str, ...]], tuple[RemoteProtectionEntry, ...]
        ]
        | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.patterns = patterns
        self.require_confirmation = require_confirmation
        self.remote_listing_provider = remote_listing_provider
        self._known_remote_files = {
            pattern for pattern in patterns if not pattern.endswith("/")
        }
        self._listing_thread: QThread | None = None
        self._listing_worker: RemoteListingWorker | None = None
        self.setWindowTitle(
            "XServer側の本番データを保護するファイル／フォルダ"
            if require_confirmation
            else "XServer側で保護するファイル／フォルダ（任意）"
        )
        self.resize(620, 500)
        layout = QVBoxLayout(self)

        if require_confirmation:
            intro = QLabel(
                "XServer上でアップロード、ログ、キャッシュなどが保存される"
                "ディレクトリを、同期元ルートからの相対パスで1行ずつ入力してください。"
                "ここに登録した場所は配下へ入らず、内容の読み取り、ハッシュ比較、"
                "バックアップ、復元、アップロード、削除を一切行いません。"
            )
        else:
            intro = QLabel(
                "静的サイトは原則としてすべてのファイルを同期します。XServer上に残し、"
                "同期処理で読み取り・比較・変更したくないファイル／フォルダだけを、XServer側の同期先フォルダ"
                "直下からの相対パスで"
                "1行ずつ入力してください（空欄のまま保存できます）。"
            )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if require_confirmation:
            defaults = "\n".join(
                f"• {path}" for path in get_preset(SiteType.CUSTOM_PHP).protect_patterns
            )
            default_label = QLabel("XServer側の次の場所は常に自動保護されます。\n" + defaults)
        else:
            default_label = QLabel(
                "この画面で追加設定する保護対象に既定値はありません。\n"
                "空欄の場合、XServer側の追加項目は保護しません。"
            )
        default_label.setWordWrap(True)
        default_label.setStyleSheet(PANEL_STYLE)
        self.default_label = default_label
        layout.addWidget(default_label)

        listing_row = QHBoxLayout()
        listing_row.addStretch(1)
        self.load_remote_button = QPushButton("XServerから一覧を取得…")
        self.load_remote_button.setEnabled(remote_listing_provider is not None)
        self.load_remote_button.setToolTip(
            "XServer側の同期先を読み取り、保護対象を選択します"
        )
        self.load_remote_button.clicked.connect(self._load_remote_entries)
        listing_row.addWidget(self.load_remote_button)
        layout.addLayout(listing_row)

        layout.addWidget(
            QLabel(
                "XServer側にあるその他の本番データ用ファイル／フォルダ（1行に1つ）"
                if require_confirmation
                else "XServer側で一切処理しないファイル／フォルダ（1行に1つ・空欄可）"
            )
        )
        self.paths_edit = QPlainTextEdit()
        self.paths_edit.setPlaceholderText(
            "userfiles/\ndata/sessions/" if require_confirmation else ".well-known/\ncgi-bin/"
        )
        self.paths_edit.setPlainText("\n".join(patterns))
        layout.addWidget(self.paths_edit, 1)

        self.confirm = QCheckBox(
            "本番で書き込まれる場所を確認し、上記の自動保護と追加一覧に漏れがないことを確認しました"
        )
        self.confirm.setChecked(confirmed)
        self.confirm.setVisible(require_confirmation)
        layout.addWidget(self.confirm)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        ok_button.setText("確認済みとして保存" if require_confirmation else "保存")
        ok_button.setEnabled(confirmed or not require_confirmation)
        if require_confirmation:
            self.confirm.toggled.connect(ok_button.setEnabled)
        self.buttons.accepted.connect(self._accept_paths)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @Slot()
    def _load_remote_entries(self) -> None:
        if self.remote_listing_provider is None or self._listing_thread is not None:
            return
        self.load_remote_button.setEnabled(False)
        self.load_remote_button.setText("XServerから取得中…")
        self.buttons.setEnabled(False)

        thread = QThread(self)
        current_patterns = tuple(
            line.strip()
            for line in self.paths_edit.toPlainText().splitlines()
            if line.strip()
        )
        worker = RemoteListingWorker(
            lambda: self.remote_listing_provider(False, current_patterns)
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._remote_entries_loaded)
        worker.failed.connect(self._remote_entries_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._remote_listing_finished)
        self._listing_thread = thread
        self._listing_worker = worker
        thread.start()

    @Slot(object)
    def _remote_entries_loaded(
        self,
        entries: tuple[RemoteProtectionEntry, ...],
    ) -> None:
        if not entries:
            QMessageBox.information(
                self,
                "XServer側の同期先",
                "同期先フォルダにファイルまたはフォルダがありません。",
            )
            return
        current_patterns = tuple(
            line.strip()
            for line in self.paths_edit.toPlainText().splitlines()
            if line.strip()
        )
        picker = RemoteProtectionPickerDialog(
            entries,
            current_patterns,
            lambda: self.remote_listing_provider(True, current_patterns),
            self,
        )
        if picker.exec() != QDialog.Accepted:
            return

        entries = picker.entries
        available_patterns = {entry.protection_pattern for entry in entries}
        preserved = [
            pattern for pattern in current_patterns if pattern not in available_patterns
        ]
        selected = picker.selected_patterns()
        combined = tuple(dict.fromkeys((*preserved, *selected)))
        self.paths_edit.setPlainText("\n".join(combined))
        self._known_remote_files.update(
            entry.relative_path
            for entry in entries
            if not entry.is_directory and entry.relative_path in selected
        )

    @Slot(str)
    def _remote_entries_failed(self, message: str) -> None:
        QMessageBox.warning(
            self,
            "XServerから一覧を取得できません",
            message,
        )

    @Slot()
    def _remote_listing_finished(self) -> None:
        self._listing_thread = None
        self._listing_worker = None
        self.load_remote_button.setText("XServerから一覧を取得…")
        self.load_remote_button.setEnabled(self.remote_listing_provider is not None)
        self.buttons.setEnabled(True)

    def _accept_paths(self) -> None:
        normalized: list[str] = []
        for line_number, value in enumerate(self.paths_edit.toPlainText().splitlines(), start=1):
            if not value.strip():
                continue
            try:
                stripped = value.strip()
                path = (
                    normalize_protected_file(stripped)
                    if stripped in self._known_remote_files
                    else normalize_runtime_directory(stripped)
                )
            except ValueError as exc:
                QMessageBox.warning(self, "パスを確認", f"{line_number}行目: {exc}")
                return
            if path not in normalized:
                normalized.append(path)
        self.patterns = tuple(normalized)
        self.accept()
