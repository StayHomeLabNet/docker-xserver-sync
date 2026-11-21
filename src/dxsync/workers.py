from __future__ import annotations

import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal, Slot

from . import __version__
from .backup import RemoteBackupEngine, delete_remote_backup
from .cancellation import CancellationToken, OperationCancelled
from .deployment import DeploymentAuthorization, RemoteDeploymentEngine
from .force_overwrite import ForceOverwriteEngine
from .health import perform_https_health_check
from .laravel_setup import (
    LARAVEL_SETUP_TOTAL_STEPS,
    LaravelSetupEngine,
    LaravelSetupProgress,
    LaravelSetupRequest,
)
from .models import Project, SiteType, SyncMapping
from .planner import DEFAULT_PLAN_VALIDITY_MINUTES
from .presets import get_preset
from .progress_tracker import (
    OperationProgress,
    _ThrottledProgressEmitter,
    format_worker_error,
)
from .recovery import analyze_recovery_artifacts, scan_recovery_artifacts
from .remote import connect_sftp
from .restore import RemoteRestoreEngine, RestoreAuthorization
from .revalidation import PlanRevalidator
from .scanner import LocalScanner
from .service import DryRunProgress, DryRunService
from .storage import BackupRecord, StoredPlan
from .updater import check_for_updates
from .wordpress_setup import (
    WORDPRESS_SETUP_TOTAL_STEPS,
    WordPressSetupEngine,
    WordPressSetupProgress,
    WordPressSetupRequest,
)
from .xserver_api import XServerApiClient


def _connect_sftp(**kwargs):
    gui_module = sys.modules.get("dxsync.gui")
    fn = getattr(gui_module, "connect_sftp", None) if gui_module is not None else None
    return (fn or connect_sftp)(**kwargs)


def _perform_https_health_check(**kwargs):
    gui_module = sys.modules.get("dxsync.gui")
    fn = (
        getattr(gui_module, "perform_https_health_check", None)
        if gui_module is not None
        else None
    )
    return (fn or perform_https_health_check)(**kwargs)


def _protect_patterns(project: Project, mapping: SyncMapping) -> tuple[str, ...]:
    return get_preset(project.site_type).protect_patterns + mapping.protect_patterns


def _close_remote(client, sftp) -> None:
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


class ScanWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

    def __init__(self, root: Path, site_type: SiteType) -> None:
        super().__init__()
        self.root = root
        self.site_type = site_type
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        try:
            preset = get_preset(self.site_type)
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)

            def report(current: int, total: int | None, detail: str) -> None:
                emit_progress(
                    OperationProgress(
                        stage=1,
                        total_stages=1,
                        message=(
                            "ローカルファイルを列挙中"
                            if total is None
                            else "SHA-256を計算中"
                        ),
                        current=current,
                        total=total,
                        unit="directories" if total is None else "files",
                        detail=detail,
                        summary_prefix="ローカル検査中…",
                        remote_wait=False,
                    )
                )

            self.completed.emit(
                LocalScanner(preset.exclude_patterns).scan(
                    self.root,
                    progress_callback=report,
                    cancellation_token=self.cancellation_token,
                )
            )
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:  # worker boundary: surface the failure in the UI
            self.failed.emit(str(exc))


class DryRunWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

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
        plan_validity: timedelta = timedelta(minutes=DEFAULT_PLAN_VALIDITY_MINUTES),
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
        self.plan_validity = plan_validity
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            self.cancellation_token.raise_if_cancelled()
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)
            emit_progress(DryRunProgress(1, 4, "XServerへSSH接続中"))
            client, sftp = _connect_sftp(
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
                plan_validity=self.plan_validity,
                progress_callback=emit_progress,
                cancellation_token=self.cancellation_token,
            )
            self.completed.emit(result)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:  # worker boundary: surface the failure in the UI
            self.failed.emit(format_worker_error(exc))
        finally:
            _close_remote(client, sftp)


class RevalidationWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

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
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            self.cancellation_token.raise_if_cancelled()
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)
            emit_progress(
                OperationProgress(
                    stage=1,
                    total_stages=4,
                    message="XServerへSSH接続中",
                )
            )
            client, sftp = _connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )

            def report(event: DryRunProgress) -> None:
                emit_progress(
                    OperationProgress(
                        stage=event.stage,
                        total_stages=event.total_stages,
                        message=event.message,
                        current=event.current,
                        total=event.total,
                        unit=event.unit,
                        detail=event.detail,
                        phase_key=(
                            "revalidate",
                            event.stage,
                            event.message,
                            event.unit,
                            event.total,
                        ),
                        progress_label=event.message,
                        remote_wait=event.stage == 3,
                    )
                )

            result = PlanRevalidator().revalidate(
                stored=self.stored,
                project=self.project,
                mapping=self.mapping,
                sftp=sftp,
                progress_callback=report,
                cancellation_token=self.cancellation_token,
            )
            self.completed.emit(result)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            _close_remote(client, sftp)


