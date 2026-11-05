from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
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
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .models import Project, SiteType, SyncMapping, utc_now
from .presets import get_preset
from .storage import ProjectStore
from .secure_store import SecureStoreError
from .validation import ValidationIssue, normalize_runtime_directory, validate_project


SITE_LABELS = {
    SiteType.CUSTOM_PHP: "独自 PHP（上記以外）",
    SiteType.WORDPRESS: "WordPress（wp-contentがある）",
    SiteType.LARAVEL: "Laravel（artisanがある）",
}


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #57606a; font-size: 11px;")
    return label


class RuntimePathsDialog(QDialog):
    def __init__(
        self,
        *,
        patterns: tuple[str, ...] = (),
        confirmed: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.patterns = patterns
        self.setWindowTitle("本番で書き込まれるディレクトリ")
        self.resize(620, 500)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "XServer上でアップロード、ログ、キャッシュなどが保存される"
            "ディレクトリを、同期元ルートからの相対パスで1行ずつ入力してください。"
            "ここに登録した場所は走査・アップロード・削除の対象にしません。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        defaults = "\n".join(f"• {path}" for path in get_preset(SiteType.CUSTOM_PHP).protect_patterns)
        default_label = QLabel("次の場所は常に自動保護されます。\n" + defaults)
        default_label.setWordWrap(True)
        default_label.setStyleSheet("background: #f6f8fa; padding: 8px;")
        layout.addWidget(default_label)

        layout.addWidget(QLabel("その他の本番書き込みディレクトリ（1行に1つ）"))
        self.paths_edit = QPlainTextEdit()
        self.paths_edit.setPlaceholderText("userfiles/\ndata/sessions/")
        self.paths_edit.setPlainText("\n".join(patterns))
        layout.addWidget(self.paths_edit, 1)

        self.confirm = QCheckBox(
            "本番で書き込まれる場所を確認し、上記の自動保護と追加一覧に漏れがないことを確認しました"
        )
        self.confirm.setChecked(confirmed)
        layout.addWidget(self.confirm)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("確認済みとして保存")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(confirmed)
        self.confirm.toggled.connect(self.buttons.button(QDialogButtonBox.Ok).setEnabled)
        self.buttons.accepted.connect(self._accept_paths)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _accept_paths(self) -> None:
        normalized: list[str] = []
        for line_number, value in enumerate(self.paths_edit.toPlainText().splitlines(), start=1):
            if not value.strip():
                continue
            try:
                path = normalize_runtime_directory(value)
            except ValueError as exc:
                QMessageBox.warning(self, "パスを確認", f"{line_number}行目: {exc}")
                return
            if path not in normalized:
                normalized.append(path)
        self.patterns = tuple(normalized)
        self.accept()


class ProjectDialog(QDialog):
    def __init__(
        self,
        store: ProjectStore,
        project: Project | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.original = project
        self.saved_project: Project | None = None
        self._original_mappings = {
            mapping.mapping_id: mapping for mapping in project.mappings
        } if project else {}
        self.setWindowTitle("プロジェクトを編集" if project else "プロジェクトを作成")
        self.resize(860, 560)

        layout = QVBoxLayout(self)
        intro_row = QHBoxLayout()
        intro = QLabel("分からない値は、右の「入力項目の確認方法」で場所と例を確認できます。")
        intro.setWordWrap(True)
        intro_row.addWidget(intro, 1)
        help_button = QPushButton("入力項目の確認方法")
        help_button.clicked.connect(self._show_setup_help)
        intro_row.addWidget(help_button)
        layout.addLayout(intro_row)

        form = QFormLayout()
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

        form.addRow("プロジェクト名", self.name_edit)
        form.addRow("", _hint("自分が見分けるための名前です。XServerの設定には影響しません。"))
        form.addRow("サイトの種類", self.site_combo)
        form.addRow("", self.site_help)
        form.addRow("公開ドメイン", self.domain_edit)
        form.addRow("", _hint("テスト先があるドメイン名。https://は付けません。"))
        form.addRow("SSHホスト", self.host_edit)
        form.addRow("", _hint("XServer サーバーパネル → SSH設定 → SSHソフト設定で確認します。"))
        form.addRow("SSHポート", self.port_spin)
        form.addRow("SSHユーザー", self.user_edit)
        form.addRow("", _hint("通常はサーバーIDです。独自ドメイン名ではありません。"))
        form.addRow("接続先の確認コード", self.fingerprint_edit)
        form.addRow("", _hint("SSHソフト設定に表示されるSHA256:から始まるホスト鍵フィンガープリントです。"))
        form.addRow("秘密鍵ファイル", key_row)
        form.addRow("", _hint("XServerへ公開鍵を登録したときにPCへ保存した秘密鍵。.pubファイルではありません。"))
        layout.addLayout(form)

        self.mapping_header = QHBoxLayout()
        self.mapping_header.addWidget(QLabel("同期するフォルダの組み合わせ"))
        self.mapping_header.addStretch(1)
        self.add_mapping_button = QPushButton("PC側フォルダを追加…")
        self.add_mapping_button.clicked.connect(self._add_mapping)
        self.mapping_header.addWidget(self.add_mapping_button)
        self.runtime_paths_button = QPushButton("本番書き込み先を設定…")
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
                "XServer側のテストフォルダ（絶対パス）",
                "本番書き込み先",
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
            "XServer側は例: /home/SERVER_ID/example.com/public_html/sync-test。"
            "テストフォルダは事前に作成してください。"
        )
        layout.addWidget(mapping_help)

        note = QLabel(
            "パスフレーズは保存しません。Laravelはアプリ本体とpublicを別マッピングにできます。"
        )
        note.setStyleSheet("color: #57606a;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if project:
            self._load(project)
        self._site_changed(self.site_combo.currentIndex())

    def _site_changed(self, index: int) -> None:
        site_type = SiteType(self.site_combo.itemData(index))
        messages = {
            SiteType.WORDPRESS: "フォルダ内にwp-content、wp-admin、wp-includesがあるサイト向けです。",
            SiteType.LARAVEL: "フォルダ内にartisan、app、routes、publicがあるサイト向けです。",
            SiteType.CUSTOM_PHP: "WordPressでもLaravelでもないPHPサイト向けです。",
        }
        self.site_help.setText(messages[site_type])
        self.runtime_paths_button.setEnabled(site_type is SiteType.CUSTOM_PHP)

    def _show_setup_help(self) -> None:
        QMessageBox.information(
            self,
            "入力項目の確認方法",
            "1. サイトの種類\n"
            "   wp-contentがあればWordPress、artisanがあればLaravel、それ以外は独自PHP。\n\n"
            "2. PC側フォルダ\n"
            "   compose.ymlの volumes: ./src:/var/www/html なら ./src を選択。\n\n"
            "3. SSHホスト・ユーザー\n"
            "   XServer サーバーパネル → SSH設定 → SSHソフト設定で確認。\n"
            "   一般にホストはSERVER_ID.xsrv.jp、ユーザーはSERVER_ID、ポートは10022。\n\n"
            "4. 接続先の確認コード\n"
            "   同じ画面にあるSHA256:から始まるホスト鍵をコピー。\n\n"
            "5. 秘密鍵\n"
            "   公開鍵登録時に保存した秘密鍵。.pubは選ばない。\n\n"
            "6. XServer側テストフォルダ\n"
            "   例: /home/SERVER_ID/example.com/public_html/sync-test\n"
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
        patterns = tuple(item.data(Qt.UserRole) or ())
        confirmed = bool(item.data(Qt.UserRole + 1))
        if not confirmed:
            item.setText("未確認（同期不可）")
            item.setForeground(QColor("#cf222e"))
        elif patterns:
            item.setText(f"確認済み（追加 {len(patterns)}件）")
            item.setToolTip("\n".join(patterns))
            item.setForeground(QColor("#1a7f37"))
        else:
            item.setText("確認済み（自動保護のみ）")
            item.setForeground(QColor("#1a7f37"))

    def _edit_runtime_paths(self) -> None:
        row = self.mapping_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "同期先を選択", "設定する同期フォルダの行を選択してください。")
            return
        item = self.mapping_table.item(row, 3)
        if item is None:
            return
        dialog = RuntimePathsDialog(
            patterns=tuple(item.data(Qt.UserRole) or ()),
            confirmed=bool(item.data(Qt.UserRole + 1)),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        item.setData(Qt.UserRole, dialog.patterns)
        item.setData(Qt.UserRole + 1, True)
        self._update_runtime_status(row)

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
