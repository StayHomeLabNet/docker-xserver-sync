from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .models import Project, SiteType, SyncMapping, utc_now
from .remote import connect_sftp
from .remote_listing import RemoteProtectionEntry, list_remote_protection_entries
from .runtime_paths_dialog import (
    RemoteListingWorker as RemoteListingWorker,
    RemoteProtectionPickerDialog as RemoteProtectionPickerDialog,
    RuntimePathsDialog as RuntimePathsDialog,
)
from .storage import ProjectStore
from .secure_store import SecureStoreError
from .ui_styles import MUTED_NOTE_STYLE, MUTED_TEXT_STYLE
from .validation import (
    ValidationIssue,
    validate_project,
)


SITE_LABELS = {
    SiteType.CUSTOM_PHP: "独自 PHP（上記以外）",
    SiteType.WORDPRESS: "WordPress（wp-contentがある）",
    SiteType.LARAVEL: "Laravel（artisanがある）",
    SiteType.STATIC_SITE: "静的サイト（HTML / ビルド済み）",
}


def _emphasize(item: QTableWidgetItem) -> None:
    font = item.font()
    font.setBold(True)
    item.setFont(font)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.PlainText)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    label.setStyleSheet(MUTED_NOTE_STYLE)
    return label


class _ConnectionTestWorker(QObject):
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, runner: Callable[[], str]) -> None:
        super().__init__()
        self.runner = runner

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self.runner())
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class ProjectDialog(QDialog):
    def __init__(
        self,
        store: ProjectStore,
        project: Project | None = None,
        parent=None,
        *,
        duplicate_from: Project | None = None,
        passphrase: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.original = project
        self.duplicate_from = duplicate_from
        self.passphrase = passphrase
        self.saved_project: Project | None = None
        self._connection_test_thread: QThread | None = None
        self._connection_test_worker: _ConnectionTestWorker | None = None
        self._original_mappings = {
            mapping.mapping_id: mapping for mapping in project.mappings
        } if project else {}
        if duplicate_from:
            self.setWindowTitle("プロジェクトを複製")
        elif project:
            self.setWindowTitle("プロジェクトを編集")
        else:
            self.setWindowTitle("プロジェクトを作成")
        self.resize(960, 720)
        self.setMinimumSize(760, 560)

        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area = scroll
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)
        intro_row = QHBoxLayout()
        self.intro_row = intro_row
        intro = QLabel("分からない値は、右の「入力項目の確認方法」で場所と例を確認できます。")
        intro.setWordWrap(True)
        intro_row.addWidget(intro, 1)
        self.duplicate_button = QPushButton("複製…")
        self.duplicate_button.setToolTip(
            "このプロジェクトの設定を複製して新しいプロジェクトを作成します"
        )
        self.duplicate_button.clicked.connect(self._duplicate_project)
        self.duplicate_button.setVisible(bool(project))
        intro_row.addWidget(self.duplicate_button)
        self.test_connection_button = QPushButton("接続テスト…")
        self.test_connection_button.setToolTip(
            "入力したSSHホスト・秘密鍵・確認コード・同期先フォルダへ接続できるか確認します"
        )
        self.test_connection_button.clicked.connect(self._test_connection)
        intro_row.addWidget(self.test_connection_button)
        help_button = QPushButton("入力項目の確認方法")
        self.help_button = help_button
        help_button.clicked.connect(self._show_setup_help)
        intro_row.addWidget(help_button)
        layout.addLayout(intro_row)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(6)
        self.details_form = form
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例: example.com テスト")
        self.site_combo = QComboBox()
        for site_type, label in SITE_LABELS.items():
            self.site_combo.addItem(label, site_type.value)
        self.site_help = _hint("")
        self.site_combo.currentIndexChanged.connect(self._site_changed)
        self.domain_edit = QLineEdit()
        self.domain_edit.setPlaceholderText("例: example.com")
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("例: SERVER_ID.xsrv.jp")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(10022)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("通常はXServerのサーバーID")
        self.fingerprint_edit = QLineEdit()
        self.fingerprint_edit.setPlaceholderText("サーバーパネルからSHA256:...をコピー")
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText(".pubではない秘密鍵ファイル")
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_button = QPushButton("選択…")
        key_button.clicked.connect(self._browse_key)
        key_row.addWidget(key_button)

        for editor in (
            self.name_edit,
            self.site_combo,
            self.domain_edit,
            self.host_edit,
            self.user_edit,
            self.fingerprint_edit,
            self.key_edit,
        ):
            editor.setMinimumWidth(440)

        form.addRow("プロジェクト名", self.name_edit)
        form.addRow(_hint("自分が見分けるための名前です。XServerの設定には影響しません。"))
        form.addRow("サイトの種類", self.site_combo)
        form.addRow(self.site_help)
        form.addRow("公開ドメイン", self.domain_edit)
        form.addRow(_hint("同期先サイトのドメイン名。https://は付けません。"))
        form.addRow("SSHホスト", self.host_edit)
        form.addRow(_hint("XServer サーバーパネル → SSH設定 → SSHソフト設定で確認します。"))
        form.addRow("SSHポート", self.port_spin)
        form.addRow("SSHユーザー", self.user_edit)
        form.addRow(_hint("通常はサーバーIDです。独自ドメイン名ではありません。"))
        form.addRow("接続先の確認コード", self.fingerprint_edit)
        form.addRow(_hint("SSHソフト設定に表示されるSHA256:から始まるホスト鍵フィンガープリントです。"))
        form.addRow("秘密鍵ファイル", key_row)
        form.addRow(_hint("XServerへ公開鍵を登録したときにPCへ保存した秘密鍵。.pubファイルではありません。"))
        layout.addLayout(form)

        self.mapping_header = QHBoxLayout()
        self.mapping_header.addWidget(QLabel("同期するフォルダの組み合わせ"))
        self.mapping_header.addStretch(1)
        self.add_mapping_button = QPushButton("PC側フォルダを追加…")
        self.add_mapping_button.clicked.connect(self._add_mapping)
        self.mapping_header.addWidget(self.add_mapping_button)
        self.runtime_paths_button = QPushButton("XServer側の保護対象を設定…")
        self.runtime_paths_button.clicked.connect(self._edit_runtime_paths)
        self.mapping_header.addWidget(self.runtime_paths_button)
        remove_mapping = QPushButton("選択行を削除")
        remove_mapping.clicked.connect(self._remove_mapping)
        self.mapping_header.addWidget(remove_mapping)
        layout.addLayout(self.mapping_header)

        self.mapping_table = QTableWidget(0, 4)
        self.mapping_table.setHorizontalHeaderLabels(
            (
                "用途",
                "PC側の同期元フォルダ",
                "XServer側の同期先フォルダ（絶対パス）",
                "XServer側の保護設定",
            )
        )
        header = self.mapping_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.mapping_table, 1)

        mapping_help = _hint(
            "PC側はcompose.ymlのvolumesでコロンより左のフォルダを選びます。"
            "XServer側は同期先の絶対パス（例: /home/SERVER_ID/example.com/public_html）。"
            "初回はテスト用フォルダを推奨します。同期先フォルダは事前に作成してください。"
        )
        layout.addWidget(mapping_help)

        note = QLabel(
            "パスフレーズは保存しません。Laravelはアプリ本体とpublicを別マッピングにできます。"
        )
        note.setStyleSheet(MUTED_TEXT_STYLE)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer_layout.addWidget(buttons)

        if project:
            self._load(project)
        elif duplicate_from:
            self._load_duplicate(duplicate_from)
        self._site_changed(self.site_combo.currentIndex())

    def _selected_site_type(self) -> SiteType:
        value = self.site_combo.currentData()
        return SiteType(value) if value else SiteType.CUSTOM_PHP

    def _site_changed(self, index: int) -> None:
        site_type = SiteType(self.site_combo.itemData(index))
        messages = {
            SiteType.WORDPRESS: "フォルダ内にwp-content、wp-admin、wp-includesがあるサイト向けです。",
            SiteType.LARAVEL: "フォルダ内にartisan、app、routes、publicがあるサイト向けです。",
            SiteType.CUSTOM_PHP: "WordPressでもLaravelでもないPHPサイト向けです。",
            SiteType.STATIC_SITE: "HTML・CSS・JSやビルド済みdist/フォルダ（Astro / Vite / Hugo / 静的化WP等）向けです。",
        }
        self.site_help.setText(messages[site_type])
        if site_type is SiteType.STATIC_SITE:
            self.runtime_paths_button.setText("XServer側の保護対象を設定…")
            self.runtime_paths_button.setEnabled(True)
        else:
            self.runtime_paths_button.setText("XServer側の保護対象を設定…")
            self.runtime_paths_button.setEnabled(site_type is SiteType.CUSTOM_PHP)
        for row in range(self.mapping_table.rowCount()):
            self._update_runtime_status(row)

    def _show_setup_help(self) -> None:
        QMessageBox.information(
            self,
            "入力項目の確認方法",
            "1. サイトの種類\n"
            "   wp-contentがあればWordPress、artisanがあればLaravel、\n"
            "   HTML/ビルド済みdist/は静的サイト、それ以外のPHPは独自PHP。\n\n"
            "2. PC側フォルダ\n"
            "   compose.ymlの volumes: ./src:/var/www/html なら ./src 、\n"
            "   静的サイトならビルド出力先（例: ./dist）を選択。\n\n"
            "3. SSHホスト・ユーザー\n"
            "   XServer サーバーパネル → SSH設定 → SSHソフト設定で確認。\n"
            "   一般にホストはSERVER_ID.xsrv.jp、ユーザーはSERVER_ID、ポートは10022。\n\n"
            "4. 接続先の確認コード\n"
            "   同じ画面にあるSHA256:から始まるホスト鍵をコピー。\n\n"
            "5. 秘密鍵\n"
            "   公開鍵登録時に保存した秘密鍵。.pubは選ばない。\n\n"
            "6. XServer側の同期先フォルダ\n"
            "   例: /home/SERVER_ID/example.com/public_html\n"
            "   初回はpublic_html内のテスト用フォルダを同期先にすることを推奨します。\n"
            "   SSHで対象フォルダへ移動し、pwdを実行すると絶対パスを確認できます。",
        )

    def _load(self, project: Project) -> None:
        self.name_edit.setText(project.name)
        index = self.site_combo.findData(project.site_type.value)
        self.site_combo.setCurrentIndex(max(index, 0))
        self.domain_edit.setText(project.domain)
        self.host_edit.setText(project.ssh_host)
        self.port_spin.setValue(project.ssh_port)
        self.user_edit.setText(project.ssh_username)
        self.fingerprint_edit.setText(project.host_key_fingerprint)
        self.key_edit.setText(project.private_key_reference)
        for mapping in project.mappings:
            self._append_mapping(
                mapping.name,
                mapping.local_root,
                mapping.remote_root,
                mapping.mapping_id,
                mapping.protect_patterns,
                mapping.runtime_paths_confirmed,
            )

    def _generate_duplicate_name(self, base_name: str) -> str:
        existing_names = {p.name for p in self.store.list_projects()}
        candidate = f"{base_name} のコピー"
        if candidate not in existing_names:
            return candidate
        index = 2
        while f"{base_name} のコピー {index}" in existing_names:
            index += 1
        return f"{base_name} のコピー {index}"

    def _load_duplicate(self, project: Project) -> None:
        self.name_edit.setText(self._generate_duplicate_name(project.name))
        index = self.site_combo.findData(project.site_type.value)
        self.site_combo.setCurrentIndex(max(index, 0))
        self.domain_edit.setText(project.domain)
        self.host_edit.setText(project.ssh_host)
        self.port_spin.setValue(project.ssh_port)
        self.user_edit.setText(project.ssh_username)
        self.fingerprint_edit.setText(project.host_key_fingerprint)
        self.key_edit.setText(project.private_key_reference)
        for mapping in project.mappings:
            self._append_mapping(
                mapping.name,
                mapping.local_root,
                mapping.remote_root,
                None,
                mapping.protect_patterns,
                mapping.runtime_paths_confirmed,
            )

    def _build_snapshot(self) -> Project | None:
        mappings = self._read_mappings()
        if not mappings and self.original:
            mappings = self.original.mappings
        if not mappings and self.duplicate_from:
            mappings = self.duplicate_from.mappings
        base_name = self.name_edit.text().strip() or (
            self.original.name
            if self.original
            else (self.duplicate_from.name if self.duplicate_from else "プロジェクト")
        )
        site_type_val = self.site_combo.currentData()
        site_type = (
            SiteType(site_type_val)
            if site_type_val
            else (self.original.site_type if self.original else SiteType.CUSTOM_PHP)
        )
        return Project(
            project_id=self.original.project_id if self.original else "",
            name=base_name,
            site_type=site_type,
            domain=self.domain_edit.text().strip() or (self.original.domain if self.original else ""),
            ssh_host=self.host_edit.text().strip() or (self.original.ssh_host if self.original else ""),
            ssh_port=self.port_spin.value(),
            ssh_username=self.user_edit.text().strip() or (self.original.ssh_username if self.original else ""),
            host_key_fingerprint=self.fingerprint_edit.text().strip() or (self.original.host_key_fingerprint if self.original else ""),
            private_key_reference=self.key_edit.text().strip() or (self.original.private_key_reference if self.original else ""),
            mappings=mappings,
        )

    @Slot()
    def _duplicate_project(self) -> None:
        source_project = self._build_snapshot()
        if source_project is None:
            source_project = self.original
        if source_project is None:
            return
        dialog = ProjectDialog(
            self.store,
            duplicate_from=source_project,
            passphrase=self.passphrase,
            parent=self.parent() or self,
        )
        if dialog.exec() == QDialog.Accepted and dialog.saved_project is not None:
            self.saved_project = dialog.saved_project
            self.accept()

    def _append_mapping(
        self,
        name: str,
        local_root: str,
        remote_root: str,
        mapping_id: str | None = None,
        protect_patterns: tuple[str, ...] = (),
        runtime_paths_confirmed: bool = False,
    ) -> None:
        row = self.mapping_table.rowCount()
        self.mapping_table.insertRow(row)
        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.UserRole, mapping_id)
        self.mapping_table.setItem(row, 0, name_item)
        self.mapping_table.setItem(row, 1, QTableWidgetItem(local_root))
        self.mapping_table.setItem(row, 2, QTableWidgetItem(remote_root))
        status_item = QTableWidgetItem()
        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
        status_item.setData(Qt.UserRole, protect_patterns)
        status_item.setData(Qt.UserRole + 1, runtime_paths_confirmed)
        self.mapping_table.setItem(row, 3, status_item)
        self._update_runtime_status(row)

    def _add_mapping(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "ローカルルートを選択")
        if not directory:
            return
        default_name = Path(directory).name or f"mapping-{self.mapping_table.rowCount() + 1}"
        self._append_mapping(default_name, directory, "")
        row = self.mapping_table.rowCount() - 1
        remote_item = self.mapping_table.item(row, 2)
        if remote_item:
            remote_item.setToolTip(
                "例: /home/SERVER_ID/example.com/public_html/sync-test"
            )
        self.mapping_table.editItem(remote_item)

    def _remove_mapping(self) -> None:
        rows = sorted({index.row() for index in self.mapping_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.mapping_table.removeRow(row)

    def _update_runtime_status(self, row: int) -> None:
        item = self.mapping_table.item(row, 3)
        if item is None:
            return
        site_type = self._selected_site_type()
        patterns = tuple(item.data(Qt.UserRole) or ())
        confirmed = bool(item.data(Qt.UserRole + 1))
        if site_type is SiteType.STATIC_SITE:
            if patterns:
                item.setText(f"追加保護 {len(patterns)}件")
                item.setToolTip("\n".join(patterns))
            else:
                item.setText("追加保護なし")
                item.setToolTip("")
            _emphasize(item)
        elif site_type is SiteType.CUSTOM_PHP:
            if not confirmed:
                item.setText("未確認（同期不可）")
                item.setToolTip("")
                _emphasize(item)
            elif patterns:
                item.setText(f"確認済み（追加 {len(patterns)}件）")
                item.setToolTip("\n".join(patterns))
                _emphasize(item)
            else:
                item.setText("確認済み（自動保護のみ）")
                item.setToolTip("")
                _emphasize(item)
        else:
            if patterns:
                item.setText(f"自動保護＋追加 {len(patterns)}件")
                item.setToolTip("\n".join(patterns))
            else:
                item.setText("自動保護（確認不要）")
                item.setToolTip("")
            _emphasize(item)

    def _edit_runtime_paths(self) -> None:
        row = self.mapping_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "同期先を選択", "設定する同期フォルダの行を選択してください。")
            return
        item = self.mapping_table.item(row, 3)
        if item is None:
            return
        site_type = self._selected_site_type()
        require_confirmation = site_type is not SiteType.STATIC_SITE
        dialog = RuntimePathsDialog(
            patterns=tuple(item.data(Qt.UserRole) or ()),
            confirmed=bool(item.data(Qt.UserRole + 1)),
            require_confirmation=require_confirmation,
            remote_listing_provider=self._remote_listing_provider(row),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        item.setData(Qt.UserRole, dialog.patterns)
        if require_confirmation:
            item.setData(Qt.UserRole + 1, True)
        self._update_runtime_status(row)

    def _remote_listing_provider(
        self,
        row: int,
    ) -> Callable[[bool, tuple[str, ...]], tuple[RemoteProtectionEntry, ...]]:
        remote_item = self.mapping_table.item(row, 2)
        remote_root = remote_item.text().strip() if remote_item else ""
        host = self.host_edit.text().strip()
        port = self.port_spin.value()
        username = self.user_edit.text().strip()
        private_key_path = self.key_edit.text().strip()
        fingerprint = self.fingerprint_edit.text().strip()
        passphrase = self.passphrase

        def load(
            recursive: bool,
            protect_patterns: tuple[str, ...],
        ) -> tuple[RemoteProtectionEntry, ...]:
            missing = [
                label
                for label, value in (
                    ("SSHホスト", host),
                    ("SSHユーザー", username),
                    ("接続先の確認コード", fingerprint),
                    ("秘密鍵ファイル", private_key_path),
                    ("XServer側の同期先フォルダ", remote_root),
                )
                if not value
            ]
            if missing:
                raise ValueError("先に次の項目を入力してください: " + "、".join(missing))
            client = None
            sftp = None
            try:
                client, sftp = connect_sftp(
                    host=host,
                    port=port,
                    username=username,
                    private_key_path=private_key_path,
                    host_key_fingerprint=fingerprint,
                    passphrase=passphrase,
                )
                return list_remote_protection_entries(
                    sftp,
                    remote_root,
                    recursive=recursive,
                    skip_patterns=protect_patterns,
                )
            finally:
                if sftp is not None:
                    try:
                        sftp.close()
                    except Exception:
                        pass
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass

        return load

    @Slot()
    def _test_connection(self) -> None:
        if self._connection_test_thread is not None:
            return
        host = self.host_edit.text().strip()
        port = self.port_spin.value()
        username = self.user_edit.text().strip()
        private_key_path = self.key_edit.text().strip()
        fingerprint = self.fingerprint_edit.text().strip()
        mappings = self._read_mappings()
        remote_roots = tuple(
            mapping.remote_root for mapping in mappings if mapping.remote_root
        )
        missing = [
            label
            for label, value in (
                ("SSHホスト", host),
                ("SSHユーザー", username),
                ("接続先の確認コード", fingerprint),
                ("秘密鍵ファイル", private_key_path),
            )
            if not value
        ]
        if missing:
            QMessageBox.warning(
                self,
                "接続テストできません",
                "先に次の項目を入力してください:\n"
                + "\n".join(f"• {label}" for label in missing),
            )
            return
        if not Path(private_key_path).expanduser().is_file():
            QMessageBox.warning(
                self,
                "接続テストできません",
                f"秘密鍵ファイルが見つかりません: {private_key_path}",
            )
            return

        passphrase = self.passphrase
        self.test_connection_button.setEnabled(False)
        self.test_connection_button.setText("接続確認中…")

        def run_check() -> str:
            import stat

            client = None
            sftp = None
            try:
                client, sftp = connect_sftp(
                    host=host,
                    port=port,
                    username=username,
                    private_key_path=str(Path(private_key_path).expanduser()),
                    host_key_fingerprint=fingerprint,
                    passphrase=passphrase,
                )
                checked: list[str] = []
                for remote_root in remote_roots:
                    info = sftp.stat(remote_root)
                    if not stat.S_ISDIR(info.st_mode):
                        raise RuntimeError(
                            f"同期先パスがフォルダではありません: {remote_root}"
                        )
                    checked.append(remote_root)
                if checked:
                    return (
                        "SSH接続とホスト鍵確認コード、同期先フォルダの確認に成功しました。\n\n"
                        + "\n".join(f"• {path}" for path in checked)
                    )
                return "SSH接続とホスト鍵確認コードの確認に成功しました。"
            finally:
                if sftp is not None:
                    try:
                        sftp.close()
                    except Exception:
                        pass
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass

        thread = QThread(self)
        worker = _ConnectionTestWorker(run_check)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._connection_test_succeeded)
        worker.failed.connect(self._connection_test_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._connection_test_finished)
        self._connection_test_thread = thread
        self._connection_test_worker = worker
        thread.start()

    @Slot(str)
    def _connection_test_succeeded(self, message: str) -> None:
        QMessageBox.information(self, "接続テスト成功", message)

    @Slot(str)
    def _connection_test_failed(self, message: str) -> None:
        QMessageBox.warning(self, "接続テスト失敗", message)

    @Slot()
    def _connection_test_finished(self) -> None:
        self._connection_test_thread = None
        self._connection_test_worker = None
        self.test_connection_button.setText("接続テスト…")
        self.test_connection_button.setEnabled(True)

    def _browse_key(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "SSH秘密鍵を選択")
        if selected:
            self.key_edit.setText(selected)

    def _read_mappings(self) -> tuple[SyncMapping, ...]:
        mappings: list[SyncMapping] = []
        for row in range(self.mapping_table.rowCount()):
            cells = [self.mapping_table.item(row, column) for column in range(4)]
            values = [cell.text().strip() if cell else "" for cell in cells[:3]]
            mapping_id = cells[0].data(Qt.UserRole) if cells[0] else None
            protect_patterns = tuple(cells[3].data(Qt.UserRole) or ()) if cells[3] else ()
            runtime_paths_confirmed = bool(cells[3].data(Qt.UserRole + 1)) if cells[3] else False
            original = self._original_mappings.get(mapping_id)
            if original:
                mappings.append(
                    replace(
                        original,
                        name=values[0],
                        local_root=values[1],
                        remote_root=values[2],
                        protect_patterns=protect_patterns,
                        runtime_paths_confirmed=runtime_paths_confirmed,
                    )
                )
            else:
                mappings.append(
                    replace(
                        SyncMapping.create(values[0], values[1], values[2]),
                        protect_patterns=protect_patterns,
                        runtime_paths_confirmed=runtime_paths_confirmed,
                    )
                )
        return tuple(mappings)

    def _save(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "入力を確認", "表示名を入力してください。")
            return
        mappings = self._read_mappings()
        if not mappings:
            QMessageBox.warning(self, "入力を確認", "パスマッピングを1件以上追加してください。")
            return

        now = utc_now()
        if self.original:
            project = replace(
                self.original,
                name=self.name_edit.text().strip(),
                site_type=SiteType(self.site_combo.currentData()),
                domain=self.domain_edit.text().strip(),
                ssh_host=self.host_edit.text().strip(),
                ssh_port=self.port_spin.value(),
                ssh_username=self.user_edit.text().strip(),
                host_key_fingerprint=self.fingerprint_edit.text().strip(),
                private_key_reference=self.key_edit.text().strip(),
                mappings=mappings,
                updated_at=now,
            )
        else:
            project = Project.create(
                name=self.name_edit.text().strip(),
                site_type=SiteType(self.site_combo.currentData()),
                domain=self.domain_edit.text().strip(),
                ssh_host=self.host_edit.text().strip(),
                ssh_port=self.port_spin.value(),
                ssh_username=self.user_edit.text().strip(),
                host_key_fingerprint=self.fingerprint_edit.text().strip(),
                private_key_reference=self.key_edit.text().strip(),
                mappings=mappings,
            )

        issues = list(validate_project(project))
        if not Path(project.private_key_reference).expanduser().is_file():
            issues.append(
                ValidationIssue(
                    "private_key",
                    "秘密鍵ファイルが存在しません。",
                    "private_key_reference",
                )
            )
        if issues:
            QMessageBox.warning(
                self,
                "設定を保存できません",
                "\n".join(f"• {issue.message}" for issue in issues),
            )
            return
        try:
            self.store.save(project)
        except SecureStoreError as exc:
            QMessageBox.critical(
                self,
                "安全な保存領域を使用できません",
                "接続先の確認コードをOSの安全な保存領域へ登録できませんでした。\n\n"
                f"{exc}",
            )
            return
        self.saved_project = project
        self.accept()