class BackupWorker(QObject):
    completed = Signal(object)
    blocked = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

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
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            self.cancellation_token.raise_if_cancelled()
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)
            emit_progress(
                OperationProgress(
                    stage=1,
                    total_stages=4,
                    message="XServerへSSH接続中",
                )
            )
            client, sftp = _connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )

            def report_recovery(current: int, total: int | None, detail: str) -> None:
                emit_progress(
                    OperationProgress(
                        stage=2,
                        total_stages=4,
                        message="残存一時ファイルを確認中",
                        current=current,
                        total=total,
                        unit="directories" if total is None else "files",
                        detail=detail,
                    )
                )

            recovery = scan_recovery_artifacts(
                sftp,
                self.mapping.remote_root,
                protect_patterns=_protect_patterns(self.project, self.mapping),
                progress_callback=report_recovery,
                cancellation_token=self.cancellation_token,
            )
            if recovery.temporary_files or recovery.issues:
                raise RuntimeError(
                    "残存一時ファイルまたは未確認領域があります。先に安全性確認を実行してください。"
                )

            def report_revalidation(event: DryRunProgress) -> None:
                emit_progress(
                    OperationProgress(
                        stage=3,
                        total_stages=4,
                        message=f"計画再検証: {event.message}",
                        current=event.current,
                        total=event.total,
                        unit=event.unit,
                        detail=event.detail,
                        phase_key=(
                            "backup_revalidate",
                            event.stage,
                            event.message,
                            event.unit,
                            event.total,
                        ),
                        progress_label=f"計画再検証: {event.message}",
                        remote_wait=event.stage == 3,
                    )
                )

            revalidation = PlanRevalidator().revalidate(
                stored=self.stored,
                project=self.project,
                mapping=self.mapping,
                sftp=sftp,
                progress_callback=report_revalidation,
                cancellation_token=self.cancellation_token,
            )
            if not revalidation.valid:
                self.blocked.emit(revalidation)
                return

            def report_backup(current: int, total: int, detail: str) -> None:
                emit_progress(
                    OperationProgress(
                        stage=4,
                        total_stages=4,
                        message="バックアップファイルを作成中",
                        current=current,
                        total=total,
                        unit="files",
                        detail=detail,
                        phase_key=("backup", total),
                    )
                )

            result = RemoteBackupEngine().create_backup(
                sftp=sftp,
                plan=self.stored.plan,
                project_id=self.project.project_id,
                remote_root=self.mapping.remote_root,
                backup_root=self.backup_root,
                delete_paths=self.delete_paths,
                progress_callback=report_backup,
                cancellation_token=self.cancellation_token,
            )
            self.completed.emit(result)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            _close_remote(client, sftp)


