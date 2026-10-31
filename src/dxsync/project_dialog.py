from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .models import Project, SiteType, SyncMapping, utc_now
from .storage import ProjectStore
from .validation import ValidationIssue, validate_project


SITE_LABELS = {
    SiteType.WORDPRESS: "WordPress",
    SiteType.LARAVEL: "Laravel",
    SiteType.CUSTOM_PHP: "独自 PHP",
}


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
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.site_combo = QComboBox()
        for site_type, label in SITE_LABELS.items():
            self.site_combo.addItem(label, site_type.value)
        self.domain_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(10022)
        self.user_edit = QLineEdit()
        self.fingerprint_edit = QLineEdit()
        self.fingerprint_edit.setPlaceholderText("SHA256:...")
        self.key_edit = QLineEdit()
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_button = QPushButton("選択…")
        key_button.clicked.connect(self._browse_key)
        key_row.addWidget(key_button)

        form.addRow("表示名", self.name_edit)
        form.addRow("サイト種別", self.site_combo)
        form.addRow("対象ドメイン", self.domain_edit)
        form.addRow("SSHホスト", self.host_edit)
        form.addRow("SSHポート", self.port_spin)
        form.addRow("SSHユーザー", self.user_edit)
        form.addRow("ホスト鍵", self.fingerprint_edit)
        form.addRow("秘密鍵ファイル", key_row)
        layout.addLayout(form)

        mapping_header = QHBoxLayout()
        mapping_header.addWidget(QLabel("パスマッピング"))
        mapping_header.addStretch(1)
        add_mapping = QPushButton("追加")
        add_mapping.clicked.connect(self._add_mapping)
        remove_mapping = QPushButton("選択行を削除")
        remove_mapping.clicked.connect(self._remove_mapping)
        mapping_header.addWidget(add_mapping)
        mapping_header.addWidget(remove_mapping)
        layout.addLayout(mapping_header)

        self.mapping_table = QTableWidget(0, 3)
        self.mapping_table.setHorizontalHeaderLabels(("名前", "ローカルルート", "リモートルート"))
        header = self.mapping_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.mapping_table, 1)

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
        else:
            self._append_mapping("public", "", "")

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
            )

    def _append_mapping(
        self,
        name: str,
        local_root: str,
        remote_root: str,
        mapping_id: str | None = None,
    ) -> None:
        row = self.mapping_table.rowCount()
        self.mapping_table.insertRow(row)
        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.UserRole, mapping_id)
        self.mapping_table.setItem(row, 0, name_item)
        self.mapping_table.setItem(row, 1, QTableWidgetItem(local_root))
        self.mapping_table.setItem(row, 2, QTableWidgetItem(remote_root))

    def _add_mapping(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "ローカルルートを選択")
        self._append_mapping(f"mapping-{self.mapping_table.rowCount() + 1}", directory, "")

    def _remove_mapping(self) -> None:
        rows = sorted({index.row() for index in self.mapping_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.mapping_table.removeRow(row)

    def _browse_key(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "SSH秘密鍵を選択")
        if selected:
            self.key_edit.setText(selected)

    def _read_mappings(self) -> tuple[SyncMapping, ...]:
        mappings: list[SyncMapping] = []
        for row in range(self.mapping_table.rowCount()):
            cells = [self.mapping_table.item(row, column) for column in range(3)]
            values = [cell.text().strip() if cell else "" for cell in cells]
            mapping_id = cells[0].data(Qt.UserRole) if cells[0] else None
            original = self._original_mappings.get(mapping_id)
            if original:
                mappings.append(
                    replace(
                        original,
                        name=values[0],
                        local_root=values[1],
                        remote_root=values[2],
                    )
                )
            else:
                mappings.append(SyncMapping.create(values[0], values[1], values[2]))
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
        self.store.save(project)
        self.saved_project = project
        self.accept()
