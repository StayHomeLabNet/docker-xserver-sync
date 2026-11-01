from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence

try:
    from PySide6.QtCore import (
        QAbstractTableModel,
        QModelIndex,
        QObject,
        QStandardPaths,
        Qt,
        QThread,
        Signal,
        Slot,
    )
    from PySide6.QtGui import QColor, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableView,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - exercised only without the GUI extra
    raise SystemExit(
        "PySide6 is required for the desktop app. Install with: uv sync --extra gui"
    ) from exc

from .models import ChangeType, FileRecord, Project, ScanResult, SiteType, SyncMapping
from .integrity import project_config_sha256
from .maintenance import cleanup_stale_staging
from .presets import get_preset
from .project_dialog import ProjectDialog
from .remote import connect_sftp
from .revalidation import PlanRevalidator, RevalidationResult
from .rules import PathRules
from .scanner import LocalScanner
from .service import DryRunResult, DryRunService
from .storage import ProjectStore, StoredPlan


SITE_LABELS = {
    SiteType.WORDPRESS: "WordPress",
    SiteType.LARAVEL: "Laravel",
    SiteType.CUSTOM_PHP: "独自 PHP",
}


def _format_size(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


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

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._files)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

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
                _format_size(record.size),
                record.sha256 or "—",
            )
            return values[index.column()]
        if role == Qt.ForegroundRole and is_protected:
            return QColor("#9a6700")
        if role == Qt.TextAlignmentRole and index.column() == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class ScanWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, root: Path, site_type: SiteType) -> None:
        super().__init__()
        self.root = root
        self.site_type = site_type

    @Slot()
    def run(self) -> None:
        try:
            preset = get_preset(self.site_type)
            self.completed.emit(LocalScanner(preset.exclude_patterns).scan(self.root))
        except Exception as exc:  # worker boundary: surface the failure in the UI
            self.failed.emit(str(exc))


class DryRunWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        root: Path,
        mapping_id: str,
        site_type: SiteType,
        host: str,
        port: int,
        username: str,
        private_key_path: str,
        fingerprint: str,
        remote_root: str,
        passphrase: str | None,
        staging_parent: Path | None,
        extra_excludes: tuple[str, ...] = (),
        extra_protected: tuple[str, ...] = (),
        config_sha256: str = "",
    ) -> None:
        super().__init__()
        self.root = root
        self.mapping_id = mapping_id
        self.site_type = site_type
        self.host = host
        self.port = port
        self.username = username
        self.private_key_path = private_key_path
        self.fingerprint = fingerprint
        self.remote_root = remote_root
        self.passphrase = passphrase
        self.staging_parent = staging_parent
        self.extra_excludes = extra_excludes
        self.extra_protected = extra_protected
        self.config_sha256 = config_sha256

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            client, sftp = connect_sftp(
                host=self.host,
                port=self.port,
                username=self.username,
                private_key_path=self.private_key_path,
                host_key_fingerprint=self.fingerprint,
                passphrase=self.passphrase,
            )
            result = DryRunService().execute(
                site_type=self.site_type,
                mapping_id=self.mapping_id,
                local_root=self.root,
                remote_root=self.remote_root,
                sftp=sftp,
                staging_parent=self.staging_parent,
                extra_excludes=self.extra_excludes,
                extra_protected=self.extra_protected,
                config_sha256=self.config_sha256,
            )
            self.completed.emit(result)
        except Exception as exc:  # worker boundary: surface the failure in the UI
            self.failed.emit(str(exc))
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()


class RevalidationWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        stored: StoredPlan,
        project: Project,
        mapping: SyncMapping,
        passphrase: str | None,
    ) -> None:
        super().__init__()
        self.stored = stored
        self.project = project
        self.mapping = mapping
        self.passphrase = passphrase

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            client, sftp = connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )
            result = PlanRevalidator().revalidate(
                stored=self.stored,
                project=self.project,
                mapping=self.mapping,
                sftp=sftp,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()


class MainWindow(QMainWindow):
    def __init__(self, store: ProjectStore | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Docker → XServer Sync")
        self.resize(1040, 700)
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        if store is None:
            state_override = os.environ.get("DXSYNC_STATE_DIR")
            state_directory = Path(
                state_override
                or QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
            )
            store = ProjectStore(state_directory / "state.db")
        self.store = store
        self.staging_parent = self.store.database_path.parent / "staging"
        self.store.expire_plans()
        self._cleanup_result = cleanup_stale_staging(self.store, self.staging_parent)
        self._projects: dict[str, Project] = {}
        self._current_project: Project | None = None
        self._current_plan_id: str | None = None

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("Docker → XServer Sync")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QLabel(
            "安全確認モード — XServerへの書き込み機能は無効です"
        )
        subtitle.setStyleSheet("color: #57606a;")
        layout.addWidget(subtitle)

        project_bar = QHBoxLayout()
        project_bar.addWidget(QLabel("プロジェクト"))
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._project_selected)
        project_bar.addWidget(self.project_combo, 1)
        new_project = QPushButton("新規…")
        new_project.clicked.connect(self._new_project)
        self.edit_project_button = QPushButton("編集…")
        self.edit_project_button.clicked.connect(self._edit_project)
        self.delete_project_button = QPushButton("削除")
        self.delete_project_button.clicked.connect(self._delete_project)
        project_bar.addWidget(new_project)
        project_bar.addWidget(self.edit_project_button)
        project_bar.addWidget(self.delete_project_button)
        layout.addLayout(project_bar)

        controls = QFrame()
        controls.setFrameShape(QFrame.StyledPanel)
        controls_layout = QHBoxLayout(controls)

        self.site_combo = QComboBox()
        for site_type, label in SITE_LABELS.items():
            self.site_combo.addItem(label, site_type.value)
        controls_layout.addWidget(QLabel("サイトの種類"))
        controls_layout.addWidget(self.site_combo)

        controls_layout.addWidget(QLabel("同期対象"))
        self.mapping_combo = QComboBox()
        self.mapping_combo.currentIndexChanged.connect(self._mapping_selected)
        controls_layout.addWidget(self.mapping_combo)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Dockerへbind mountしているローカルフォルダ")
        controls_layout.addWidget(self.path_edit, 1)

        browse = QPushButton("参照…")
        browse.clicked.connect(self._browse)
        controls_layout.addWidget(browse)

        self.scan_button = QPushButton("ローカルを検査")
        self.scan_button.clicked.connect(self._start_scan)
        self.scan_button.setDefault(True)
        controls_layout.addWidget(self.scan_button)
        layout.addWidget(controls)

        connection = QGroupBox("XServer接続（読み取り専用・書き込みなし）")
        connection_layout = QGridLayout(connection)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("SERVER_ID.xsrv.jp")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(10022)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("サーバーID")
        self.remote_root_edit = QLineEdit()
        self.remote_root_edit.setPlaceholderText("/home/SERVER_ID/example.com/public_html")
        self.fingerprint_edit = QLineEdit()
        self.fingerprint_edit.setPlaceholderText("SHA256:...")
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("秘密鍵ファイル")
        key_browse = QPushButton("鍵を選択…")
        key_browse.clicked.connect(self._browse_key)
        self.passphrase_edit = QLineEdit()
        self.passphrase_edit.setEchoMode(QLineEdit.Password)
        self.passphrase_edit.setPlaceholderText("保存しません")
        self.dry_run_button = QPushButton("読み取り専用ドライラン")
        self.dry_run_button.clicked.connect(self._start_dry_run)
        self.revalidate_button = QPushButton("計画を再検証")
        self.revalidate_button.clicked.connect(self._start_revalidation)
        self.revalidate_button.setEnabled(False)

        connection_layout.addWidget(QLabel("ホスト"), 0, 0)
        connection_layout.addWidget(self.host_edit, 0, 1)
        connection_layout.addWidget(QLabel("ポート"), 0, 2)
        connection_layout.addWidget(self.port_spin, 0, 3)
        connection_layout.addWidget(QLabel("ユーザー"), 0, 4)
        connection_layout.addWidget(self.user_edit, 0, 5)
        connection_layout.addWidget(QLabel("リモートルート"), 1, 0)
        connection_layout.addWidget(self.remote_root_edit, 1, 1, 1, 5)
        connection_layout.addWidget(QLabel("接続先の確認コード"), 2, 0)
        connection_layout.addWidget(self.fingerprint_edit, 2, 1, 1, 5)
        connection_layout.addWidget(QLabel("秘密鍵ファイル"), 3, 0)
        connection_layout.addWidget(self.key_edit, 3, 1, 1, 3)
        connection_layout.addWidget(key_browse, 3, 4)
        connection_layout.addWidget(self.passphrase_edit, 3, 5)
        connection_layout.addWidget(self.revalidate_button, 4, 3)
        connection_layout.addWidget(self.dry_run_button, 4, 4, 1, 2)
        layout.addWidget(connection)

        self.summary = QLabel("フォルダを選択して検査してください。")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.table_model = FileTableModel()
        table = QTableView()
        table.setModel(self.table_model)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 100)
        table.setColumnWidth(1, 430)
        table.setColumnWidth(2, 90)
        layout.addWidget(table, 1)

        footer = QLabel("ドライランはリモートへの一覧取得・読み取りだけを行い、ファイルを作成・変更しません。")
        footer.setStyleSheet("color: #57606a;")
        layout.addWidget(footer)

        self.setCentralWidget(root)
        self._reload_projects()

    def _reload_projects(self, select_id: str | None = None) -> None:
        if select_id is None and self._current_project is not None:
            select_id = self._current_project.project_id
        projects = self.store.list_projects()
        self._projects = {project.project_id: project for project in projects}
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("手動設定（未保存）", None)
        for project in projects:
            self.project_combo.addItem(project.name, project.project_id)
        index = self.project_combo.findData(select_id) if select_id else (1 if projects else 0)
        self.project_combo.setCurrentIndex(max(index, 0))
        self.project_combo.blockSignals(False)
        self._project_selected(self.project_combo.currentIndex())

    @Slot(int)
    def _project_selected(self, index: int) -> None:
        project_id = self.project_combo.itemData(index) if index >= 0 else None
        self._current_project = self._projects.get(project_id)
        has_project = self._current_project is not None
        self.edit_project_button.setEnabled(has_project)
        self.delete_project_button.setEnabled(has_project)
        self.mapping_combo.blockSignals(True)
        self.mapping_combo.clear()
        if self._current_project is not None:
            project = self._current_project
            site_index = self.site_combo.findData(project.site_type.value)
            self.site_combo.setCurrentIndex(max(site_index, 0))
            self.host_edit.setText(project.ssh_host)
            self.port_spin.setValue(project.ssh_port)
            self.user_edit.setText(project.ssh_username)
            self.fingerprint_edit.setText(project.host_key_fingerprint)
            self.key_edit.setText(project.private_key_reference)
            self.passphrase_edit.clear()
            for mapping in project.mappings:
                self.mapping_combo.addItem(mapping.name, mapping.mapping_id)
        self.mapping_combo.blockSignals(False)
        self._mapping_selected(self.mapping_combo.currentIndex())

    @Slot(int)
    def _mapping_selected(self, index: int) -> None:
        mapping = self._selected_mapping()
        if mapping is not None:
            self.path_edit.setText(mapping.local_root)
            self.remote_root_edit.setText(mapping.remote_root)
        self._refresh_ready_plan()

    def _refresh_ready_plan(self) -> None:
        self.store.expire_plans()
        cleanup_stale_staging(self.store, self.staging_parent)
        self._current_plan_id = None
        mapping = self._selected_mapping()
        if self._current_project is not None and mapping is not None:
            matching = [
                stored
                for stored in self.store.list_plans({"ready"})
                if stored.project_id == self._current_project.project_id
                and stored.plan.mapping_id == mapping.mapping_id
            ]
            if matching:
                self._current_plan_id = matching[-1].plan.plan_id
        self.revalidate_button.setEnabled(
            self._current_plan_id is not None and self._thread is None
        )

    def _selected_mapping(self) -> SyncMapping | None:
        if self._current_project is None:
            return None
        mapping_id = self.mapping_combo.currentData()
        return next(
            (mapping for mapping in self._current_project.mappings if mapping.mapping_id == mapping_id),
            None,
        )

    @Slot()
    def _new_project(self) -> None:
        dialog = ProjectDialog(self.store, parent=self)
        if dialog.exec() == QDialog.Accepted and dialog.saved_project is not None:
            self._reload_projects(dialog.saved_project.project_id)

    @Slot()
    def _edit_project(self) -> None:
        if self._current_project is None:
            return
        dialog = ProjectDialog(self.store, self._current_project, self)
        if dialog.exec() == QDialog.Accepted and dialog.saved_project is not None:
            self._reload_projects(dialog.saved_project.project_id)

    @Slot()
    def _delete_project(self) -> None:
        if self._current_project is None:
            return
        answer = QMessageBox.question(
            self,
            "プロジェクトを削除",
            f"「{self._current_project.name}」の保存設定を削除しますか？\n"
            "リモートファイルとローカルファイルは削除されません。",
        )
        if answer == QMessageBox.Yes:
            self.store.delete(self._current_project.project_id)
            self._current_project = None
            self._reload_projects()

    @Slot()
    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "同期元フォルダを選択")
        if selected:
            self.path_edit.setText(selected)

    @Slot()
    def _browse_key(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "SSH秘密鍵を選択")
        if selected:
            self.key_edit.setText(selected)

    @Slot()
    def _start_scan(self) -> None:
        if self._thread is not None:
            return
        root = Path(self.path_edit.text().strip()).expanduser()
        if not root.is_dir():
            QMessageBox.warning(self, "フォルダを確認", "読み取り可能なフォルダを選択してください。")
            return
        site_type = SiteType(self.site_combo.currentData())
        self.scan_button.setEnabled(False)
        self.summary.setText("検査中… ファイル内容からSHA-256を計算しています。")

        thread = QThread(self)
        worker = ScanWorker(root, site_type)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._scan_completed)
        worker.failed.connect(self._scan_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def _start_dry_run(self) -> None:
        if self._thread is not None:
            return
        root = Path(self.path_edit.text().strip()).expanduser()
        key_path = Path(self.key_edit.text().strip()).expanduser()
        required = {
            "ローカルフォルダ": root.is_dir(),
            "ホスト": bool(self.host_edit.text().strip()),
            "ユーザー": bool(self.user_edit.text().strip()),
            "リモートルート": bool(self.remote_root_edit.text().strip()),
            "SHA-256ホスト鍵": self.fingerprint_edit.text().strip().startswith("SHA256:"),
            "秘密鍵": key_path.is_file(),
        }
        missing = [label for label, valid in required.items() if not valid]
        if missing:
            QMessageBox.warning(self, "接続設定を確認", "確認が必要です: " + "、".join(missing))
            return

        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.summary.setText("読み取り専用ドライラン中… ローカルとXServerを比較しています。")
        thread = QThread(self)
        mapping = self._selected_mapping()
        worker = DryRunWorker(
            root=root,
            mapping_id=mapping.mapping_id if mapping else "gui-manual",
            site_type=SiteType(self.site_combo.currentData()),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            username=self.user_edit.text().strip(),
            private_key_path=str(key_path),
            fingerprint=self.fingerprint_edit.text().strip(),
            remote_root=self.remote_root_edit.text().strip(),
            passphrase=self.passphrase_edit.text() or None,
            staging_parent=self.staging_parent,
            extra_excludes=mapping.exclude_patterns if mapping else (),
            extra_protected=mapping.protect_patterns if mapping else (),
            config_sha256=(
                project_config_sha256(self._current_project, mapping)
                if self._current_project is not None and mapping is not None
                else ""
            ),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._dry_run_completed)
        worker.failed.connect(self._dry_run_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def _start_revalidation(self) -> None:
        if self._thread is not None or self._current_plan_id is None:
            return
        project = self._current_project
        mapping = self._selected_mapping()
        stored = self.store.get_plan(self._current_plan_id)
        if project is None or mapping is None or stored is None:
            self._refresh_ready_plan()
            return
        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.revalidate_button.setEnabled(False)
        self.summary.setText("計画を再検証中… ローカル、XServer、ステージングを確認しています。")
        thread = QThread(self)
        worker = RevalidationWorker(
            stored=stored,
            project=project,
            mapping=mapping,
            passphrase=self.passphrase_edit.text() or None,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._revalidation_completed)
        worker.failed.connect(self._revalidation_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _scan_completed(self, result: ScanResult) -> None:
        site_type = SiteType(self.site_combo.currentData())
        preset = get_preset(site_type)
        self.table_model.set_scan(result, preset.protect_patterns)
        protected_count = sum(
            1 for item in result.files if PathRules(preset.protect_patterns).matches(item.relative_path)
        )
        total_bytes = sum(item.size or 0 for item in result.files)
        issue_text = f" / 要確認 {len(result.issues)}件" if result.issues else ""
        self.summary.setText(
            f"{len(result.files):,}ファイル（{_format_size(total_bytes)}） / "
            f"保護 {protected_count:,}件 / 除外 {result.excluded_count:,}件{issue_text}"
        )

    @Slot(str)
    def _scan_failed(self, message: str) -> None:
        self.summary.setText("検査に失敗しました。")
        QMessageBox.critical(self, "検査エラー", message)

    @Slot(object)
    def _dry_run_completed(self, result: DryRunResult) -> None:
        self.table_model.set_plan(result)
        if self._current_project is not None:
            staging_path = str(result.staging.root) if result.staging else None
            self.store.save_plan(
                self._current_project.project_id,
                result.plan,
                staging_path,
            )
            cleanup_stale_staging(self.store, self.staging_parent)
            self._current_plan_id = result.plan.plan_id
            self.revalidate_button.setEnabled(True)
        counts = result.plan.counts()
        protected_count = len(
            set(result.local.skipped_protected_paths) | set(result.remote.skipped_protected_paths)
        )
        status = "実行不可" if result.has_blocking_issues else "確認可能"
        persisted = " / 計画保存済み（5分間有効）" if self._current_project else ""
        self.summary.setText(
            f"ドライラン完了（{status}） / ADD {counts[ChangeType.ADD]:,} / "
            f"UPDATE {counts[ChangeType.UPDATE]:,} / "
            f"REMOTE_ONLY {counts[ChangeType.REMOTE_ONLY]:,} / "
            f"保護領域 {protected_count:,}件{persisted}"
        )

    @Slot(str)
    def _dry_run_failed(self, message: str) -> None:
        self.summary.setText("ドライランに失敗しました。リモートは変更されていません。")
        QMessageBox.critical(self, "ドライランエラー", message)

    @Slot(object)
    def _revalidation_completed(self, result: RevalidationResult) -> None:
        if result.valid:
            self.summary.setText(
                "計画の再検証に成功しました。設定、ローカル、XServer、ステージングは一致しています。"
            )
            return
        if self._current_plan_id is not None:
            self.store.set_plan_status(self._current_plan_id, "invalid")
        cleanup_stale_staging(self.store, self.staging_parent)
        self._current_plan_id = None
        self.revalidate_button.setEnabled(False)
        details = "\n".join(f"• {reason}" for reason in result.reasons)
        self.summary.setText("計画は無効になりました。再度ドライランを実行してください。")
        QMessageBox.warning(self, "再検証に失敗", details)

    @Slot(str)
    def _revalidation_failed(self, message: str) -> None:
        self.summary.setText("再検証を完了できませんでした。計画は実行されていません。")
        QMessageBox.critical(self, "再検証エラー", message)

    @Slot()
    def _scan_finished(self) -> None:
        self.scan_button.setEnabled(True)
        self.dry_run_button.setEnabled(True)
        self._refresh_ready_plan()
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None


def main(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("Docker XServer Sync")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