class DeployWorker(QObject):
    completed = Signal(object)
    blocked = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    status_progress = Signal(object)
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
        total_stages = 5 if self.health_check_path is not None else 4
        try:
            if not self.stored.staging_path:
                raise RuntimeError("同期計画にステージングファイルがありません")
            self.cancellation_token.raise_if_cancelled()
            emit_status = _ThrottledProgressEmitter(self.status_progress.emit)
            emit_status(
                OperationProgress(
                    stage=1,
                    total_stages=total_stages,
                    message="XServerへSSH接続中",
                )
            )
            client, sftp = _connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )

            def report_recovery(current: int, total: int | None, detail: str) -> None:
                emit_status(
                    OperationProgress(
                        stage=2,
                        total_stages=total_stages,
                        message="残存一時ファイルを確認中",
                        current=current,
                        total=total,
                        unit="directories" if total is None else "files",
                        detail=detail,
                    )
                )

            recovery = scan_recovery_artifacts(
                sftp,
                self.mapping.remote_root,
                protect_patterns=_protect_patterns(self.project, self.mapping),
                progress_callback=report_recovery,
                cancellation_token=self.cancellation_token,
            )
            if recovery.temporary_files or recovery.issues:
                raise RuntimeError(
                    "残存一時ファイルまたは未確認領域があります。先に安全性確認を実行してください。"
                )

            def report_revalidation(event: DryRunProgress) -> None:
                emit_status(
                    OperationProgress(
                        stage=3,
                        total_stages=total_stages,
                        message=f"計画再検証: {event.message}",
                        current=event.current,
                        total=event.total,
                        unit=event.unit,
                        detail=event.detail,
                        phase_key=(
                            "deploy_revalidate",
                            event.stage,
                            event.message,
                            event.unit,
                            event.total,
                        ),
                        progress_label=f"計画再検証: {event.message}",
                        remote_wait=event.stage == 3,
                    )
                )

            revalidation = PlanRevalidator().revalidate(
                stored=self.stored,
                project=self.project,
                mapping=self.mapping,
                sftp=sftp,
                allow_resume=self.resume,
                progress_callback=report_revalidation,
                cancellation_token=self.cancellation_token,
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
                emit_status(
                    OperationProgress(
                        stage=5,
                        total_stages=total_stages,
                        message="HTTPS動作確認を実行中",
                        detail=f"https://{self.project.domain}{self.health_check_path}",
                    )
                )
                result = replace(
                    result,
                    health_check=_perform_https_health_check(
                        domain=self.project.domain,
                        path=self.health_check_path,
                    ),
                )
            self.completed.emit(result)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            _close_remote(client, sftp)


class ForceOverwriteWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    status_progress = Signal(object)
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
            self.cancellation_token.raise_if_cancelled()
            emit_status = _ThrottledProgressEmitter(self.status_progress.emit)
            emit_status(
                OperationProgress(
                    stage=1,
                    total_stages=2,
                    message="XServerへSSH接続中",
                    summary_prefix="無条件上書き中…",
                )
            )
            client, sftp = _connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )

            def report_started(current: int, total: int, path: str) -> None:
                emit_status(
                    OperationProgress(
                        stage=2,
                        total_stages=2,
                        message=f"転送中: {path}",
                        current=current,
                        total=total,
                        unit="files",
                        phase_key=("force_overwrite", total),
                        summary_prefix="無条件上書き中…",
                    )
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
                file_started_callback=report_started,
                cancellation_token=self.cancellation_token,
            )
            self.completed.emit(result)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            _close_remote(client, sftp)


class RecoveryScanWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

    def __init__(self, project: Project, mapping: SyncMapping, passphrase: str | None) -> None:
        super().__init__()
        self.project = project
        self.mapping = mapping
        self.passphrase = passphrase
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            self.cancellation_token.raise_if_cancelled()
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)
            emit_progress(
                OperationProgress(
                    stage=1,
                    total_stages=2,
                    message="XServerへSSH接続中",
                    summary_prefix="安全性確認を実行中…",
                )
            )
            client, sftp = _connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )

            def report(current: int, total: int | None, detail: str) -> None:
                emit_progress(
                    OperationProgress(
                        stage=2,
                        total_stages=2,
                        message=(
                            "XServerのフォルダを確認中"
                            if total is None
                            else "残存一時ファイルを照合中"
                        ),
                        current=current,
                        total=total,
                        unit="directories" if total is None else "files",
                        detail=detail,
                        summary_prefix="安全性確認を実行中…",
                    )
                )

            self.completed.emit(
                analyze_recovery_artifacts(
                    sftp,
                    self.mapping.remote_root,
                    protect_patterns=_protect_patterns(self.project, self.mapping),
                    progress_callback=report,
                    cancellation_token=self.cancellation_token,
                )
            )
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            _close_remote(client, sftp)


class RestoreWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

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
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        try:
            self.cancellation_token.raise_if_cancelled()
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)
            emit_progress(
                OperationProgress(
                    stage=1,
                    total_stages=3,
                    message="XServerへSSH接続中",
                    summary_prefix="バックアップから復元中…",
                )
            )
            client, sftp = _connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )

            def report_recovery(current: int, total: int | None, detail: str) -> None:
                emit_progress(
                    OperationProgress(
                        stage=2,
                        total_stages=3,
                        message="残存一時ファイルを確認中",
                        current=current,
                        total=total,
                        unit="directories" if total is None else "files",
                        detail=detail,
                        summary_prefix="バックアップから復元中…",
                    )
                )

            recovery = scan_recovery_artifacts(
                sftp,
                self.mapping.remote_root,
                protect_patterns=_protect_patterns(self.project, self.mapping),
                progress_callback=report_recovery,
                cancellation_token=self.cancellation_token,
            )
            if recovery.temporary_files or recovery.issues:
                raise RuntimeError(
                    "残存一時ファイルまたは未確認領域があります。先に安全性確認を実行してください。"
                )

            def report_restore(current: int, total: int, detail: str) -> None:
                phase_kind = detail.split(":", 1)[0].strip() if ":" in detail else detail
                emit_progress(
                    OperationProgress(
                        stage=3,
                        total_stages=3,
                        message=detail,
                        current=current,
                        total=total,
                        unit="files",
                        phase_key=("restore", phase_kind, total),
                        summary_prefix="バックアップから復元中…",
                    )
                )

            result = RemoteRestoreEngine().restore(
                sftp=sftp,
                manifest_path=self.manifest_path,
                destination_root=self.mapping.remote_root,
                expected_current_hashes=self.expected_current_hashes,
                authorization=self.authorization,
                protect_patterns=_protect_patterns(self.project, self.mapping),
                progress_callback=report_restore,
                cancellation_token=self.cancellation_token,
            )
            self.completed.emit(result)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            _close_remote(client, sftp)


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
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)
            emit_progress(
                WordPressSetupProgress(
                    step=1,
                    total_steps=WORDPRESS_SETUP_TOTAL_STEPS,
                    message="XServerへSSH接続中",
                )
            )
            client, sftp = _connect_sftp(
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
                progress_callback=emit_progress,
                checkpoint_callback=self.checkpoint.emit,
                cancellation_token=self.cancellation_token,
            )
            emit_progress(
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
            homepage = _perform_https_health_check(
                domain=health_domain, path=homepage_path
            )
            emit_progress(
                WordPressSetupProgress(
                    step=10,
                    total_steps=WORDPRESS_SETUP_TOTAL_STEPS,
                    message="HTTPSでログイン画面を確認中",
                )
            )
            login = _perform_https_health_check(
                domain=health_domain, path=login_path
            )
            self.completed.emit((result, homepage, login))
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            self.api_key = ""
            self.request = None
            _close_remote(client, sftp)


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
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)
            emit_progress(
                LaravelSetupProgress(
                    step=1,
                    total_steps=LARAVEL_SETUP_TOTAL_STEPS,
                    message="XServerへSSH接続中",
                )
            )
            client, sftp = _connect_sftp(
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
                progress_callback=emit_progress,
                checkpoint_callback=self.checkpoint.emit,
                cancellation_token=self.cancellation_token,
            )
            emit_progress(
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
            homepage = _perform_https_health_check(
                domain=health_domain, path=homepage_path
            )
            self.completed.emit((result, homepage))
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            self.api_key = ""
            self.request = None
            _close_remote(client, sftp)


class UpdateCheckWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, current_version: str = __version__) -> None:
        super().__init__()
        self.current_version = current_version

    @Slot()
    def run(self) -> None:
        try:
            result = check_for_updates(self.current_version)
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class BackupDeleteWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    cancelled = Signal(str)

    def __init__(
        self,
        *,
        project: Project,
        backups: Sequence[BackupRecord],
        passphrase: str | None,
    ) -> None:
        super().__init__()
        self.project = project
        self.backups = tuple(backups)
        self.passphrase = passphrase
        self.cancellation_token = CancellationToken()

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    @Slot()
    def run(self) -> None:
        client = None
        sftp = None
        deleted_ids: list[str] = []
        missing_ids: list[str] = []
        try:
            emit_progress = _ThrottledProgressEmitter(self.progress.emit)
            emit_progress(
                OperationProgress(
                    stage=1,
                    total_stages=2,
                    message="XServerへSSH接続中",
                    summary_prefix="古いバックアップを削除中…",
                )
            )
            client, sftp = _connect_sftp(
                host=self.project.ssh_host,
                port=self.project.ssh_port,
                username=self.project.ssh_username,
                private_key_path=self.project.private_key_reference,
                host_key_fingerprint=self.project.host_key_fingerprint,
                passphrase=self.passphrase,
            )
            total = len(self.backups)
            for index, backup in enumerate(self.backups, start=1):
                self.cancellation_token.raise_if_cancelled()
                emit_progress(
                    OperationProgress(
                        stage=2,
                        total_stages=2,
                        message=backup.backup_id,
                        current=index - 1,
                        total=total,
                        unit="件",
                        phase_key=("backup_delete", total),
                        summary_prefix="古いバックアップを削除中…",
                    )
                )
                removed = delete_remote_backup(
                    sftp,
                    backup.backup_directory,
                    cancellation_token=self.cancellation_token,
                )
                deleted_ids.append(backup.backup_id)
                if not removed:
                    missing_ids.append(backup.backup_id)
                emit_progress(
                    OperationProgress(
                        stage=2,
                        total_stages=2,
                        message=backup.backup_id,
                        current=index,
                        total=total,
                        unit="件",
                        phase_key=("backup_delete", total),
                        summary_prefix="古いバックアップを削除中…",
                    )
                )
            self.completed.emit((tuple(deleted_ids), tuple(missing_ids)))
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(format_worker_error(exc))
        finally:
            _close_remote(client, sftp)
