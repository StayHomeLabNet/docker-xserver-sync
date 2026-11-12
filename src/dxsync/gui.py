from __future__ import annotations

import html
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

try:
    from PySide6.QtCore import (
        QAbstractTableModel,
        QModelIndex,
        QObject,
        QSettings,
        QStandardPaths,
        Qt,
        QThread,
        QTimer,
        Signal,
        Slot,
    )
    from PySide6.QtGui import QColor, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QProgressBar,
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
from . import __version__
from .integrity import project_config_sha256
from .maintenance import cleanup_stale_staging
from .history_dialog import HistoryDialog
from .health import normalize_health_check_path, perform_https_health_check
from .approval_dialog import (
    BackupConfirmationDialog,
    DeploymentApprovalDialog,
    ForceOverwriteApprovalDialog,
    RestoreApprovalDialog,
)
from .auto_sync import (
    AutoSyncApprovalDialog,
    AutoSyncPreferences,
    AutoSyncSettingsDialog,
    auto_sync_policy_issues,
    load_auto_sync_preferences,
    save_auto_sync_preferences,
)
from .backup import BackupResult, RemoteBackupEngine
from .cancellation import CancellationToken, OperationCancelled
from .deployment import (
    DeploymentAuthorization,
    DeploymentResult,
    RemoteDeploymentEngine,
)
from .force_overwrite import (
    ForceOverwriteEngine,
    ForceOverwriteResult,
    collect_overwrite_candidates,
)
from .presets import get_preset
from .project_dialog import ProjectDialog
from .remote import connect_sftp
from .wordpress_setup import (
    WORDPRESS_SETUP_TOTAL_STEPS,
    WordPressSetupEngine,
    WordPressSetupProgress,
    WordPressSetupRequest,
)
from .wordpress_setup_dialog import WordPressSetupWizard
from .laravel_setup import (
    LARAVEL_SETUP_TOTAL_STEPS,
    LaravelSetupEngine,
    LaravelSetupProgress,
    LaravelSetupRequest,
)
from .laravel_setup_dialog import LaravelSetupWizard
from .xserver_api import XServerApiClient
from .recovery import (
    RecoveryAnalysisResult,
    analyze_recovery_artifacts,
    scan_recovery_artifacts,
)
from .recovery_dialog import RecoveryAnalysisDialog
from .revalidation import PlanRevalidator, RevalidationResult
from .restore import RestoreAuthorization, RestoreResult, RemoteRestoreEngine
from .rules import PathRules
from .scanner import LocalScanner
from .service import DryRunProgress, DryRunResult, DryRunService
from .storage import BackupRecord, ProjectStore, StoredPlan
from .secure_store import SecureStoreError
from .settings_dialog import MASK_FIELDS, MaskSettingsDialog
from .validation import custom_runtime_paths_issue


SITE_LABELS = {
    SiteType.CUSTOM_PHP: "独自 PHP",
    SiteType.WORDPRESS: "WordPress",
    SiteType.LARAVEL: "Laravel",
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


class BackupWorker(QObject):
    completed = Signal(object)
    blocked = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        stored: StoredPlan,
        project: Project,
        mapping: SyncMapping,
        passphrase: str | None,
        backup_root: str,
        delete_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.stored = stored
        self.project = project
        self.mapping = mapping
        self.passphrase = passphrase
        self.backup_root = backup_root
        self.delete_paths = delete_paths

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
            recovery = scan_recovery_artifacts(sftp, self.mapping.remote_root)
            if recovery.temporary_files or recovery.issues:
                raise RuntimeError(
                    "残存一時ファイルまたは未確認領域があります。先に安全性確認を実行してください。"
                )
            revalidation = PlanRevalidator().revalidate(
                stored=self.stored,
                project=self.project,
                mapping=self.mapping,
                sftp=sftp,
            )
            if not revalidation.valid:
                self.blocked.emit(revalidation)
                return
            result = RemoteBackupEngine().create_backup(
                sftp=sftp,
                plan=self.stored.plan,
                project_id=self.project.project_id,
                remote_root=self.mapping.remote_root,
                backup_root=self.backup_root,
                delete_paths=self.delete_paths,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()


class DeployWorker(QObject):
    completed = Signal(object)
    blocked = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    cancelled = Signal(str)

    def __init__(
        self,
        *,
        stored: StoredPlan,
        project: Project,
        mapping: SyncMapping,
        passphrase: str | None,
        authorization: DeploymentAuthorization,
        health_check_path: str | None = None,
        resume: bool = False,
    ) -> None:
        super().__init__()
        self.stored = stored
        self.project = project
        self.mapping = mapping
        self.passphrase = passphrase
        self.authorization = authorization
        self.health_check_path = health_check_path
        self.resume = resume
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            if not self.stored.staging_path:
                raise RuntimeError("同期計画にステージングファイルがありません")
            client, sftp = connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )
            recovery = scan_recovery_artifacts(sftp, self.mapping.remote_root)
            if recovery.temporary_files or recovery.issues:
                raise RuntimeError(
                    "残存一時ファイルまたは未確認領域があります。先に安全性確認を実行してください。"
                )
            revalidation = PlanRevalidator().revalidate(
                stored=self.stored,
                project=self.project,
                mapping=self.mapping,
                sftp=sftp,
                allow_resume=self.resume,
            )
            if not revalidation.valid:
                self.blocked.emit(revalidation)
                return
            result = RemoteDeploymentEngine().deploy(
                sftp=sftp,
                plan=self.stored.plan,
                staging_root=Path(self.stored.staging_path),
                remote_root=self.mapping.remote_root,
                authorization=self.authorization,
                file_mode=self.mapping.file_mode,
                directory_mode=self.mapping.directory_mode,
                progress_callback=self.progress.emit,
                cancellation_token=self.cancellation_token,
            )
            if self.health_check_path is not None:
                result = replace(
                    result,
                    health_check=perform_https_health_check(
                        domain=self.project.domain,
                        path=self.health_check_path,
                    ),
                )
            self.completed.emit(result)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            msg = str(exc).strip()
            if not msg or msg.lower() == "failure":
                msg = f"SFTP/SSHファイル転送エラー ({type(exc).__name__}: SFTP Failure - サーバーまたはファイルアクセスでエラーが発生しました)"
            self.failed.emit(msg)
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()


class ForceOverwriteWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    cancelled = Signal(str)

    def __init__(
        self,
        *,
        project: Project,
        mapping: SyncMapping,
        passphrase: str | None,
        selected_paths: tuple[str, ...],
    ) -> None:
        super().__init__()
        self.project = project
        self.mapping = mapping
        self.passphrase = passphrase
        self.selected_paths = selected_paths
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

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
            preset = get_preset(self.project.site_type)
            result = ForceOverwriteEngine().overwrite(
                sftp=sftp,
                local_root=Path(self.mapping.local_root),
                remote_root=self.mapping.remote_root,
                exclude_patterns=preset.exclude_patterns + self.mapping.exclude_patterns,
                protect_patterns=preset.protect_patterns + self.mapping.protect_patterns,
                include_paths=self.selected_paths,
                file_mode=self.mapping.file_mode,
                directory_mode=self.mapping.directory_mode,
                progress_callback=self.progress.emit,
                cancellation_token=self.cancellation_token,
            )
            self.completed.emit(result)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            msg = str(exc).strip()
            if not msg or msg.lower() == "failure":
                msg = f"SFTP/SSHファイル転送エラー ({type(exc).__name__}: SFTP Failure - サーバーまたはファイルアクセスでエラーが発生しました)"
            self.failed.emit(msg)
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()


class RecoveryScanWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project, mapping: SyncMapping, passphrase: str | None) -> None:
        super().__init__()
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
            self.completed.emit(analyze_recovery_artifacts(sftp, self.mapping.remote_root))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()


class RestoreWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        project: Project,
        mapping: SyncMapping,
        manifest_path: str,
        expected_current_hashes: dict[str, str],
        authorization: RestoreAuthorization,
        passphrase: str | None,
    ) -> None:
        super().__init__()
        self.project = project
        self.mapping = mapping
        self.manifest_path = manifest_path
        self.expected_current_hashes = expected_current_hashes
        self.authorization = authorization
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
            recovery = scan_recovery_artifacts(sftp, self.mapping.remote_root)
            if recovery.temporary_files or recovery.issues:
                raise RuntimeError(
                    "残存一時ファイルまたは未確認領域があります。先に安全性確認を実行してください。"
                )
            result = RemoteRestoreEngine().restore(
                sftp=sftp,
                manifest_path=self.manifest_path,
                destination_root=self.mapping.remote_root,
                expected_current_hashes=self.expected_current_hashes,
                authorization=self.authorization,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if sftp is not None:
                sftp.close()
            if client is not None:
                client.close()


class WordPressSetupWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    checkpoint = Signal(object)
    cancelled = Signal(str)

    def __init__(
        self,
        *,
        project: Project,
        request: WordPressSetupRequest,
        api_key: str | None,
        servername: str | None,
        passphrase: str | None,
    ) -> None:
        super().__init__()
        self.project = project
        self.request = request
        self.api_key = api_key
        self.servername = servername
        self.passphrase = passphrase
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            self.progress.emit(
                WordPressSetupProgress(
                    step=1,
                    total_steps=WORDPRESS_SETUP_TOTAL_STEPS,
                    message="XServerへSSH接続中",
                )
            )
            client, sftp = connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )
            result = WordPressSetupEngine().setup(
                request=self.request,
                api=(
                    XServerApiClient(
                        api_key=self.api_key,
                        servername=self.servername or "",
                    )
                    if self.api_key
                    else None
                ),
                ssh_client=client,
                sftp=sftp,
                ssh_username=self.project.ssh_username,
                progress_callback=self.progress.emit,
                checkpoint_callback=self.checkpoint.emit,
                cancellation_token=self.cancellation_token,
            )
            self.progress.emit(
                WordPressSetupProgress(
                    step=10,
                    total_steps=WORDPRESS_SETUP_TOTAL_STEPS,
                    message="HTTPSでトップページを確認中",
                )
            )
            public_url = urlparse(result.public_url)
            health_domain = public_url.netloc
            base_path = public_url.path.rstrip("/")
            homepage_path = f"{base_path}/" or "/"
            login_path = f"{base_path}/wp-login.php"
            homepage = perform_https_health_check(
                domain=health_domain, path=homepage_path
            )
            self.progress.emit(
                WordPressSetupProgress(
                    step=10,
                    total_steps=WORDPRESS_SETUP_TOTAL_STEPS,
                    message="HTTPSでログイン画面を確認中",
                )
            )
            login = perform_https_health_check(
                domain=health_domain, path=login_path
            )
            self.completed.emit((result, homepage, login))
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            msg = str(exc).strip()
            if not msg or msg.lower() == "failure":
                msg = f"SFTP/SSHファイル転送エラー ({type(exc).__name__}: SFTP Failure - サーバーまたはファイルアクセスでエラーが発生しました)"
            self.failed.emit(msg)
        finally:
            self.api_key = ""
            self.request = None
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


class LaravelSetupWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    checkpoint = Signal(object)
    cancelled = Signal(str)

    def __init__(
        self,
        *,
        project: Project,
        request: LaravelSetupRequest,
        api_key: str | None,
        servername: str | None,
        passphrase: str | None,
    ) -> None:
        super().__init__()
        self.project = project
        self.request = request
        self.api_key = api_key
        self.servername = servername
        self.passphrase = passphrase
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            self.progress.emit(
                LaravelSetupProgress(
                    step=1,
                    total_steps=LARAVEL_SETUP_TOTAL_STEPS,
                    message="XServerへSSH接続中",
                )
            )
            client, sftp = connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )
            result = LaravelSetupEngine().setup(
                request=self.request,
                api=(
                    XServerApiClient(
                        api_key=self.api_key,
                        servername=self.servername or "",
                    )
                    if self.api_key
                    else None
                ),
                ssh_client=client,
                sftp=sftp,
                ssh_username=self.project.ssh_username,
                progress_callback=self.progress.emit,
                checkpoint_callback=self.checkpoint.emit,
                cancellation_token=self.cancellation_token,
            )
            self.progress.emit(
                LaravelSetupProgress(
                    step=10,
                    total_steps=LARAVEL_SETUP_TOTAL_STEPS,
                    message="HTTPSでサイトトップを確認中",
                )
            )
            public_url = urlparse(result.public_url)
            health_domain = public_url.netloc
            base_path = public_url.path.rstrip("/")
            homepage_path = f"{base_path}/" or "/"
            homepage = perform_https_health_check(
                domain=health_domain, path=homepage_path
            )
            self.completed.emit((result, homepage))
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            msg = str(exc).strip()
            if not msg or msg.lower() == "failure":
                msg = f"SFTP/SSHファイル転送エラー ({type(exc).__name__}: SFTP Failure - サーバーまたはファイルアクセスでエラーが発生しました)"
            self.failed.emit(msg)
        finally:
            self.api_key = ""
            self.request = None
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


