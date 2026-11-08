from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .integrity import project_config_sha256
from .models import ChangeType, Project, SyncMapping
from .remote import RemoteFileSystem
from .service import DryRunResult, DryRunService
from .staging import StagingArea
from .storage import StoredPlan
from .validation import custom_runtime_paths_issue


@dataclass(frozen=True)
class RevalidationResult:
    valid: bool
    reasons: tuple[str, ...]
    current: DryRunResult | None = None


class PlanRevalidator:
    def revalidate(
        self,
        *,
        stored: StoredPlan,
        project: Project,
        mapping: SyncMapping,
        sftp: RemoteFileSystem,
        now: datetime | None = None,
        allow_resume: bool = False,
    ) -> RevalidationResult:
        current_time = now or datetime.now(timezone.utc)
        reasons: list[str] = []
        if runtime_issue := custom_runtime_paths_issue(project, mapping):
            reasons.append(runtime_issue.message)
        allowed_statuses = {"ready", "expired"} if allow_resume else {"ready"}
        if stored.status not in allowed_statuses:
            reasons.append(f"Plan status is {stored.status}, not ready")
        if stored.plan.expires_at <= current_time and not allow_resume:
            reasons.append("Plan has expired")
        expected_config = project_config_sha256(project, mapping)
        if not stored.plan.config_sha256 or stored.plan.config_sha256 != expected_config:
            reasons.append("Project or mapping configuration changed")
        if stored.plan.mapping_id != mapping.mapping_id:
            reasons.append("Plan belongs to a different mapping")

        expected_hashes = {
            change.relative_path: change.local.sha256
            for change in stored.plan.changes
            if change.change_type in {ChangeType.ADD, ChangeType.UPDATE}
            and change.local is not None
            and change.local.sha256 is not None
        }
        if not stored.staging_path:
            reasons.append("Plan has no staging directory")
        else:
            staging = StagingArea().verify(Path(stored.staging_path), expected_hashes)
            reasons.extend(staging.issues)

        if reasons:
            return RevalidationResult(False, tuple(reasons))

        current = DryRunService().execute(
            site_type=project.site_type,
            mapping_id=mapping.mapping_id,
            local_root=Path(mapping.local_root),
            remote_root=mapping.remote_root,
            sftp=sftp,
            extra_excludes=mapping.exclude_patterns,
            extra_protected=mapping.protect_patterns,
            config_sha256=expected_config,
        )
        if current.has_blocking_issues:
            reasons.append("Current scan contains blocking issues")
        if current.plan.local_snapshot_sha256 != stored.plan.local_snapshot_sha256:
            reasons.append("Local source changed after the plan was created")
        if current.plan.remote_snapshot_sha256 != stored.plan.remote_snapshot_sha256:
            reasons.append("Remote destination changed after the plan was created")
        if current.plan.config_sha256 != stored.plan.config_sha256:
            reasons.append("Configuration hash no longer matches")
        return RevalidationResult(not reasons, tuple(reasons), current)
