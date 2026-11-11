from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cancellation import CancellationToken
from .models import ChangeType, ScanResult, SiteType, SyncPlan
from .planner import PlanBuilder
from .presets import get_preset
from .remote import RemoteFileSystem, RemoteScanResult, SftpScanner
from .scanner import LocalScanner
from .staging import StagingArea, StagingResult


@dataclass(frozen=True)
class DryRunResult:
    local: ScanResult
    remote: RemoteScanResult
    plan: SyncPlan
    staging: StagingResult | None = None

    @property
    def has_blocking_issues(self) -> bool:
        return (
            self.plan.has_blocking_changes
            or any(issue.fatal for issue in self.local.issues)
            or any(issue.fatal for issue in self.remote.issues)
        )


@dataclass(frozen=True)
class DryRunProgress:
    stage: int
    total_stages: int
    message: str
    current: int | None = None
    total: int | None = None
    unit: str = ""
    detail: str = ""


class DryRunService:
    def execute(
        self,
        *,
        site_type: SiteType,
        mapping_id: str,
        local_root: Path,
        remote_root: str,
        sftp: RemoteFileSystem,
        extra_excludes: tuple[str, ...] = (),
        extra_protected: tuple[str, ...] = (),
        staging_parent: Path | None = None,
        config_sha256: str = "",
        progress_callback: Callable[[DryRunProgress], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> DryRunResult:
        progress = progress_callback or (lambda _event: None)
        cancel = cancellation_token or CancellationToken()
        preset = get_preset(site_type)
        excludes = preset.exclude_patterns + extra_excludes
        protected = preset.protect_patterns + extra_protected
        local = LocalScanner(excludes, protected).scan(
            local_root,
            progress_callback=lambda current, total, detail: progress(
                DryRunProgress(
                    stage=2,
                    total_stages=4,
                    message=(
                        "ローカルファイルを列挙中"
                        if total is None
                        else "ローカルファイルを読み取り中"
                    ),
                    current=current,
                    total=total,
                    unit="files" if total is not None else "directories",
                    detail=detail,
                )
            ),
            cancellation_token=cancel,
        )
        remote = SftpScanner(excludes, protected).scan(
            sftp,
            remote_root,
            progress_callback=lambda current, total, detail: progress(
                DryRunProgress(
                    stage=3,
                    total_stages=4,
                    message=(
                        "XServerのフォルダを列挙中"
                        if total is None
                        else "XServerファイルを読み取り中"
                    ),
                    current=current,
                    total=total,
                    unit="files" if total is not None else "directories",
                    detail=detail,
                )
            ),
            cancellation_token=cancel,
        )
        cancel.raise_if_cancelled()
        progress(DryRunProgress(4, 4, "差分計画を作成中"))
        plan = PlanBuilder(protected).build(
            mapping_id=mapping_id,
            local=local,
            remote_records=remote.files,
            config_sha256=config_sha256,
        )
        staging = None
        has_blocking = (
            plan.has_blocking_changes
            or any(issue.fatal for issue in local.issues)
            or any(issue.fatal for issue in remote.issues)
        )
        if staging_parent is not None and not has_blocking:
            upload_paths = {
                change.relative_path
                for change in plan.changes
                if change.change_type in {ChangeType.ADD, ChangeType.UPDATE}
            }
            staging = StagingArea().create(
                local,
                staging_parent / plan.plan_id,
                include_paths=upload_paths,
                progress_callback=lambda current, total, detail: progress(
                    DryRunProgress(
                        stage=4,
                        total_stages=4,
                        message="承認用ファイルを準備中",
                        current=current,
                        total=total,
                        unit="files",
                        detail=detail,
                    )
                ),
                cancellation_token=cancel,
            )
        return DryRunResult(local=local, remote=remote, plan=plan, staging=staging)