class MainWindow(QMainWindow):
    def __init__(
        self,
        store: ProjectStore | None = None,
        settings: QSettings | None = None,
    ) -> None:
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
        self.settings = settings or QSettings()
        self.staging_parent = self.store.database_path.parent / "staging"
        self._interrupted_operations = self.store.recover_interrupted_operations()
        self.store.expire_plans()
        self._cleanup_result = cleanup_stale_staging(self.store, self.staging_parent)
        self._projects: dict[str, Project] = {}
        self._current_project: Project | None = None
        self._current_plan_id: str | None = None
        self._validated_plan_id: str | None = None
        self._current_backup_id: str | None = None
        self._backup_skipped = False
        self._approved_delete_paths: tuple[str, ...] = ()
        self._restore_backup_id: str | None = None
        self._current_operation_id: str | None = None
        self._current_operation_details: dict = {}
        self._close_when_idle = False
        self._auto_sync_active = False
        self._wordpress_setup_active = False
        self._wordpress_progress_event: WordPressSetupProgress | None = None
        self._wordpress_progress_started_at: float | None = None
        self._wordpress_progress_phase_started_at: float | None = None
        self._wordpress_progress_last_update_at: float | None = None
        self._wordpress_progress_phase_key: tuple[object, ...] | None = None
        self._wordpress_progress_timer = QTimer(self)
        self._wordpress_progress_timer.setInterval(1000)
        self._wordpress_progress_timer.timeout.connect(
            self._refresh_wordpress_progress
        )
        self._laravel_setup_active = False
        self._laravel_progress_event: LaravelSetupProgress | None = None
        self._laravel_progress_started_at: float | None = None
        self._laravel_progress_phase_started_at: float | None = None
        self._laravel_progress_last_update_at: float | None = None
        self._laravel_progress_phase_key: tuple[object, ...] | None = None
        self._laravel_progress_timer = QTimer(self)
        self._laravel_progress_timer.setInterval(1000)
        self._laravel_progress_timer.timeout.connect(
            self._refresh_laravel_progress
        )
        self._dry_run_progress_event: DryRunProgress | None = None
        self._dry_run_progress_started_at: float | None = None
        self._dry_run_progress_phase_started_at: float | None = None
        self._dry_run_progress_last_update_at: float | None = None
        self._dry_run_progress_phase_key: tuple[object, ...] | None = None
        self._dry_run_progress_timer = QTimer(self)
        self._dry_run_progress_timer.setInterval(1000)
        self._dry_run_progress_timer.timeout.connect(
            self._refresh_dry_run_progress
        )
        self._auto_sync_stage: str | None = None
        self._auto_sync_preferences: AutoSyncPreferences | None = None
        self._auto_backup_skipped = False
        self._auto_health_check_path: str | None = None
        self._auto_dry_run_result: DryRunResult | None = None

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 4, 24, 20)
        layout.setSpacing(14)

        title = QLabel("Docker → XServer Sync")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QLabel(
            "安全同期モード — 通常は再検証・バックアップ・明示承認を行います"
        )
        subtitle.setStyleSheet("color: #57606a;")
        layout.addWidget(subtitle)

        project_bar = QHBoxLayout()
        project_bar.addWidget(QLabel("プロジェクト"))
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._project_selected)
        project_bar.addWidget(self.project_combo, 1)
        self.new_project_button = QPushButton("新規…")
        self.new_project_button.clicked.connect(self._new_project)
        self.edit_project_button = QPushButton("編集…")
        self.edit_project_button.clicked.connect(self._edit_project)
        self.delete_project_button = QPushButton("削除")
        self.delete_project_button.clicked.connect(self._delete_project)
        self.history_button = QPushButton("履歴…")
        self.history_button.clicked.connect(self._show_history)
        self.recovery_button = QPushButton("安全性確認")
        self.recovery_button.clicked.connect(self._start_recovery_scan)
        self.recovery_button.setEnabled(False)
        project_bar.addWidget(self.new_project_button)
        project_bar.addWidget(self.edit_project_button)
        project_bar.addWidget(self.delete_project_button)
        project_bar.addWidget(self.recovery_button)
        project_bar.addWidget(self.history_button)
        layout.addLayout(project_bar)

        self.setup_panel = QFrame()
        self.setup_panel.setFrameShape(QFrame.StyledPanel)
        setup_layout = QHBoxLayout(self.setup_panel)
        self.setup_text = QLabel()
        self.setup_text.setWordWrap(True)
        setup_layout.addWidget(self.setup_text, 1)
        self.wordpress_setup_button = QPushButton("セットアップを開始…")
        self.wordpress_setup_button.setToolTip(
            "WordPressプロジェクトを選び、空のXServerへ初回配置します"
        )
        self.wordpress_setup_button.clicked.connect(self._start_wordpress_setup)
        setup_layout.addWidget(self.wordpress_setup_button)
        self.wordpress_resume_button = QPushButton("中断したセットアップを再開…")
        self.wordpress_resume_button.setToolTip(
            "配置済みファイルとDBを検査し、URL置換以降を再開します"
        )
        self.wordpress_resume_button.clicked.connect(self._resume_wordpress_setup)
        setup_layout.addWidget(self.wordpress_resume_button)
        self.laravel_setup_button = QPushButton("セットアップを開始…")
        self.laravel_setup_button.setToolTip(
            "Laravelプロジェクトを選び、空のXServerへ初回配置します"
        )
        self.laravel_setup_button.clicked.connect(self._start_laravel_setup)
        setup_layout.addWidget(self.laravel_setup_button)
        self.setup_panel.setVisible(False)
        layout.addWidget(self.setup_panel)

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

        self.browse_button = QPushButton("参照…")
        self.browse_button.clicked.connect(self._browse)
        controls_layout.addWidget(self.browse_button)

        self.scan_button = QPushButton("ローカルを検査")
        self.scan_button.clicked.connect(self._start_scan)
        self.scan_button.setDefault(True)
        controls_layout.addWidget(self.scan_button)
        layout.addWidget(controls)

        connection = QGroupBox("XServer接続と安全同期")
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
        self.key_browse_button = QPushButton("鍵を選択…")
        self.key_browse_button.clicked.connect(self._browse_key)
        self.passphrase_edit = QLineEdit()
        self.passphrase_edit.setEchoMode(QLineEdit.Password)
        self.passphrase_edit.setPlaceholderText("保存しません")
        self.dry_run_button = QPushButton("読み取り専用ドライラン")
        self.dry_run_button.clicked.connect(self._start_dry_run)
        self.auto_sync_button = QPushButton("安全同期を自動実行")
        self.auto_sync_button.clicked.connect(self._start_auto_sync)
        self.auto_sync_button.setEnabled(False)
        self.revalidate_button = QPushButton("計画を再検証")
        self.revalidate_button.clicked.connect(self._start_revalidation)
        self.revalidate_button.setEnabled(False)
        self.backup_button = QPushButton("バックアップを作成")
        self.backup_button.clicked.connect(self._start_backup)
        self.backup_button.setEnabled(False)
        self.deploy_button = QPushButton("承認付き同期")
        self.deploy_button.clicked.connect(self._start_deployment)
        self.deploy_button.setEnabled(False)
        self.force_overwrite_button = QPushButton("無条件上書き")
        self.force_overwrite_button.clicked.connect(self._start_force_overwrite)
        self.force_overwrite_button.setEnabled(False)
        self.mask_toggle = QCheckBox("接続情報のマスクを一時解除")
        self.mask_toggle.setToolTip("この画面を閉じるまでの表示だけを切り替えます")
        self.mask_toggle.toggled.connect(self._apply_connection_masks)

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
        connection_layout.addWidget(self.key_browse_button, 3, 4)
        connection_layout.addWidget(self.passphrase_edit, 3, 5)
        connection_layout.addWidget(self.force_overwrite_button, 4, 1)
        connection_layout.addWidget(self.revalidate_button, 4, 2)
        connection_layout.addWidget(self.backup_button, 4, 3)
        connection_layout.addWidget(self.deploy_button, 4, 4)
        connection_layout.addWidget(self.dry_run_button, 4, 5)
        connection_layout.addWidget(self.mask_toggle, 5, 1, 1, 4)
        connection_layout.addWidget(self.auto_sync_button, 5, 5)
        layout.addWidget(connection)

        self.summary = QLabel("フォルダを選択して検査してください。")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar, 1)
        self.stop_button = QPushButton("処理を停止")
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self._request_operation_cancel)
        progress_layout.addWidget(self.stop_button)
        self.resume_button = QPushButton("中断した処理を再開…")
        self.resume_button.clicked.connect(self._resume_operation)
        self.resume_button.setVisible(False)
        progress_layout.addWidget(self.resume_button)
        layout.addLayout(progress_layout)

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
        self._create_menus()
        self._apply_connection_masks()
        self._reload_projects()
        if self._interrupted_operations:
            self.summary.setText(
                f"前回終了時に未完了だった操作を{len(self._interrupted_operations)}件検出しました。"
                "対象プロジェクトで「安全性確認」を実行してください。"
            )

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
        self._reset_write_state()
        self.mask_toggle.setChecked(False)
        project_id = self.project_combo.itemData(index) if index >= 0 else None
        self._current_project = self._projects.get(project_id)
        has_project = self._current_project is not None
        self.edit_project_button.setEnabled(has_project)
        self.delete_project_button.setEnabled(has_project)
        self.recovery_button.setEnabled(has_project and self._thread is None)
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
        self._apply_connection_masks()
        self._update_setup_panel()

    @Slot(int)
    def _mapping_selected(self, index: int) -> None:
        self._reset_write_state()
        mapping = self._selected_mapping()
        if mapping is not None:
            self.path_edit.setText(mapping.local_root)
            self.remote_root_edit.setText(mapping.remote_root)
        self._refresh_ready_plan()
        self._apply_connection_masks()

    def _mask_preferences(self) -> dict[str, bool]:
        return {
            key: self.settings.value(f"display_mask/{key}", True, type=bool)
            for key, _label in MASK_FIELDS
        }

    @Slot()
    def _apply_connection_masks(self) -> None:
        preferences = self._mask_preferences()
        reveal = self.mask_toggle.isChecked()
        fields = {
            "host": self.host_edit,
            "username": self.user_edit,
            "remote_root": self.remote_root_edit,
            "fingerprint": self.fingerprint_edit,
            "private_key": self.key_edit,
        }
        for key, editor in fields.items():
            masked = preferences.get(key, True) and not reveal
            editor.setEchoMode(QLineEdit.Password if masked else QLineEdit.Normal)
        self.passphrase_edit.setEchoMode(
            QLineEdit.Normal if reveal else QLineEdit.Password
        )

    def _create_menus(self) -> None:
        project_menu = self.menuBar().addMenu("プロジェクト")
        new_action = project_menu.addAction("新しいプロジェクト…")
        new_action.triggered.connect(self._new_project)
        self.wordpress_setup_action = project_menu.addAction(
            "WordPressをXServerへ新規セットアップ…"
        )
        self.wordpress_setup_action.triggered.connect(self._start_wordpress_setup)
        self.laravel_setup_action = project_menu.addAction(
            "LaravelをXServerへ新規セットアップ…"
        )
        self.laravel_setup_action.triggered.connect(
            self._start_laravel_setup
        )

        settings_menu = self.menuBar().addMenu("設定")
        mask_action = settings_menu.addAction("接続情報のマスク設定…")
        mask_action.triggered.connect(self._show_mask_settings)
        auto_action = settings_menu.addAction("自動実行設定…")
        auto_action.triggered.connect(self._show_auto_sync_settings)
        security_action = settings_menu.addAction("確認コードの保存状態…")
        security_action.triggered.connect(self._show_security_status)
        register_action = settings_menu.addAction("接続先の確認コードを再登録…")
        register_action.triggered.connect(self._reregister_host_key)
        settings_menu.addSeparator()
        reset_action = settings_menu.addAction("表示設定を初期状態に戻す")
        reset_action.triggered.connect(self._reset_display_settings)

        help_menu = self.menuBar().addMenu("ヘルプ")
        about_action = help_menu.addAction("バージョン情報")
        about_action.triggered.connect(self._show_about)

    @Slot()
    def _show_mask_settings(self) -> None:
        dialog = MaskSettingsDialog(self._mask_preferences(), self)
        if dialog.exec() != QDialog.Accepted:
            return
        for key, value in dialog.selected_values().items():
            self.settings.setValue(f"display_mask/{key}", value)
        self.settings.sync()
        self.mask_toggle.setChecked(False)
        self._apply_connection_masks()

    @Slot()
    def _show_auto_sync_settings(self) -> None:
        if self._auto_sync_active or self._thread is not None:
            QMessageBox.information(
                self,
                "自動実行設定",
                "処理の完了後に設定を変更してください。",
            )
            return
        project = self._current_project
        if project is None:
            QMessageBox.information(
                self,
                "自動実行設定",
                "保存済みプロジェクトを選択してください。",
            )
            return
        preferences = load_auto_sync_preferences(
            self.settings, project.project_id
        )
        dialog = AutoSyncSettingsDialog(preferences, self)
        if dialog.exec() != QDialog.Accepted:
            return
        save_auto_sync_preferences(
            self.settings,
            project.project_id,
            dialog.selected_preferences(),
        )
        QMessageBox.information(
            self,
            "自動実行設定",
            f"「{project.name}」の自動実行設定を保存しました。",
        )

    @Slot()
    def _reset_display_settings(self) -> None:
        for key, _label in MASK_FIELDS:
            self.settings.remove(f"display_mask/{key}")
        self.settings.sync()
        self.mask_toggle.setChecked(False)
        self._apply_connection_masks()

    @Slot()
    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "Docker → XServer Sync バージョン情報",
            f"Docker → XServer Sync\nバージョン {__version__}\n\n"
            "接続先の確認コードはOS資格情報ストアに保存します。",
        )

    @Slot()
    def _show_security_status(self) -> None:
        if not self._projects:
            QMessageBox.information(self, "確認コードの保存状態", "保存済みプロジェクトはありません。")
            return
        state_labels = {
            "secure": "保護済み",
            "legacy": "移行未完了",
            "unavailable": "読み取り不可",
            "missing": "未登録",
        }
        lines = []
        for project in self._projects.values():
            status = self.store.security_status(project.project_id)
            lines.append(f"{project.name}: {state_labels.get(status.state, status.state)}")
        backend = self.store.secure_store.backend_name
        QMessageBox.information(
            self,
            "確認コードの保存状態",
            "\n".join(lines) + f"\n\n資格情報ストア: {backend}",
        )

    @Slot()
    def _reregister_host_key(self) -> None:
        project = self._current_project
        if project is None:
            QMessageBox.information(self, "再登録", "保存済みプロジェクトを選択してください。")
            return
        fingerprint, accepted = QInputDialog.getText(
            self,
            "接続先の確認コードを再登録",
            "XServerサーバーパネルで確認した SHA256: から始まる値:",
            QLineEdit.Password,
        )
        fingerprint = fingerprint.strip()
        if not accepted:
            return
        if not fingerprint.startswith("SHA256:"):
            QMessageBox.warning(self, "入力を確認", "SHA256: から始まる確認コードを入力してください。")
            return
        try:
            self.store.update_host_key(project.project_id, fingerprint)
        except SecureStoreError as exc:
            QMessageBox.critical(self, "再登録できません", str(exc))
            return
        self._reload_projects(project.project_id)
        QMessageBox.information(self, "再登録完了", "OS資格情報ストアへ保存しました。")

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
        if self._validated_plan_id != self._current_plan_id:
            self._validated_plan_id = None
            self._current_backup_id = None
            self._backup_skipped = False
        self.revalidate_button.setEnabled(
            self._current_plan_id is not None and self._thread is None
        )
        self._update_action_buttons()

    def _reset_write_state(self) -> None:
        self._validated_plan_id = None
        self._current_backup_id = None
        self._approved_delete_paths = ()
        self._backup_skipped = False
        if hasattr(self, "backup_button"):
            self.backup_button.setEnabled(False)
        if hasattr(self, "deploy_button"):
            self.deploy_button.setEnabled(False)

    def _update_action_buttons(self) -> None:
        idle = self._thread is None and not self._auto_sync_active
        validated = (
            self._current_plan_id is not None
            and self._validated_plan_id == self._current_plan_id
        )
        self.backup_button.setEnabled(idle and validated and self._current_backup_id is None)
        self.deploy_button.setEnabled(
            idle and validated and (self._current_backup_id is not None or self._backup_skipped)
        )
        self.force_overwrite_button.setEnabled(
            idle and self._current_project is not None and self._selected_mapping() is not None
        )
        self.recovery_button.setEnabled(idle and self._current_project is not None)
        self.auto_sync_button.setEnabled(
            self._thread is None
            and not self._auto_sync_active
            and self._current_project is not None
            and self._selected_mapping() is not None
        )
        self._update_setup_panel()
        resumable = self._latest_resumable_operation()
        self.resume_button.setVisible(idle and resumable is not None)
        self.resume_button.setEnabled(idle and resumable is not None)

    def _update_setup_panel(self) -> None:
        project = self._current_project
        site_type = project.site_type if project is not None else None
        idle = self._thread is None and not self._auto_sync_active
        is_wordpress = site_type is SiteType.WORDPRESS
        is_laravel = site_type is SiteType.LARAVEL

        self.setup_panel.setVisible(is_wordpress or is_laravel)
        self.wordpress_setup_button.setVisible(is_wordpress)
        self.wordpress_setup_button.setEnabled(idle and is_wordpress)
        self.wordpress_resume_button.setVisible(is_wordpress)
        self.wordpress_resume_button.setEnabled(idle and is_wordpress)
        self.laravel_setup_button.setVisible(is_laravel)
        self.laravel_setup_button.setEnabled(idle and is_laravel)
        if is_wordpress:
            self.setup_text.setText(
                "WordPress初回セットアップ — "
                "ローカルDockerのWordPressを空のXServerへ配置します"
            )
        elif is_laravel:
            self.setup_text.setText(
                "Laravel初回セットアップ — "
                "ローカルDockerのLaravelを空のXServerへ配置します"
            )
        else:
            self.setup_text.clear()

        if hasattr(self, "wordpress_setup_action"):
            self.wordpress_setup_action.setVisible(is_wordpress)
            self.wordpress_setup_action.setEnabled(idle and is_wordpress)
        if hasattr(self, "laravel_setup_action"):
            self.laravel_setup_action.setVisible(is_laravel)
            self.laravel_setup_action.setEnabled(idle and is_laravel)

    def _backup_root(self) -> str:
        if self._current_project is None:
            raise RuntimeError("プロジェクトが選択されていません")
        return f"/home/{self._current_project.ssh_username}/.dxsync/backups"

    def _selected_mapping(self) -> SyncMapping | None:
        if self._current_project is None:
            return None
        mapping_id = self.mapping_combo.currentData()
        if mapping_id is None and self._current_project.mappings:
            return self._current_project.mappings[0]
        return next(
            (mapping for mapping in self._current_project.mappings if mapping.mapping_id == mapping_id),
            None,
        )

    def _require_runtime_paths_confirmation(self) -> bool:
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None:
            return True
        security = self.store.security_status(project.project_id)
        if security.state != "secure":
            QMessageBox.warning(
                self,
                "接続先の確認コードを利用できません",
                "接続先の確認コードがOS資格情報ストアで保護されていないため、"
                "この操作を実行できません。\n\n"
                "「設定」→「接続先の確認コードを再登録…」から再登録してください。\n\n"
                f"状態: {security.message}",
            )
            return False
        issue = custom_runtime_paths_issue(project, mapping)
        if issue is None:
            return True
        QMessageBox.warning(
            self,
            "本番書き込み先が未確認です",
            issue.message
            + "\n\n「プロジェクトを編集」→「本番書き込み先を設定」から確認するまで、"
            "ドライラン計画と書き込み操作は実行できません。",
        )
        return False

    @Slot()
    def _new_project(self) -> None:
        dialog = ProjectDialog(self.store, parent=self)
        if dialog.exec() == QDialog.Accepted and dialog.saved_project is not None:
            self._reload_projects(dialog.saved_project.project_id)

    @Slot()
    def _show_laravel_setup_guide(self) -> None:
        project = self._current_project
        if project is None or project.site_type is not SiteType.LARAVEL:
            return
        QMessageBox.information(
            self,
            "Laravel初回セットアップ",
            "Laravel専用の自動セットアップは現在準備中です。\n\n"
            "現時点では、プロジェクト設定でアプリ本体とpublicを別々に"
            "マッピングし、読み取り専用ドライランから安全同期を実行してください。\n\n"
            "本番の.env作成、APP_KEY、DB設定、storageへの書き込み権限、"
            "public/storageのリンク、artisan migrateやキャッシュ生成は"
            "自動実行しません。XServer上で内容を確認して個別に実行してください。",
        )

    @Slot()
    def _start_laravel_setup(self) -> None:
        if self._thread is not None or not self._require_runtime_paths_confirmation():
            return
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None or project.site_type is not SiteType.LARAVEL:
            QMessageBox.information(
                self,
                "Laravel新規セットアップ",
                "Laravelの保存済みプロジェクトを選択してください。",
            )
            return
        wizard = LaravelSetupWizard(project, mapping, self)
        if wizard.exec() != QDialog.Accepted:
            return
        request = wizard.request_value()
        api_key = wizard.api_key or None
        servername = wizard.servername if wizard.uses_xserver_api else None

        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.laravel_setup_button.setEnabled(False)
        self._update_action_buttons()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)

        now = time.monotonic()
        self._laravel_progress_started_at = now
        self._laravel_progress_phase_started_at = now
        self._laravel_progress_last_update_at = now
        self._laravel_progress_phase_key = None
        self._laravel_progress_event = LaravelSetupProgress(
            step=1,
            total_steps=LARAVEL_SETUP_TOTAL_STEPS,
            message="XServerへSSH接続中",
        )
        self._laravel_progress_timer.start()
        self._refresh_laravel_progress()
        self._begin_operation(
            "laravel_setup",
            details={
                "database_mode": request.database_mode,
                "database": request.existing_db_name,
                "database_user": request.existing_db_user,
                "db_host": request.db_host,
                "app_url": request.app_url,
                "php_cli_version": request.php_cli_version,
                "remote_app_root": request.remote_app_root,
                "remote_public_root": request.remote_public_root,
                "migration_mode": request.migration_mode,
            },
        )
        thread = QThread(self)
        worker = LaravelSetupWorker(
            project=project,
            request=request,
            api_key=api_key,
            servername=servername,
            passphrase=self.passphrase_edit.text() or None,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._laravel_setup_progress)
        worker.checkpoint.connect(self._update_operation_checkpoint)
        worker.completed.connect(self._laravel_setup_completed)
        worker.failed.connect(self._laravel_setup_failed)
        worker.cancelled.connect(self._laravel_setup_cancelled)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        self._laravel_setup_active = True
        self._set_auto_sync_controls_locked(True)
        self.stop_button.setVisible(True)
        self.stop_button.setEnabled(True)
        thread.start()

    @Slot()
    def _start_wordpress_setup(self) -> None:
        self._open_wordpress_setup(False)

    @Slot()
    def _resume_wordpress_setup(self) -> None:
        self._open_wordpress_setup(True)

    def _open_wordpress_setup(self, resume_existing: bool) -> None:
        if self._thread is not None or not self._require_runtime_paths_confirmation():
            return
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None or project.site_type is not SiteType.WORDPRESS:
            QMessageBox.information(
                self,
                "WordPress新規セットアップ",
                "WordPressの保存済みプロジェクトを選択してください。",
            )
            return
        wizard = WordPressSetupWizard(
            project, mapping, self, resume_existing=resume_existing
        )
        if wizard.exec() != QDialog.Accepted:
            return
        request = wizard.request_value()
        api_key = wizard.api_key or None
        servername = wizard.servername if wizard.uses_xserver_api else None
        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.wordpress_setup_button.setEnabled(False)
        self._update_action_buttons()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        now = time.monotonic()
        self._wordpress_progress_started_at = now
        self._wordpress_progress_phase_started_at = now
        self._wordpress_progress_last_update_at = now
        self._wordpress_progress_phase_key = None
        self._wordpress_progress_event = WordPressSetupProgress(
            step=1,
            total_steps=WORDPRESS_SETUP_TOTAL_STEPS,
            message="XServerへSSH接続中",
        )
        self._wordpress_progress_timer.start()
        self._refresh_wordpress_progress()
        self._begin_operation(
            "wordpress_setup",
            details={
                "resumable": True,
                "resume_existing": request.resume_existing,
                "use_remote_wp_config": request.use_remote_wp_config,
                "import_sql_if_database_empty": (
                    request.import_sql_if_database_empty
                ),
                "database_mode": request.database_mode,
                "database": request.existing_db_name,
                "database_user": request.existing_db_user,
                "db_host": request.db_host,
                "old_url": request.old_url,
                "new_url": request.new_url,
                "php_cli_version": request.php_cli_version,
                "remote_root": request.remote_root,
            },
        )
        thread = QThread(self)
        worker = WordPressSetupWorker(
            project=project,
            request=request,
            api_key=api_key,
            servername=servername,
            passphrase=self.passphrase_edit.text() or None,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._wordpress_setup_progress)
        worker.checkpoint.connect(self._update_operation_checkpoint)
        worker.completed.connect(self._wordpress_setup_completed)
        worker.failed.connect(self._wordpress_setup_failed)
        worker.cancelled.connect(self._wordpress_setup_cancelled)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        self._wordpress_setup_active = True
        self._set_auto_sync_controls_locked(True)
        self.stop_button.setVisible(True)
        self.stop_button.setEnabled(True)
        thread.start()

    @Slot(object)
    def _wordpress_setup_progress(self, event: object) -> None:
        if isinstance(event, str):
            event = WordPressSetupProgress(
                step=1,
                total_steps=WORDPRESS_SETUP_TOTAL_STEPS,
                message=event,
            )
        if not isinstance(event, WordPressSetupProgress):
            return
        now = time.monotonic()
        phase_key = (event.step, event.message, event.unit, event.total)
        if phase_key != self._wordpress_progress_phase_key:
            self._wordpress_progress_phase_started_at = now
            self._wordpress_progress_phase_key = phase_key
        self._wordpress_progress_event = event
        self._wordpress_progress_last_update_at = now
        self._refresh_wordpress_progress()

    @staticmethod
    def _format_progress_duration(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}秒"
        minutes, remaining_seconds = divmod(seconds, 60)
        if minutes < 60:
            return (
                f"{minutes}分{remaining_seconds}秒"
                if remaining_seconds
                else f"{minutes}分"
            )
        hours, remaining_minutes = divmod(minutes, 60)
        return (
            f"{hours}時間{remaining_minutes}分"
            if remaining_minutes
            else f"{hours}時間"
        )

    @staticmethod
    def _format_progress_amount(event: WordPressSetupProgress) -> str:
        assert event.current is not None and event.total is not None
        if event.unit == "bytes":
            mib = 1024 * 1024
            return f"{event.current / mib:.1f}/{event.total / mib:.1f} MiB"
        return f"{event.current:,}/{event.total:,}"

    @Slot()
    def _refresh_wordpress_progress(self) -> None:
        event = self._wordpress_progress_event
        if event is None:
            return
        now = time.monotonic()
        overall_elapsed = now - (self._wordpress_progress_started_at or now)
        stage = f"手順 {event.step}/{event.total_steps}"
        elapsed_text = f"経過 {self._format_progress_duration(overall_elapsed)}"
        details = [stage, event.message]

        determinate = (
            event.current is not None
            and event.total is not None
            and event.total > 0
        )
        if determinate:
            assert event.current is not None and event.total is not None
            current = min(max(event.current, 0), event.total)
            ratio = current / event.total
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(round(ratio * 1000))
            amount = self._format_progress_amount(
                replace(event, current=current)
            )
            percent = f"{ratio * 100:.1f}%"
            details.extend((amount, percent))
            eta_text = ""
            phase_elapsed = now - (self._wordpress_progress_phase_started_at or now)
            if 0 < current < event.total and phase_elapsed >= 1:
                eta_seconds = phase_elapsed * (event.total - current) / current
                eta_text = (
                    f"残り約 {self._format_progress_duration(eta_seconds)}"
                )
            elif current == 0:
                eta_text = "残り時間を計算中"
            elif current >= event.total:
                eta_text = "完了"
            if eta_text:
                details.append(eta_text)
            self.progress_bar.setFormat(f"{amount} ({percent})")
        else:
            self.progress_bar.setRange(0, 0)
            details.append("処理継続中")
            self.progress_bar.setFormat(f"{stage}  {event.message}")

        details.append(elapsed_text)
        if event.detail:
            details.append(event.detail)
        since_update = now - (self._wordpress_progress_last_update_at or now)
        if since_update >= 2:
            details.append(
                "サーバー応答待ち "
                f"(最終更新から {self._format_progress_duration(since_update)})"
            )
        self.summary.setText(" / ".join(details))

    def _stop_wordpress_progress(self) -> None:
        self._wordpress_progress_timer.stop()
        self._wordpress_progress_event = None
        self._wordpress_progress_phase_key = None

    @Slot(object)
    def _dry_run_progress(self, event: object) -> None:
        if not isinstance(event, DryRunProgress):
            return
        now = time.monotonic()
        phase_key = (event.stage, event.message, event.unit, event.total)
        if phase_key != self._dry_run_progress_phase_key:
            self._dry_run_progress_phase_started_at = now
            self._dry_run_progress_phase_key = phase_key
        self._dry_run_progress_event = event
        self._dry_run_progress_last_update_at = now
        self._refresh_dry_run_progress()

    def _refresh_dry_run_progress(self) -> None:
        event = self._dry_run_progress_event
        if event is None:
            return
        now = time.monotonic()
        elapsed = now - (self._dry_run_progress_started_at or now)
        stage = f"手順 {event.stage}/{event.total_stages}"
        details = [stage, event.message]
        determinate = (
            event.current is not None
            and event.total is not None
            and event.total > 0
        )
        if determinate:
            current = min(max(event.current, 0), event.total)
            ratio = current / event.total
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(round(ratio * 1000))
            amount = f"{current}/{event.total}"
            percent = f"{ratio * 100:.1f}%"
            details.extend((amount, percent))
            phase_elapsed = now - (self._dry_run_progress_phase_started_at or now)
            if 0 < current < event.total and phase_elapsed >= 1:
                eta_seconds = phase_elapsed * (event.total - current) / current
                details.append(f"残り約 {self._format_progress_duration(eta_seconds)}")
            elif current >= event.total:
                details.append("この工程は完了")
            self.progress_bar.setFormat(f"{event.message}  {amount}  {percent}")
        else:
            self.progress_bar.setRange(0, 0)
            if event.current:
                details.append(f"{event.current}フォルダ確認済み")
            self.progress_bar.setFormat(f"{stage}  {event.message}")
            details.append("全体数を確認中")

        details.append(f"経過 {self._format_progress_duration(elapsed)}")
        if event.detail:
            details.append(event.detail)
        since_update = now - (self._dry_run_progress_last_update_at or now)
        if since_update >= 2:
            details.append(
                "サーバー応答待ち "
                f"(最終更新から {self._format_progress_duration(since_update)})"
            )
        self.summary.setText(" / ".join(details))

    def _stop_dry_run_progress(self) -> None:
        self._dry_run_progress_timer.stop()
        self._dry_run_progress_event = None
        self._dry_run_progress_phase_key = None

    @Slot(object)
    def _wordpress_setup_completed(self, payload: object) -> None:
        self._stop_wordpress_progress()
        result, homepage, login = payload
        health_ok = homepage.ok and login.ok
        self._finish_operation(
            "succeeded" if health_ok else "failed",
            details={
                "database": result.db_name,
                "database_user": result.db_user,
                "file_count": result.transferred_files,
                "table_count": result.table_count,
                "homepage_status": homepage.status_code,
                "login_status": login.status_code,
                "public_url": result.public_url,
                "detected_home_url": result.detected_home_url,
                "detected_site_url": result.detected_site_url,
                "replaced_urls": list(result.replaced_urls),
            },
            error=None if health_ok else "HTTPS health check failed after setup",
        )
        if health_ok:
            self._retire_resumable_operations("wordpress_setup")
        self.progress_bar.setVisible(False)
        if health_ok:
            self.summary.setText(
                f"WordPressセットアップ完了 / {result.transferred_files}ファイル / "
                f"{result.table_count}テーブル。HTTPS動作確認済みです。"
            )
            top_url = html.escape(homepage.url, quote=True)
            login_url = html.escape(login.url, quote=True)
            completion = QMessageBox(self)
            completion.setIcon(QMessageBox.Information)
            completion.setWindowTitle("WordPressセットアップ完了")
            completion.setTextFormat(Qt.RichText)
            completion.setText(
                "<p><b>WordPressセットアップは正常に完了しました。</b></p>"
                f'<p>トップページ: <a href="{top_url}">{top_url}</a><br>'
                f'ログイン画面: <a href="{login_url}">{login_url}</a><br>'
                f"HTTPS確認: トップ {homepage.status_code} / ログイン {login.status_code}</p>"
                f"<p>DB: {html.escape(result.db_name)}<br>"
                f"DBユーザー: {html.escape(result.db_user)}<br>"
                f"DBテーブル: {result.table_count}<br>"
                f"今回転送したファイル: {result.transferred_files}</p>"
                f"<p>DB内で検出したhome: {html.escape(result.detected_home_url)}<br>"
                f"DB内で検出したsiteurl: {html.escape(result.detected_site_url)}<br>"
                f"最終公開URL: {html.escape(result.public_url)}</p>"
            )
            completion.setStandardButtons(QMessageBox.Ok)
            for label in completion.findChildren(QLabel):
                label.setOpenExternalLinks(True)
                label.setTextInteractionFlags(
                    Qt.TextBrowserInteraction | Qt.TextSelectableByMouse
                )
            completion.exec()
        else:
            self.summary.setText(
                "ファイルとDBのセットアップは完了しましたが、HTTPS確認に失敗しました。"
            )
            QMessageBox.warning(
                self,
                "WordPress配置完了・HTTPS確認が必要",
                "ファイル配置とDB更新は完了しましたが、"
                "公開URLの動作確認に失敗しました。\n\n"
                f"トップページ: {homepage.url}\n"
                f"結果: {homepage.error or homepage.status_code}\n\n"
                f"ログイン画面: {login.url}\n"
                f"結果: {login.error or login.status_code}\n\n"
                f"DB: {result.db_name}\n"
                f"DBユーザー: {result.db_user}\n"
                f"DBテーブル: {result.table_count}\n"
                f"最終公開URL: {result.public_url}\n\n"
                "上記URL、XServerのドメイン設定、SSL設定を"
                "確認してください。",
            )

    @Slot(str)
    def _wordpress_setup_failed(self, message: str) -> None:
        self._stop_wordpress_progress()
        self._finish_operation("failed", error=message)
        self.progress_bar.setVisible(False)
        self.summary.setText("WordPress初回セットアップを完了できませんでした。")
        QMessageBox.critical(self, "WordPressセットアップエラー", message)

    @Slot(str)
    def _wordpress_setup_cancelled(self, message: str) -> None:
        self._stop_wordpress_progress()
        self._finish_operation("cancelled", error=message)
        self.progress_bar.setVisible(False)
        self.summary.setText(
            "WordPressセットアップを安全な区切りで停止しました。再開できます。"
        )

    @Slot(object)
    def _laravel_setup_progress(self, event: object) -> None:
        if isinstance(event, str):
            event = LaravelSetupProgress(
                step=1,
                total_steps=LARAVEL_SETUP_TOTAL_STEPS,
                message=event,
            )
        if not isinstance(event, LaravelSetupProgress):
            return
        now = time.monotonic()
        phase_key = (event.step, event.message, event.unit, event.total)
        if phase_key != self._laravel_progress_phase_key:
            self._laravel_progress_phase_started_at = now
            self._laravel_progress_phase_key = phase_key
        self._laravel_progress_event = event
        self._laravel_progress_last_update_at = now
        self._refresh_laravel_progress()

    def _refresh_laravel_progress(self) -> None:
        event = self._laravel_progress_event
        if event is None:
            return
        now = time.monotonic()
        overall_elapsed = now - (self._laravel_progress_started_at or now)
        stage = f"手順 {event.step}/{event.total_steps}"
        elapsed_text = f"経過 {self._format_progress_duration(overall_elapsed)}"
        details = [stage, event.message]

        determinate = (
            event.current is not None
            and event.total is not None
            and event.total > 0
        )
        if determinate:
            assert event.current is not None and event.total is not None
            current = min(max(event.current, 0), event.total)
            ratio = current / event.total
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(round(ratio * 1000))
            amount = f"{current}/{event.total}"
            percent = f"{ratio * 100:.1f}%"
            details.extend((amount, percent))
            self.progress_bar.setFormat(f"{amount} ({percent})")
        else:
            self.progress_bar.setRange(0, 0)
            details.append("処理継続中")
            self.progress_bar.setFormat(f"{stage}  {event.message}")

        details.append(elapsed_text)
        if event.detail:
            details.append(event.detail)
        self.summary.setText(" / ".join(details))

    def _stop_laravel_progress(self) -> None:
        self._laravel_progress_timer.stop()
        self._laravel_progress_event = None
        self._laravel_progress_phase_key = None

    @Slot(object)
    def _laravel_setup_completed(self, payload: object) -> None:
        self._stop_laravel_progress()
        result, homepage = payload
        health_ok = homepage.ok
        self._finish_operation(
            "succeeded" if health_ok else "failed",
            details={
                "database": result.db_name,
                "database_user": result.db_user,
                "app_file_count": result.transferred_app_files,
                "public_file_count": result.transferred_public_files,
                "homepage_status": homepage.status_code,
                "public_url": result.public_url,
            },
            error=None if health_ok else "HTTPS health check failed after setup",
        )
        self.progress_bar.setVisible(False)
        if health_ok:
            self.summary.setText(
                f"Laravelセットアップ完了 / {result.transferred_app_files + result.transferred_public_files}ファイル転送 / "
                f"DB: {result.db_name}"
            )
            completion = QMessageBox(self)
            completion.setIcon(QMessageBox.Information)
            completion.setWindowTitle("Laravelセットアップ完了")
            completion.setTextFormat(Qt.RichText)
            top_url = html.escape(homepage.url, quote=True)
            completion.setText(
                "<p><b>Laravelセットアップは正常に完了しました。</b></p>"
                f'<p>公開URL: <a href="{top_url}">{top_url}</a><br>'
                f"HTTPS確認: ステータス {homepage.status_code}</p>"
                f"<p>DB: {html.escape(result.db_name)}<br>"
                f"DBユーザー: {html.escape(result.db_user)}<br>"
                f"転送ファイル: 本体 {result.transferred_app_files} / 公開 {result.transferred_public_files}</p>"
                f"<p>公開URL: {html.escape(result.public_url)}</p>"
            )
            completion.setStandardButtons(QMessageBox.Ok)
            for label in completion.findChildren(QLabel):
                label.setOpenExternalLinks(True)
                label.setTextInteractionFlags(
                    Qt.TextBrowserInteraction | Qt.TextSelectableByMouse
                )
            completion.exec()
        else:
            self.summary.setText(
                "ファイルとDBのセットアップは完了しましたが、HTTPS確認に失敗しました。"
            )
            QMessageBox.warning(
                self,
                "Laravel配置完了・HTTPS確認が必要",
                "ファイル配置とDB初期化は完了しましたが、"
                "公開URLの動作確認に失敗しました。\n\n"
                f"公開URL: {homepage.url}\n"
                f"結果: {homepage.error or homepage.status_code}\n\n"
                f"DB: {result.db_name}\n"
                f"DBユーザー: {result.db_user}\n"
                f"公開URL: {result.public_url}\n\n"
                "XServerのドメイン設定、SSL設定、.htaccess等を確認してください。",
            )

    @Slot(str)
    def _laravel_setup_failed(self, message: str) -> None:
        self._stop_laravel_progress()
        self._finish_operation("failed", error=message)
        self.progress_bar.setVisible(False)
        self.summary.setText("Laravel初回セットアップを完了できませんでした。")
        QMessageBox.critical(self, "Laravelセットアップエラー", message)

    @Slot(str)
    def _laravel_setup_cancelled(self, message: str) -> None:
        self._stop_laravel_progress()
        self._finish_operation("cancelled", error=message)
        self.progress_bar.setVisible(False)
        self.summary.setText(
            "Laravelセットアップを安全な区切りで停止しました。"
        )

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
            try:
                self.store.delete(self._current_project.project_id)
            except SecureStoreError as exc:
                QMessageBox.critical(
                    self,
                    "プロジェクトを削除できません",
                    "OS資格情報ストアから確認コードを削除できなかったため、"
                    f"プロジェクトを残しました。\n\n{exc}",
                )
                return
            self._current_project = None
            self._reload_projects()

    @Slot()
    def _show_history(self) -> None:
        project_id = self._current_project.project_id if self._current_project else None
        dialog = HistoryDialog(self.store, project_id, self)
        dialog.exec()
        if dialog.selected_backup_id:
            self._start_restore(dialog.selected_backup_id)

    def _latest_resumable_operation(self):
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None:
            return None
        for operation in self.store.list_operations(project.project_id, limit=100):
            if (
                operation.mapping_id == mapping.mapping_id
                and operation.status in {"cancelled", "failed", "interrupted"}
                and operation.details.get("resumable") is True
                and operation.kind in {
                    "deployment",
                    "wordpress_setup",
                    "laravel_setup",
                    "force_overwrite",
                    "dry_run",
                }
            ):
                return operation
        return None

    def _retire_resumable_operations(self, kind: str) -> None:
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None:
            return
        for operation in self.store.list_operations(project.project_id, limit=1_000):
            if (
                operation.mapping_id == mapping.mapping_id
                and operation.kind == kind
                and operation.status in {"cancelled", "failed", "interrupted"}
                and operation.details.get("resumable") is True
            ):
                details = dict(operation.details)
                details["resumable"] = False
                details["resolved_by_successful_resume"] = True
                self.store.update_operation_details(operation.operation_id, details)

    @Slot()
    def _request_operation_cancel(self) -> None:
        if self._thread is None or self._worker is None:
            return
        cancel = getattr(self._worker, "cancel", None)
        if not callable(cancel):
            QMessageBox.information(
                self,
                "停止できない処理",
                "この処理は安全な中断に対応していません。",
            )
            return
        if QMessageBox.question(
            self,
            "処理を停止",
            "安全な区切りで処理を停止しますか？\n"
            "サーバーコマンドの実行中は、完了後の区切りまで待ちます。",
        ) != QMessageBox.Yes:
            return
        cancel()
        self.stop_button.setEnabled(False)
        self.stop_button.setText("停止を要求済み…")
        self.summary.setText("停止要求を受け付けました。安全な区切りまで待っています…")

    @Slot()
    def _resume_operation(self) -> None:
        operation = self._latest_resumable_operation()
        project = self._current_project
        mapping = self._selected_mapping()
        if operation is None or project is None or mapping is None:
            return
        if operation.kind == "wordpress_setup":
            self._open_wordpress_setup(True)
            return
        if operation.kind == "laravel_setup":
            self._start_laravel_setup()
            return
        if operation.kind == "dry_run":
            if operation.details.get("auto_sync"):
                self._start_auto_sync()
            else:
                self._start_dry_run()
            return
        if operation.kind == "deployment":
            stored = self.store.get_plan(str(operation.details.get("plan_id", "")))
            if stored is None or stored.status not in {"ready", "expired"}:
                QMessageBox.warning(
                    self, "再開できません", "保存済みの同期計画またはステージングが見つかりません。"
                )
                return
            backup_id = operation.details.get("backup_id")
            backup = self.store.get_backup(str(backup_id)) if backup_id else None
            self._backup_skipped = bool(operation.details.get("backup_skipped"))
            self._approved_delete_paths = tuple(operation.details.get("delete_paths", []))
            counts = stored.plan.counts()
            upload_bytes = sum(
                change.local.size or 0
                for change in stored.plan.changes
                if change.change_type in {ChangeType.ADD, ChangeType.UPDATE}
                and change.local is not None
            )
            approval = DeploymentApprovalDialog(
                domain=project.domain,
                remote_root=mapping.remote_root,
                add_count=counts[ChangeType.ADD],
                update_count=counts[ChangeType.UPDATE],
                upload_bytes=upload_bytes,
                backup_id=backup.backup_id if backup else None,
                backup_skipped=self._backup_skipped,
                delete_count=len(self._approved_delete_paths),
                parent=self,
            )
            if approval.exec() == QDialog.Accepted:
                self._run_deployment(
                    project,
                    mapping,
                    stored,
                    backup,
                    approval.selected_health_check_path(),
                    resume=True,
                )
            return
        selected = tuple(operation.details.get("selected_paths", []))
        completed = set(operation.details.get("completed_paths", []))
        remaining = tuple(path for path in selected if path not in completed)
        if not remaining:
            QMessageBox.information(
                self, "再開不要", "保存された対象ファイルはすべて完了しています。"
            )
            return
        approval = ForceOverwriteApprovalDialog(
            domain=project.domain,
            remote_root=mapping.remote_root,
            file_paths=remaining,
            parent=self,
        )
        if approval.exec() != QDialog.Accepted:
            return
        remaining = approval.selected_file_paths()
        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self._update_action_buttons()
        self.progress_bar.setRange(0, max(len(remaining), 1))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("無条件上書きを再開 %v/%m")
        self.progress_bar.setVisible(True)
        self.summary.setText("無条件上書きを未完了ファイルから再開中…")
        self._begin_operation(
            "force_overwrite",
            details={
                "resumable": True,
                "selected_paths": list(remaining),
                "resumed_from": operation.operation_id,
            },
        )
        thread = QThread(self)
        worker = ForceOverwriteWorker(
            project=project,
            mapping=mapping,
            passphrase=self.passphrase_edit.text() or None,
            selected_paths=remaining,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._force_overwrite_progress)
        worker.completed.connect(self._force_overwrite_completed)
        worker.failed.connect(self._force_overwrite_failed)
        worker.cancelled.connect(self._force_overwrite_cancelled)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        self.stop_button.setVisible(True)
        self.stop_button.setEnabled(True)
        thread.start()

    def _begin_operation(self, kind: str, details: dict | None = None) -> None:
        mapping = self._selected_mapping()
        if self._current_project is None or mapping is None:
            self._current_operation_id = None
            return
        operation_details = {
            "mode": "write"
            if kind in {
                "backup",
                "deployment",
                "force_overwrite",
                "restore",
                "wordpress_setup",
            }
            else "read_only"
        }
        operation_details.update(details or {})
        operation = self.store.start_operation(
            project_id=self._current_project.project_id,
            mapping_id=mapping.mapping_id,
            kind=kind,
            details=operation_details,
        )
        self._current_operation_id = operation.operation_id
        self._current_operation_details = operation_details

    def _update_operation_checkpoint(self, checkpoint: dict) -> None:
        if self._current_operation_id is None:
            return
        self._current_operation_details["checkpoint"] = dict(checkpoint)
        self._current_operation_details["resumable"] = True
        self.store.update_operation_details(
            self._current_operation_id, self._current_operation_details
        )

    def _finish_operation(
        self,
        status: str,
        *,
        details: dict | None = None,
        error: str | None = None,
    ) -> None:
        if self._current_operation_id is None:
            return
        merged_details = dict(self._current_operation_details)
        merged_details.update(details or {})
        self.store.finish_operation(
            self._current_operation_id,
            status=status,
            details=merged_details,
            error=error,
        )
        self._current_operation_id = None
        self._current_operation_details = {}

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

    def _set_auto_sync_controls_locked(self, locked: bool) -> None:
        for widget in (
            self.project_combo,
            self.mapping_combo,
            self.site_combo,
            self.path_edit,
            self.host_edit,
            self.port_spin,
            self.user_edit,
            self.remote_root_edit,
            self.fingerprint_edit,
            self.key_edit,
            self.passphrase_edit,
            self.new_project_button,
            self.edit_project_button,
            self.delete_project_button,
            self.history_button,
            self.recovery_button,
            self.browse_button,
            self.key_browse_button,
            self.scan_button,
            self.dry_run_button,
            self.force_overwrite_button,
            self.revalidate_button,
            self.backup_button,
            self.deploy_button,
            self.wordpress_setup_button,
            self.laravel_setup_button,
        ):
            widget.setEnabled(not locked)
        self.auto_sync_button.setEnabled(not locked)
        if hasattr(self, "wordpress_setup_action"):
            self.wordpress_setup_action.setEnabled(not locked)
        if hasattr(self, "laravel_setup_action"):
            self.laravel_setup_action.setEnabled(not locked)
        if not locked:
            self._update_setup_panel()

    def _set_auto_stage(self, step: int, label: str) -> None:
        self.progress_bar.setRange(0, 4)
        self.progress_bar.setValue(step)
        self.progress_bar.setFormat(f"{step}/4 (%p%)")
        self.progress_bar.setVisible(True)
        self.summary.setText(f"自動実行 {step}/4 — {label}")

    def _stop_auto_sync(self, *, hide_progress: bool = True) -> None:
        self._auto_sync_active = False
        self._auto_sync_stage = None
        self._auto_sync_preferences = None
        self._auto_backup_skipped = False
        self._auto_health_check_path = None
        self._auto_dry_run_result = None
        self._set_auto_sync_controls_locked(False)
        if hide_progress:
            self.progress_bar.setVisible(False)
        self._update_action_buttons()

    @Slot()
    def _start_auto_sync(self) -> None:
        if self._thread is not None or self._auto_sync_active:
            return
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None:
            QMessageBox.information(
                self,
                "自動実行できません",
                "保存済みプロジェクトと同期対象を選択してください。",
            )
            return
        self._auto_sync_active = True
        self._auto_sync_stage = "dry_run"
        self._auto_sync_preferences = load_auto_sync_preferences(
            self.settings, project.project_id
        )
        self._auto_backup_skipped = False
        self._auto_health_check_path = None
        self._auto_dry_run_result = None
        self._set_auto_sync_controls_locked(True)
        self._update_action_buttons()
        self._set_auto_stage(1, "読み取り専用ドライラン")
        self._start_dry_run()
        if self._thread is None:
            self._stop_auto_sync()

    def _continue_auto_sync(self) -> None:
        if not self._auto_sync_active or self._thread is not None:
            return
        if self._auto_sync_stage == "approval":
            result = self._auto_dry_run_result
            self._auto_dry_run_result = None
            if result is None:
                self.summary.setText(
                    "自動実行を停止しました。ドライラン結果がありません。"
                )
                self._stop_auto_sync()
                return
            self._prepare_auto_after_dry_run(result)
            if self._auto_sync_active and self._auto_sync_stage == "revalidation":
                QTimer.singleShot(0, self._continue_auto_sync)
            return
        if self._auto_sync_stage == "revalidation":
            self._set_auto_stage(2, "計画を再検証")
            self._start_revalidation()
        elif self._auto_sync_stage == "backup":
            self._set_auto_stage(3, "バックアップを作成")
            self._start_auto_backup()
        elif self._auto_sync_stage == "deployment":
            self._set_auto_stage(4, "承認付き同期")
            self._start_auto_deployment()
        if self._thread is None and self._auto_sync_active:
            self._stop_auto_sync()

    def _start_auto_backup(self) -> None:
        project = self._current_project
        mapping = self._selected_mapping()
        stored = self.store.get_plan(self._current_plan_id or "")
        if project is None or mapping is None or stored is None:
            QMessageBox.warning(
                self, "自動実行を停止", "バックアップ対象の計画を取得できません。"
            )
            return
        self._approved_delete_paths = ()
        self._run_backup(project, mapping, stored)

    def _start_auto_deployment(self) -> None:
        project = self._current_project
        mapping = self._selected_mapping()
        stored = self.store.get_plan(self._current_plan_id or "")
        backups = {
            backup.backup_id: backup
            for backup in self.store.list_backups(project.project_id)
        } if project else {}
        backup = backups.get(self._current_backup_id) if self._current_backup_id else None
        if (
            project is None
            or mapping is None
            or stored is None
            or (
                not self._backup_skipped
                and (backup is None or backup.plan_id != stored.plan.plan_id)
            )
        ):
            QMessageBox.warning(
                self, "自動実行を停止", "この計画に対応するバックアップがありません。"
            )
            return
        self._run_deployment(
            project,
            mapping,
            stored,
            backup,
            self._auto_health_check_path,
        )

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
        if not self._require_runtime_paths_confirmation():
            return
        self._reset_write_state()
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
        self._begin_operation("dry_run")
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
        if not self._require_runtime_paths_confirmation():
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
        self._begin_operation("revalidation")
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

    @Slot()
    def _start_backup(self) -> None:
        if self._thread is not None or self._validated_plan_id != self._current_plan_id:
            return
        if not self._require_runtime_paths_confirmation():
            return
        project = self._current_project
        mapping = self._selected_mapping()
        stored = self.store.get_plan(self._current_plan_id or "")
        if project is None or mapping is None or stored is None:
            return
        update_count = stored.plan.counts()[ChangeType.UPDATE]
        confirmation = BackupConfirmationDialog(
            update_count=update_count,
            backup_root=self._backup_root(),
            remote_only_paths=tuple(
                change.relative_path
                for change in stored.plan.changes
                if change.change_type is ChangeType.REMOTE_ONLY
            ),
            parent=self,
        )
        if confirmation.exec() != QDialog.Accepted:
            return
        self._approved_delete_paths = confirmation.selected_delete_paths()
        if confirmation.should_skip_backup():
            self._backup_skipped = True
            self._current_backup_id = None
            self._update_action_buttons()
            self.summary.setText(
                "バックアップをスキップしました。復元できないことを確認して承認付き同期へ進んでください。"
            )
            return
        self._backup_skipped = False
        self._run_backup(project, mapping, stored)

    def _run_backup(
        self,
        project: Project,
        mapping: SyncMapping,
        stored: StoredPlan,
    ) -> None:

        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.revalidate_button.setEnabled(False)
        self.backup_button.setEnabled(False)
        self.deploy_button.setEnabled(False)
        self.summary.setText("バックアップ作成中… 公開ファイルは変更しません。")
        self._begin_operation("backup")
        thread = QThread(self)
        worker = BackupWorker(
            stored=stored,
            project=project,
            mapping=mapping,
            passphrase=self.passphrase_edit.text() or None,
            backup_root=self._backup_root(),
            delete_paths=self._approved_delete_paths,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._backup_completed)
        worker.blocked.connect(self._backup_blocked)
        worker.failed.connect(self._backup_failed)
        worker.completed.connect(thread.quit)
        worker.blocked.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def _start_deployment(self) -> None:
        if (
            self._thread is not None
            or self._validated_plan_id != self._current_plan_id
            or (self._current_backup_id is None and not self._backup_skipped)
        ):
            return
        if not self._require_runtime_paths_confirmation():
            return
        project = self._current_project
        mapping = self._selected_mapping()
        stored = self.store.get_plan(self._current_plan_id or "")
        backups = {
            backup.backup_id: backup for backup in self.store.list_backups(project.project_id)
        } if project else {}
        backup = backups.get(self._current_backup_id) if self._current_backup_id else None
        if (
            project is None
            or mapping is None
            or stored is None
            or (
                not self._backup_skipped
                and (backup is None or backup.plan_id != stored.plan.plan_id)
            )
        ):
            QMessageBox.warning(self, "同期できません", "この計画に対応するバックアップがありません。")
            return
        counts = stored.plan.counts()
        upload_bytes = sum(
            change.local.size or 0
            for change in stored.plan.changes
            if change.change_type in {ChangeType.ADD, ChangeType.UPDATE}
            and change.local is not None
        )
        approval = DeploymentApprovalDialog(
            domain=project.domain,
            remote_root=mapping.remote_root,
            add_count=counts[ChangeType.ADD],
            update_count=counts[ChangeType.UPDATE],
            upload_bytes=upload_bytes,
            backup_id=backup.backup_id if backup is not None else None,
            backup_skipped=self._backup_skipped,
            delete_count=len(self._approved_delete_paths),
            parent=self,
        )
        if approval.exec() != QDialog.Accepted:
            return
        self._run_deployment(
            project,
            mapping,
            stored,
            backup,
            approval.selected_health_check_path(),
        )

    def _run_deployment(
        self,
        project: Project,
        mapping: SyncMapping,
        stored: StoredPlan,
        backup: BackupRecord | None,
        health_check_path: str | None,
        *,
        resume: bool = False,
    ) -> None:
        counts = stored.plan.counts()

        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.revalidate_button.setEnabled(False)
        self.backup_button.setEnabled(False)
        self.deploy_button.setEnabled(False)
        self.summary.setText("承認済み同期を実行中… 一時転送と検証を行っています。")
        total_changes = (
            counts[ChangeType.ADD]
            + counts[ChangeType.UPDATE]
            + len(self._approved_delete_paths)
        )
        self.progress_bar.setRange(0, max(total_changes, 1))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("承認済み同期 %v/%m")
        self.progress_bar.setVisible(True)
        self._current_plan_id = stored.plan.plan_id
        self._begin_operation(
            "deployment",
            details={
                "resumable": True,
                "plan_id": stored.plan.plan_id,
                "backup_id": backup.backup_id if backup is not None else None,
                "backup_skipped": self._backup_skipped,
                "delete_paths": list(self._approved_delete_paths),
                "health_check_path": health_check_path,
                "resumed": resume,
            },
        )
        thread = QThread(self)
        worker = DeployWorker(
            stored=stored,
            project=project,
            mapping=mapping,
            passphrase=self.passphrase_edit.text() or None,
            authorization=DeploymentAuthorization(
                plan_id=stored.plan.plan_id,
                backup_plan_id=backup.plan_id if backup is not None else None,
                remote_root=mapping.remote_root,
                approved=True,
                backup_skipped=self._backup_skipped,
                delete_paths=self._approved_delete_paths,
            ),
            health_check_path=health_check_path,
            resume=resume,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._deployment_completed)
        worker.blocked.connect(self._deployment_blocked)
        worker.failed.connect(self._deployment_failed)
        worker.cancelled.connect(self._deployment_cancelled)
        worker.progress.connect(self._deployment_progress)
        worker.completed.connect(thread.quit)
        worker.blocked.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        self.stop_button.setVisible(True)
        self.stop_button.setEnabled(True)
        thread.start()

    @Slot()
    def _start_force_overwrite(self) -> None:
        if self._thread is not None or not self._require_runtime_paths_confirmation():
            return
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None:
            return
        if not Path(mapping.local_root).expanduser().is_dir():
            QMessageBox.warning(self, "フォルダを確認", "同期元のローカルフォルダを読み取れません。")
            return
        preset = get_preset(project.site_type)
        try:
            candidates = collect_overwrite_candidates(
                Path(mapping.local_root),
                exclude_patterns=preset.exclude_patterns + mapping.exclude_patterns,
                protect_patterns=preset.protect_patterns + mapping.protect_patterns,
            )
        except Exception as exc:
            QMessageBox.critical(self, "上書き対象を確認できません", str(exc))
            return
        if not candidates:
            QMessageBox.warning(
                self,
                "上書き対象がありません",
                "除外・保護ルール適用後に上書きできるローカルファイルがありません。",
            )
            return
        approval = ForceOverwriteApprovalDialog(
            domain=project.domain,
            remote_root=mapping.remote_root,
            file_paths=candidates,
            parent=self,
        )
        if approval.exec() != QDialog.Accepted:
            return
        selected_paths = approval.selected_file_paths()
        if self._current_plan_id is not None:
            self.store.set_plan_status(self._current_plan_id, "invalid")
            self._current_plan_id = None
        self._reset_write_state()
        cleanup_stale_staging(self.store, self.staging_parent)
        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.force_overwrite_button.setEnabled(False)
        self.revalidate_button.setEnabled(False)
        self.backup_button.setEnabled(False)
        self.deploy_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("無条件上書きの準備中…")
        self.progress_bar.setVisible(True)
        self.summary.setText(
            "無条件上書き中… 差分照合・バックアップ・転送後ハッシュ検証は行いません。"
        )
        self._begin_operation(
            "force_overwrite",
            details={
                "resumable": True,
                "selected_paths": list(selected_paths),
            },
        )
        thread = QThread(self)
        worker = ForceOverwriteWorker(
            project=project,
            mapping=mapping,
            passphrase=self.passphrase_edit.text() or None,
            selected_paths=selected_paths,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._force_overwrite_progress)
        worker.completed.connect(self._force_overwrite_completed)
        worker.failed.connect(self._force_overwrite_failed)
        worker.cancelled.connect(self._force_overwrite_cancelled)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        self.stop_button.setVisible(True)
        self.stop_button.setEnabled(True)
        thread.start()

    @Slot()
    def _start_recovery_scan(self) -> None:
        if self._thread is not None:
            return
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None:
            return
        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self._update_action_buttons()
        self.summary.setText("残存一時ファイルを読み取り専用で確認しています…")
        self._begin_operation("recovery_scan")
        thread = QThread(self)
        worker = RecoveryScanWorker(
            project, mapping, self.passphrase_edit.text() or None
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._recovery_scan_completed)
        worker.failed.connect(self._recovery_scan_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _start_restore(self, backup_id: str) -> None:
        if self._thread is not None:
            return
        backup = self.store.get_backup(backup_id)
        project = self._current_project
        if backup is None or project is None or backup.project_id != project.project_id:
            QMessageBox.warning(self, "復元できません", "バックアップとプロジェクトが一致しません。")
            return
        mapping = next(
            (item for item in project.mappings if item.mapping_id == backup.mapping_id),
            None,
        )
        if mapping is not None and (issue := custom_runtime_paths_issue(project, mapping)):
            QMessageBox.warning(
                self,
                "復元できません",
                issue.message
                + "\n\n「プロジェクトを編集」から確認してください。",
            )
            return
        stored = self.store.get_plan(backup.plan_id)
        if (
            mapping is None
            or stored is None
            or backup.status != "available"
            or backup.file_count <= 0
        ):
            QMessageBox.warning(self, "復元できません", "復元に必要な計画またはバックアップがありません。")
            return
        mapping_index = self.mapping_combo.findData(mapping.mapping_id)
        if mapping_index >= 0:
            self.mapping_combo.setCurrentIndex(mapping_index)
        expected_hashes = {
            change.relative_path: (
                change.local.sha256 if change.change_type is ChangeType.UPDATE else None
            )
            for change in stored.plan.changes
            if (
                change.change_type is ChangeType.UPDATE
                and change.local is not None
                and change.local.sha256 is not None
            )
            or change.change_type is ChangeType.REMOTE_ONLY
        }
        if len(expected_hashes) < backup.file_count:
            QMessageBox.warning(self, "復元できません", "同期完了時のハッシュ記録が不足しています。")
            return
        approval = RestoreApprovalDialog(
            domain=project.domain,
            remote_root=mapping.remote_root,
            backup_id=backup.backup_id,
            file_count=backup.file_count,
            total_bytes=backup.total_bytes,
            parent=self,
        )
        if approval.exec() != QDialog.Accepted:
            return
        self._restore_backup_id = backup.backup_id
        self.scan_button.setEnabled(False)
        self.dry_run_button.setEnabled(False)
        self.revalidate_button.setEnabled(False)
        self.backup_button.setEnabled(False)
        self.deploy_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.summary.setText("現在ファイルとバックアップを検証して復元しています…")
        self._begin_operation("restore")
        thread = QThread(self)
        worker = RestoreWorker(
            project=project,
            mapping=mapping,
            manifest_path=backup.manifest_path,
            expected_current_hashes=expected_hashes,
            authorization=RestoreAuthorization(
                backup_id=backup.backup_id,
                destination_root=mapping.remote_root,
                approved=True,
            ),
            passphrase=self.passphrase_edit.text() or None,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._restore_completed)
        worker.failed.connect(self._restore_failed)
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
        if self._current_project is not None and not result.has_blocking_issues:
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
        persisted = " / 計画保存済み（15分間有効）" if self._current_plan_id else ""
        self._finish_operation(
            "blocked" if result.has_blocking_issues else "succeeded",
            details={
                "plan_id": result.plan.plan_id,
                "add": counts[ChangeType.ADD],
                "update": counts[ChangeType.UPDATE],
                "remote_only": counts[ChangeType.REMOTE_ONLY],
                "blocking": result.has_blocking_issues,
            },
        )
        self.summary.setText(
            f"ドライラン完了（{status}） / ADD {counts[ChangeType.ADD]:,} / "
            f"UPDATE {counts[ChangeType.UPDATE]:,} / "
            f"REMOTE_ONLY {counts[ChangeType.REMOTE_ONLY]:,} / "
            f"保護領域 {protected_count:,}件{persisted}"
        )
        if self._auto_sync_active and self._auto_sync_stage == "dry_run":
            self._auto_dry_run_result = result
            self._auto_sync_stage = "approval"

    def _prepare_auto_after_dry_run(self, result: DryRunResult) -> None:
        preferences = self._auto_sync_preferences
        project = self._current_project
        mapping = self._selected_mapping()
        if (
            preferences is None
            or project is None
            or mapping is None
            or result.has_blocking_issues
            or self._current_plan_id != result.plan.plan_id
        ):
            self.summary.setText(
                "自動実行を停止しました。ドライランの要確認項目を確認してください。"
            )
            self._stop_auto_sync()
            return

        counts = result.plan.counts()
        add_count = counts[ChangeType.ADD]
        update_count = counts[ChangeType.UPDATE]
        remote_only_count = counts[ChangeType.REMOTE_ONLY]
        changed_count = add_count + update_count
        upload_bytes = sum(
            change.local.size or 0
            for change in result.plan.changes
            if change.change_type in {ChangeType.ADD, ChangeType.UPDATE}
            and change.local is not None
        )
        policy_issues = auto_sync_policy_issues(
            preferences,
            add_count=add_count,
            update_count=update_count,
            remote_only_count=remote_only_count,
            upload_bytes=upload_bytes,
        )
        if policy_issues:
            self.summary.setText("自動実行を安全上限で停止しました。")
            QMessageBox.warning(
                self,
                "自動実行を停止",
                "\n".join(f"• {issue}" for issue in policy_issues),
            )
            self._stop_auto_sync()
            return
        if changed_count == 0:
            self.summary.setText(
                "自動実行完了。ADD・UPDATEはなく、XServerへの書き込みは行いませんでした。"
            )
            self._stop_auto_sync()
            return

        try:
            health_path = (
                normalize_health_check_path(preferences.health_check_path)
                if preferences.health_check_enabled
                else None
            )
        except ValueError as exc:
            QMessageBox.warning(self, "自動実行設定を確認", str(exc))
            self._stop_auto_sync()
            return
        backup_skipped = preferences.resolved_backup_skip(update_count)
        approval = AutoSyncApprovalDialog(
            domain=project.domain,
            remote_root=mapping.remote_root,
            add_count=add_count,
            update_count=update_count,
            remote_only_count=remote_only_count,
            upload_bytes=upload_bytes,
            backup_skipped=backup_skipped,
            health_check_path=health_path,
            require_domain_confirmation=(
                preferences.require_domain_confirmation
            ),
            parent=self,
        )
        if approval.exec() != QDialog.Accepted:
            self.summary.setText(
                "自動実行をキャンセルしました。XServerへの書き込みは行っていません。"
            )
            self._stop_auto_sync()
            return
        self._approved_delete_paths = ()
        self._auto_backup_skipped = backup_skipped
        self._auto_health_check_path = health_path
        self._auto_sync_stage = "revalidation"

    @Slot(str)
    def _dry_run_failed(self, message: str) -> None:
        self._finish_operation("failed", error=message)
        self.summary.setText("ドライランに失敗しました。リモートは変更されていません。")
        QMessageBox.critical(self, "ドライランエラー", message)
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(object)
    def _revalidation_completed(self, result: RevalidationResult) -> None:
        if result.valid:
            self._validated_plan_id = self._current_plan_id
            self._current_backup_id = None
            self._backup_skipped = False
            self._update_action_buttons()
            self._finish_operation("succeeded", details={"valid": True})
            self.summary.setText(
                "計画の再検証に成功しました。設定、ローカル、XServer、ステージングは一致しています。"
            )
            if self._auto_sync_active and self._auto_sync_stage == "revalidation":
                if self._auto_backup_skipped:
                    self._backup_skipped = True
                    self._current_backup_id = None
                    self._set_auto_stage(3, "バックアップを設定によりスキップ")
                    self._auto_sync_stage = "deployment"
                else:
                    self._auto_sync_stage = "backup"
            return
        if self._current_plan_id is not None:
            self.store.set_plan_status(self._current_plan_id, "invalid")
        cleanup_stale_staging(self.store, self.staging_parent)
        self._current_plan_id = None
        self._reset_write_state()
        self.revalidate_button.setEnabled(False)
        details = "\n".join(f"• {reason}" for reason in result.reasons)
        self._finish_operation(
            "blocked",
            details={"valid": False, "reasons": list(result.reasons)},
        )
        self.summary.setText("計画は無効になりました。再度ドライランを実行してください。")
        QMessageBox.warning(self, "再検証に失敗", details)
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(str)
    def _revalidation_failed(self, message: str) -> None:
        self._finish_operation("failed", error=message)
        self.summary.setText("再検証を完了できませんでした。計画は実行されていません。")
        QMessageBox.critical(self, "再検証エラー", message)
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(object)
    def _backup_completed(self, result: BackupResult) -> None:
        project = self._current_project
        mapping = self._selected_mapping()
        if project is None or mapping is None or self._current_plan_id is None:
            self._finish_operation("failed", error="Project selection changed during backup")
            return
        backup = BackupRecord(
            backup_id=result.backup_id,
            project_id=project.project_id,
            mapping_id=mapping.mapping_id,
            plan_id=self._current_plan_id,
            backup_directory=result.backup_directory,
            manifest_path=result.manifest_path,
            created_at=result.created_at,
            file_count=len(result.files),
            total_bytes=result.total_bytes,
        )
        self.store.save_backup(backup)
        self._current_backup_id = backup.backup_id
        self._backup_skipped = False
        self._finish_operation(
            "succeeded",
            details={
                "backup_id": backup.backup_id,
                "file_count": backup.file_count,
                "total_bytes": backup.total_bytes,
            },
        )
        self.summary.setText(
            f"バックアップ完了 / {backup.file_count}ファイル / {backup.total_bytes:,} bytes。"
            "承認付き同期へ進めます。"
        )
        if self._auto_sync_active and self._auto_sync_stage == "backup":
            self._auto_sync_stage = "deployment"

    @Slot(object)
    def _backup_blocked(self, result: RevalidationResult) -> None:
        if self._current_plan_id:
            self.store.set_plan_status(self._current_plan_id, "invalid")
        self._finish_operation(
            "blocked", details={"reasons": list(result.reasons)}
        )
        self._reset_write_state()
        self.summary.setText("再検証に失敗したためバックアップを作成しませんでした。")
        QMessageBox.warning(self, "バックアップを中止", "\n".join(result.reasons))
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(str)
    def _backup_failed(self, message: str) -> None:
        self._finish_operation("failed", error=message)
        self._reset_write_state()
        self.summary.setText("バックアップに失敗しました。公開ファイルは変更されていません。")
        QMessageBox.critical(self, "バックアップエラー", message)
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(object)
    def _deployment_completed(self, result: DeploymentResult) -> None:
        self.progress_bar.setVisible(False)
        if self._current_plan_id:
            self.store.set_plan_status(self._current_plan_id, "used")
        details = {
            "plan_id": result.plan_id,
            "file_count": len(result.files),
            "total_bytes": result.total_bytes,
            "backup_id": self._current_backup_id,
            "backup_skipped": self._backup_skipped,
            "deleted_files": list(result.deleted_files),
        }
        if result.health_check is not None:
            details["health_check"] = {
                "url": result.health_check.url,
                "ok": result.health_check.ok,
                "status_code": result.health_check.status_code,
                "error": result.health_check.error,
            }
        self._finish_operation(
            "succeeded",
            details=details,
        )
        self._retire_resumable_operations("deployment")
        if result.health_check is not None and not result.health_check.ok:
            health_detail = result.health_check.error or "不明なエラー"
            self.summary.setText(
                f"同期は完了しましたが、HTTPS動作確認に失敗しました: {health_detail}"
            )
            QMessageBox.warning(
                self,
                "同期完了・動作確認失敗",
                "ファイルの同期と転送後ハッシュ検証は完了しています。\n\n"
                f"確認先: {result.health_check.url}\n"
                f"結果: {health_detail}\n\n"
                "ブラウザとサーバーログを確認し、必要な場合は履歴から復元してください。",
            )
        else:
            health_text = ""
            if result.health_check is not None:
                health_text = f" HTTPS {result.health_check.status_code}を確認済みです。"
            self.summary.setText(
                f"同期完了 / {len(result.files)}ファイル / {result.total_bytes:,} bytes。"
                "転送後ハッシュを確認済みです。"
                f" 承認済み削除 {len(result.deleted_files)}件。"
                + health_text
            )
        self._current_plan_id = None
        self._reset_write_state()
        cleanup_stale_staging(self.store, self.staging_parent)
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(object)
    def _deployment_blocked(self, result: RevalidationResult) -> None:
        self.progress_bar.setVisible(False)
        if self._current_plan_id:
            self.store.set_plan_status(self._current_plan_id, "invalid")
        self._finish_operation(
            "blocked", details={"reasons": list(result.reasons)}
        )
        self._current_plan_id = None
        self._reset_write_state()
        cleanup_stale_staging(self.store, self.staging_parent)
        self.summary.setText("再検証に失敗したため同期を実行しませんでした。")
        QMessageBox.warning(self, "同期を中止", "\n".join(result.reasons))
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(str)
    def _deployment_failed(self, message: str) -> None:
        self.progress_bar.setVisible(False)
        self._finish_operation("failed", error=message)
        self._reset_write_state()
        self.summary.setText(
            "同期に失敗しました。適用済みファイルは可能な限り復元され、"
            "計画は再開用に保存されています。"
        )
        QMessageBox.critical(self, "同期エラー", message)
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(str)
    def _deployment_cancelled(self, message: str) -> None:
        self.progress_bar.setVisible(False)
        self._finish_operation("cancelled", error=message)
        self._reset_write_state()
        self.summary.setText(
            "承認付き同期を安全に停止しました。"
            "ステージング計画を再検証して再開できます。"
        )
        if self._auto_sync_active:
            self._stop_auto_sync()

    @Slot(int, int, str)
    def _deployment_progress(self, current: int, total: int, phase: str) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        ratio = current / max(total, 1)
        percent = f"{ratio * 100:.1f}%"
        self.progress_bar.setFormat(f"{current}/{total} ({percent})")
        prefix = "自動実行 4/4 — " if self._auto_sync_active else ""
        self.summary.setText(f"{prefix}承認済み同期を実行中… {phase}")

    @Slot(int, int, str)
    def _force_overwrite_progress(self, current: int, total: int, path: str) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)
        ratio = current / max(total, 1)
        percent = f"{ratio * 100:.1f}%"
        self.progress_bar.setFormat(f"{current}/{total} ({percent})")
        self.summary.setText(f"無条件上書き中… {path}")
        completed = list(self._current_operation_details.get("completed_paths", []))
        if path not in completed:
            completed.append(path)
            self._current_operation_details["completed_paths"] = completed
            if self._current_operation_id:
                self.store.update_operation_details(
                    self._current_operation_id, self._current_operation_details
                )

    @Slot(object)
    def _force_overwrite_completed(self, result: ForceOverwriteResult) -> None:
        self.progress_bar.setVisible(False)
        self._finish_operation(
            "succeeded",
            details={
                "file_count": len(result.files),
                "total_bytes": result.total_bytes,
                "remote_root": result.remote_root,
                "validation": "skipped",
            },
        )
        self._retire_resumable_operations("force_overwrite")
        self.summary.setText(
            f"無条件上書き完了 / {len(result.files)}ファイル / "
            f"{result.total_bytes:,} bytes。リモートのみのファイルは削除していません。"
        )
        self._current_plan_id = None
        self._reset_write_state()
        cleanup_stale_staging(self.store, self.staging_parent)

    @Slot(str)
    def _force_overwrite_failed(self, message: str) -> None:
        self.progress_bar.setVisible(False)
        self._finish_operation("failed", error=message)
        self._reset_write_state()
        self.summary.setText(
            "無条件上書きに失敗しました。このモードはバックアップとロールバックを行いません。"
        )
        QMessageBox.critical(self, "無条件上書きエラー", message)

    @Slot(str)
    def _force_overwrite_cancelled(self, message: str) -> None:
        self.progress_bar.setVisible(False)
        self._finish_operation("cancelled", error=message)
        self._reset_write_state()
        self.summary.setText(
            "無条件上書きをファイル境界で停止しました。"
            "完了済みファイルを除いて再開できます。"
        )

    @Slot(object)
    def _recovery_scan_completed(self, result: RecoveryAnalysisResult) -> None:
        self._finish_operation(
            "blocked" if result.artifacts or result.issues else "succeeded",
            details={
                "temporary_files": [item.temporary_path for item in result.artifacts],
                "issues": list(result.issues),
            },
        )
        if result.artifacts:
            self.summary.setText(
                f"残存一時ファイルを{len(result.artifacts)}件検出しました。"
                "自動削除は行っていません。"
            )
            RecoveryAnalysisDialog(result, self).exec()
        elif result.issues:
            self.summary.setText("安全性確認で読み取れない場所がありました。")
            QMessageBox.warning(self, "確認未完了", "\n".join(result.issues))
        else:
            self.summary.setText("安全性確認完了。残存一時ファイルはありません。")

    @Slot(str)
    def _recovery_scan_failed(self, message: str) -> None:
        self._finish_operation("failed", error=message)
        self.summary.setText("安全性確認を完了できませんでした。")
        QMessageBox.critical(self, "安全性確認エラー", message)

    @Slot(object)
    def _restore_completed(self, result: RestoreResult) -> None:
        if self._restore_backup_id:
            self.store.set_backup_status(self._restore_backup_id, "restored")
        self._finish_operation(
            "succeeded",
            details={
                "backup_id": result.backup_id,
                "file_count": len(result.files),
                "total_bytes": result.total_bytes,
            },
        )
        self.summary.setText(
            f"復元完了 / {len(result.files)}ファイル / {result.total_bytes:,} bytes。"
            "復元後のSHA-256を確認済みです。"
        )
        self._restore_backup_id = None
        self._reset_write_state()

    @Slot(str)
    def _restore_failed(self, message: str) -> None:
        self._finish_operation("failed", error=message)
        self._restore_backup_id = None
        self._reset_write_state()
        self.summary.setText("復元を中止しました。外部変更または検証エラーを確認してください。")
        QMessageBox.critical(self, "復元エラー", message)

    @Slot()
    def _scan_finished(self) -> None:
        if self._wordpress_setup_active:
            self._wordpress_setup_active = False
            self._set_auto_sync_controls_locked(False)
        self.scan_button.setEnabled(not self._auto_sync_active)
        self.dry_run_button.setEnabled(not self._auto_sync_active)
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self.stop_button.setVisible(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("処理を停止")
        self._refresh_ready_plan()
        if self._auto_sync_active and self._auto_sync_stage in {
            "approval",
            "revalidation",
            "backup",
            "deployment",
        }:
            QTimer.singleShot(0, self._continue_auto_sync)
        if self._close_when_idle:
            self._close_when_idle = False
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:
        if self._thread is None:
            event.accept()
            return
        cancel = getattr(self._worker, "cancel", None)
        if not callable(cancel):
            QMessageBox.warning(
                self,
                "処理中です",
                "実行中の処理が完了するまでアプリを閉じられません。",
            )
            event.ignore()
            return
        answer = QMessageBox.question(
            self,
            "処理を停止して終了",
            "実行中の処理を安全な区切りで停止し、その後アプリを終了しますか？\n"
            "次回起動後に再開できます。",
        )
        if answer != QMessageBox.Yes:
            event.ignore()
            return
        cancel()
        self._close_when_idle = True
        self.stop_button.setEnabled(False)
        self.stop_button.setText("停止後に終了…")
        event.ignore()


def main(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setOrganizationName("Docker XServer Sync")
    app.setOrganizationDomain("dxsync.local")
    app.setApplicationName("Docker XServer Sync")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
