from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .auto_sync_policy import AutoSyncPreferences, auto_sync_policy_issues
from .backup import (
    RemoteBackupEngine,
    delete_remote_backup,
    select_prune_candidates,
)
from .cancellation import CancellationToken
from .deployment import (
    DeploymentAuthorization,
    DeployError,
    RemoteDeploymentEngine,
)
from .health import normalize_health_check_path, perform_https_health_check
from .integrity import project_config_sha256
from .models import ChangeType, Project, SyncMapping
from .planner import DEFAULT_PLAN_VALIDITY_MINUTES, validate_plan_validity
from .presets import get_preset
from .recovery import is_dxsync_temporary_name, scan_recovery_artifacts
from .remote import connect_sftp
from .restore import RemoteRestoreEngine, RestoreAuthorization
from .revalidation import PlanRevalidator
from .secure_store import SecureStoreError
from .service import DryRunProgress, DryRunService
from .storage import BackupRecord, ProjectStore, get_default_store
from .validation import custom_runtime_paths_issue


class OperationError(RuntimeError):
    pass


def _protect_patterns(project: Project, mapping: SyncMapping) -> tuple[str, ...]:
    return get_preset(project.site_type).protect_patterns + mapping.protect_patterns


def _resolve_passphrase(
    store: ProjectStore, project: Project, passphrase: str | None
) -> str | None:
    if passphrase:
        return passphrase
    try:
        saved = store.get_ssh_passphrase(project.project_id)
        return saved or None
    except SecureStoreError:
        return None


def find_project(store: ProjectStore, identifier: str) -> Project | None:
    store.initialize()
    # 1. Match by exact project_id
    proj = store.get(identifier)
    if proj:
        return proj
    # 2. Match by exact name
    all_projects = store.list_projects()
    for p in all_projects:
        if p.name == identifier:
            return p
    # 3. Match case-insensitively by name or domain
    lower = identifier.lower().strip()
    for p in all_projects:
        if p.name.lower() == lower or p.domain.lower() == lower:
            return p
    return None


def select_mapping(project: Project, mapping_name: str | None = None) -> SyncMapping:
    if not project.mappings:
        raise OperationError(f"Project '{project.name}' has no sync mappings configured.")
    if mapping_name:
        for m in project.mappings:
            if m.name == mapping_name or m.mapping_id == mapping_name:
                return m
        lower = mapping_name.lower()
        for m in project.mappings:
            if m.name.lower() == lower:
                return m
        raise OperationError(
            f"Mapping '{mapping_name}' not found in project '{project.name}'. "
            f"Available mappings: {[m.name for m in project.mappings]}"
        )
    return project.mappings[0]


def get_backup_root(project: Project) -> str:
    return f"/home/{project.ssh_username}/.dxsync/backups"


def list_projects_summary(store: ProjectStore) -> list[dict[str, Any]]:
    store.initialize()
    result = []
    for p in store.list_projects():
        sec_status = store.security_status(p.project_id)
        result.append(
            {
                "project_id": p.project_id,
                "name": p.name,
                "domain": p.domain,
                "site_type": p.site_type.value,
                "ssh_host": p.ssh_host,
                "ssh_port": p.ssh_port,
                "ssh_username": p.ssh_username,
                "mappings_count": len(p.mappings),
                "security_status": sec_status.state,
                "updated_at": p.updated_at.isoformat(),
            }
        )
    return result


def get_project_detail(store: ProjectStore, project: Project) -> dict[str, Any]:
    store.initialize()
    sec_status = store.security_status(project.project_id)
    recent_ops = store.list_operations(project.project_id, limit=5)
    return {
        "project_id": project.project_id,
        "name": project.name,
        "domain": project.domain,
        "site_type": project.site_type.value,
        "ssh_host": project.ssh_host,
        "ssh_port": project.ssh_port,
        "ssh_username": project.ssh_username,
        "private_key_path": project.private_key_reference,
        "has_fingerprint": bool(project.host_key_fingerprint),
        "security_status": {
            "state": sec_status.state,
            "backend": sec_status.backend,
            "message": sec_status.message,
        },
        "mappings": [
            {
                "mapping_id": m.mapping_id,
                "name": m.name,
                "local_root": m.local_root,
                "remote_root": m.remote_root,
                "exclude_patterns": list(m.exclude_patterns),
                "protect_patterns": list(m.protect_patterns),
                "runtime_paths_confirmed": m.runtime_paths_confirmed,
            }
            for m in project.mappings
        ],
        "recent_operations": [
            {
                "operation_id": op.operation_id,
                "kind": op.kind,
                "status": op.status,
                "started_at": op.started_at.isoformat(),
                "completed_at": op.completed_at.isoformat() if op.completed_at else None,
                "error": op.error,
            }
            for op in recent_ops
        ],
    }


def _ensure_project_operational(
    store: ProjectStore,
    project: Project,
    mapping: SyncMapping,
    *,
    require_runtime_paths: bool = True,
) -> None:
    store.initialize()
    security = store.security_status(project.project_id)
    if security.state != "secure":
        raise OperationError(
            f"接続先の確認コードがOS資格情報ストアで保護されていないため実行できません: {security.message}"
        )
    if require_runtime_paths:
        if runtime_issue := custom_runtime_paths_issue(project, mapping):
            raise OperationError(runtime_issue.message)


def execute_dry_run(
    store: ProjectStore,
    project: Project,
    mapping_name: str | None = None,
    staging_parent: Path | None = None,
    passphrase: str | None = None,
    plan_validity_minutes: int = DEFAULT_PLAN_VALIDITY_MINUTES,
    progress_callback: Callable[[DryRunProgress], None] | None = None,
) -> dict[str, Any]:
    plan_validity = validate_plan_validity(timedelta(minutes=plan_validity_minutes))
    mapping = select_mapping(project, mapping_name)
    _ensure_project_operational(store, project, mapping, require_runtime_paths=True)
    config_sha256 = project_config_sha256(project, mapping)
    staging_dir = staging_parent or (store.database_path.parent / "staging")
    op = store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        kind="dry_run",
        details={"mode": "read_only", "plan_validity_minutes": plan_validity_minutes},
    )
    client, sftp = connect_sftp(
        host=project.ssh_host,
        port=project.ssh_port,
        username=project.ssh_username,
        private_key_path=project.private_key_reference,
        host_key_fingerprint=project.host_key_fingerprint,
        passphrase=_resolve_passphrase(store, project, passphrase),
    )
    try:
        service = DryRunService()
        result = service.execute(
            site_type=project.site_type,
            mapping_id=mapping.mapping_id,
            local_root=Path(mapping.local_root),
            remote_root=mapping.remote_root,
            sftp=sftp,
            extra_excludes=mapping.exclude_patterns,
            extra_protected=mapping.protect_patterns,
            staging_parent=staging_dir,
            config_sha256=config_sha256,
            plan_validity=plan_validity,
            progress_callback=progress_callback,
        )
        staging_root = str(result.staging.root) if result.staging else None
        store.save_plan(
            project_id=project.project_id,
            plan=result.plan,
            staging_path=staging_root,
        )
        store.finish_operation(
            op.operation_id,
            status="failed" if result.has_blocking_issues else "succeeded",
            details={"plan_id": result.plan.plan_id},
        )
        counts = {kind.value: count for kind, count in result.plan.counts().items()}
        return {
            "status": "warning" if result.has_blocking_issues else "success",
            "project": project.name,
            "mapping": mapping.name,
            "plan_id": result.plan.plan_id,
            "plan_validity_minutes": plan_validity_minutes,
            "expires_at": result.plan.expires_at.isoformat(),
            "counts": counts,
            "has_blocking_issues": result.has_blocking_issues,
            "changes": [
                {
                    "relative_path": c.relative_path,
                    "change_type": c.change_type.value,
                    "reason": c.reason,
                    "local_size": c.local.size if c.local else None,
                    "remote_size": c.remote.size if c.remote else None,
                }
                for c in result.plan.changes
            ],
            "issues": {
                "local": [asdict(i) for i in result.local.issues],
                "remote": [asdict(i) for i in result.remote.issues],
            },
            "protected_skipped": {
                "local": list(result.local.skipped_protected_paths),
                "remote": list(result.remote.skipped_protected_paths),
            },
        }
    except Exception as exc:
        store.finish_operation(op.operation_id, status="failed", error=str(exc))
        raise
    finally:
        sftp.close()
        client.close()


def execute_auto_sync(
    store: ProjectStore,
    project: Project,
    mapping_name: str | None = None,
    staging_parent: Path | None = None,
    passphrase: str | None = None,
    *,
    backup_mode: str = "always",
    remote_only_policy: str = "keep",
    delete_paths: Sequence[str] = (),
    health_check_enabled: bool = True,
    health_check_path: str = "/",
    plan_validity_minutes: int = DEFAULT_PLAN_VALIDITY_MINUTES,
    max_changed_files: int = 1000,
    max_update_files: int = 500,
    max_upload_mib: int = 1024,
) -> dict[str, Any]:
    if backup_mode not in {"always", "add_only", "skip"}:
        raise OperationError(
            f"Unsupported backup_mode '{backup_mode}'. Must be one of: always, add_only, skip."
        )
    if remote_only_policy not in {"keep", "stop", "ask"}:
        raise OperationError(
            f"Unsupported remote_only_policy '{remote_only_policy}'. Must be one of: keep, stop, ask."
        )
    if max_changed_files < 0 or max_update_files < 0 or max_upload_mib < 0:
        raise OperationError("Auto-sync threshold limits must be zero or positive.")

    plan_validity = validate_plan_validity(timedelta(minutes=plan_validity_minutes))
    normalized_health_path = (
        normalize_health_check_path(health_check_path) if health_check_enabled else "/"
    )
    requested_delete_paths = tuple(dict.fromkeys(delete_paths))
    if requested_delete_paths and backup_mode == "skip":
        raise OperationError("削除を含む同期ではバックアップを省略できません。")
    if requested_delete_paths and remote_only_policy != "ask":
        raise OperationError(
            "リモートのみファイルを削除する場合は remote_only_policy='ask' を指定してください。"
        )

    preferences = AutoSyncPreferences(
        backup_mode=backup_mode,
        remote_only_policy=remote_only_policy,
        health_check_enabled=health_check_enabled,
        health_check_path=normalized_health_path,
        max_changed_files=max_changed_files,
        max_update_files=max_update_files,
        max_upload_mib=max_upload_mib,
    )

    mapping = select_mapping(project, mapping_name)
    _ensure_project_operational(store, project, mapping, require_runtime_paths=True)
    config_sha256 = project_config_sha256(project, mapping)
    staging_dir = staging_parent or (store.database_path.parent / "staging")
    backup_root = get_backup_root(project)

    op = store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        kind="auto_sync",
        details={
            "mode": "write",
            "backup_mode": backup_mode,
            "remote_only_policy": remote_only_policy,
            "plan_validity_minutes": plan_validity_minutes,
        },
    )
    client, sftp = connect_sftp(
        host=project.ssh_host,
        port=project.ssh_port,
        username=project.ssh_username,
        private_key_path=project.private_key_reference,
        host_key_fingerprint=project.host_key_fingerprint,
        passphrase=_resolve_passphrase(store, project, passphrase),
    )
    try:
        # Phase 1: Dry run & Staging
        service = DryRunService()
        dry_result = service.execute(
            site_type=project.site_type,
            mapping_id=mapping.mapping_id,
            local_root=Path(mapping.local_root),
            remote_root=mapping.remote_root,
            sftp=sftp,
            extra_excludes=mapping.exclude_patterns,
            extra_protected=mapping.protect_patterns,
            staging_parent=staging_dir,
            config_sha256=config_sha256,
            plan_validity=plan_validity,
        )
        if dry_result.has_blocking_issues:
            raise OperationError("Dry-run detected blocking issues. Auto-sync cannot proceed.")

        plan = dry_result.plan
        add_count = sum(1 for c in plan.changes if c.change_type is ChangeType.ADD)
        update_count = sum(1 for c in plan.changes if c.change_type is ChangeType.UPDATE)
        remote_only_changes = [
            c for c in plan.changes if c.change_type is ChangeType.REMOTE_ONLY
        ]
        remote_only_paths = {c.relative_path for c in remote_only_changes}
        upload_bytes = sum(
            c.local.size
            for c in plan.changes
            if c.change_type in {ChangeType.ADD, ChangeType.UPDATE} and c.local is not None
        )

        policy_issues = auto_sync_policy_issues(
            preferences,
            add_count=add_count,
            update_count=update_count,
            remote_only_count=len(remote_only_changes),
            upload_bytes=upload_bytes,
        )
        if policy_issues:
            raise OperationError("; ".join(policy_issues))

        unknown_deletes = [
            path for path in requested_delete_paths if path not in remote_only_paths
        ]
        if unknown_deletes:
            raise OperationError(
                "削除対象パスが REMOTE_ONLY 差分に見つかりません: "
                + ", ".join(unknown_deletes)
            )

        upload_changes = [
            c for c in plan.changes if c.change_type in {ChangeType.ADD, ChangeType.UPDATE}
        ]
        if not upload_changes and not requested_delete_paths:
            store.finish_operation(
                op.operation_id,
                status="succeeded",
                details={"transferred_files": 0, "message": "No changes"},
            )
            return {
                "status": "no_changes",
                "project": project.name,
                "mapping": mapping.name,
                "message": "同期が必要なファイル差分はありません。本番環境は最新です。",
                "counts": {kind.value: count for kind, count in plan.counts().items()},
                "remote_only_paths": [c.relative_path for c in remote_only_changes],
            }

        if not dry_result.staging:
            raise OperationError("Staging files could not be prepared for deployment.")

        store.save_plan(
            project_id=project.project_id,
            plan=plan,
            staging_path=str(dry_result.staging.root),
        )
        stored_plan = store.get_plan(plan.plan_id)
        if not stored_plan:
            raise OperationError("Could not retrieve staged plan from storage.")

        # Phase 2: Revalidate
        revalidator = PlanRevalidator()
        reval_result = revalidator.revalidate(
            stored=stored_plan,
            project=project,
            mapping=mapping,
            sftp=sftp,
        )
        if not reval_result.valid:
            store.set_plan_status(plan.plan_id, "invalid")
            reasons = "; ".join(reval_result.reasons)
            raise OperationError(f"Plan revalidation failed: {reasons}")

        # Phase 3: Backup
        backup_skipped = preferences.resolved_backup_skip(
            update_count, len(requested_delete_paths)
        )
        backup_id: str | None = None
        backup_files = 0
        if not backup_skipped:
            backup_engine = RemoteBackupEngine()
            backup_result = backup_engine.create_backup(
                sftp=sftp,
                plan=plan,
                project_id=project.project_id,
                remote_root=mapping.remote_root,
                backup_root=backup_root,
                delete_paths=requested_delete_paths,
            )
            backup_record = BackupRecord(
                backup_id=backup_result.backup_id,
                project_id=project.project_id,
                mapping_id=mapping.mapping_id,
                plan_id=plan.plan_id,
                backup_directory=backup_result.backup_directory,
                manifest_path=backup_result.manifest_path,
                created_at=backup_result.created_at,
                file_count=len(backup_result.files),
                total_bytes=backup_result.total_bytes,
            )
            store.save_backup(backup_record)
            backup_id = backup_record.backup_id
            backup_files = backup_record.file_count

        # Phase 4: Deploy
        auth = DeploymentAuthorization(
            plan_id=plan.plan_id,
            backup_plan_id=backup_id,
            remote_root=mapping.remote_root,
            approved=True,
            backup_skipped=backup_skipped,
            delete_paths=requested_delete_paths,
        )
        deploy_engine = RemoteDeploymentEngine()
        deploy_result = deploy_engine.deploy(
            sftp=sftp,
            plan=plan,
            staging_root=dry_result.staging.root,
            remote_root=mapping.remote_root,
            authorization=auth,
            file_mode=mapping.file_mode,
            directory_mode=mapping.directory_mode,
        )
        store.set_plan_status(plan.plan_id, "used")

        # Phase 5: HTTPS Health Check
        if health_check_enabled:
            health = perform_https_health_check(
                domain=project.domain, path=normalized_health_path
            )
            health_error = (
                None
                if health.ok
                else (health.error or f"HTTPS check returned {health.status_code}")
            )
            health_summary = (
                f"HTTPS {health.status_code}"
                if health.status_code is not None
                else f"HTTPSエラー ({health.error or '不明'})"
            )
            health_payload = {
                "ok": health.ok,
                "healthy": health.ok,
                "status_code": health.status_code,
                "url": health.url,
                "error": health.error,
                "skipped": False,
            }
            overall_status = "success" if health.ok else "warning"
        else:
            health_error = None
            health_summary = "HTTPS確認スキップ"
            health_payload = {
                "ok": True,
                "healthy": True,
                "status_code": None,
                "url": f"https://{project.domain}{normalized_health_path}",
                "error": None,
                "skipped": True,
            }
            overall_status = "success"

        backup_summary = (
            f"バックアップ: {backup_id}"
            if backup_id is not None
            else "バックアップ: スキップ"
        )
        store.finish_operation(
            op.operation_id,
            status="succeeded",
            details={
                "plan_id": plan.plan_id,
                "backup_id": backup_id,
                "backup_skipped": backup_skipped,
                "transferred_files": len(deploy_result.files),
                "deleted_files": len(deploy_result.deleted_files),
                "total_bytes": deploy_result.total_bytes,
                "health_status": health_payload["status_code"],
                "health_check": health_payload,
            },
            error=health_error,
        )
        return {
            "status": overall_status,
            "project": project.name,
            "mapping": mapping.name,
            "plan_id": plan.plan_id,
            "transferred_files": len(deploy_result.files),
            "deleted_files": list(deploy_result.deleted_files),
            "deleted_count": len(deploy_result.deleted_files),
            "total_bytes": deploy_result.total_bytes,
            "backup_id": backup_id,
            "backup_files": backup_files,
            "backup_skipped": backup_skipped,
            "health_check": health_payload,
            "summary": (
                f"{len(deploy_result.files)}件のファイルを同期しました"
                + (
                    f"（{len(deploy_result.deleted_files)}件削除）"
                    if deploy_result.deleted_files
                    else ""
                )
                + f" / {backup_summary} / {health_summary}"
            ),
        }
    except Exception as exc:
        store.finish_operation(op.operation_id, status="failed", error=str(exc))
        raise
    finally:
        sftp.close()
        client.close()


def execute_verify(
    store: ProjectStore,
    project: Project,
    mapping_name: str | None = None,
    cleanup: bool = False,
    passphrase: str | None = None,
) -> dict[str, Any]:
    mapping = select_mapping(project, mapping_name)
    _ensure_project_operational(store, project, mapping, require_runtime_paths=False)
    client, sftp = connect_sftp(
        host=project.ssh_host,
        port=project.ssh_port,
        username=project.ssh_username,
        private_key_path=project.private_key_reference,
        host_key_fingerprint=project.host_key_fingerprint,
        passphrase=_resolve_passphrase(store, project, passphrase),
    )
    try:
        scan_res = scan_recovery_artifacts(
            sftp,
            mapping.remote_root,
            protect_patterns=_protect_patterns(project, mapping),
        )
        issues = list(scan_res.issues)
        cleaned_up = []
        if cleanup and scan_res.temporary_files:
            for rel in scan_res.temporary_files:
                abs_path = f"{mapping.remote_root.rstrip('/')}/{rel.lstrip('/')}"
                try:
                    sftp.remove(abs_path)
                    cleaned_up.append(rel)
                except Exception as exc:
                    issues.append(f"Failed to remove {rel}: {exc}")
        return {
            "status": "success",
            "project": project.name,
            "mapping": mapping.name,
            "remote_root": scan_res.remote_root,
            "temporary_files_count": len(scan_res.temporary_files),
            "temporary_files": list(scan_res.temporary_files),
            "cleaned_up_count": len(cleaned_up),
            "cleaned_up": cleaned_up,
            "issues": issues,
        }
    finally:
        sftp.close()
        client.close()


def list_backups_summary(store: ProjectStore, project: Project) -> list[dict[str, Any]]:
    store.initialize()
    backups = store.list_backups(project.project_id)
    return [
        {
            "backup_id": b.backup_id,
            "plan_id": b.plan_id,
            "mapping_id": b.mapping_id,
            "created_at": b.created_at.isoformat(),
            "file_count": b.file_count,
            "total_bytes": b.total_bytes,
            "status": b.status,
            "backup_directory": b.backup_directory,
        }
        for b in backups
    ]


def execute_restore(
    store: ProjectStore,
    project: Project,
    backup_id: str,
    mapping_name: str | None = None,
    passphrase: str | None = None,
) -> dict[str, Any]:
    store.initialize()
    backups = {b.backup_id: b for b in store.list_backups(project.project_id)}
    backup = backups.get(backup_id)
    if not backup:
        raise OperationError(f"Backup '{backup_id}' not found for project '{project.name}'.")

    backup_mapping = next(
        (m for m in project.mappings if m.mapping_id == backup.mapping_id),
        None,
    )
    if mapping_name is not None:
        selected_mapping = select_mapping(project, mapping_name)
        if selected_mapping.mapping_id != backup.mapping_id:
            raise OperationError(
                f"Backup '{backup_id}' belongs to mapping '{backup.mapping_id}', "
                f"not '{selected_mapping.name}'."
            )
    if backup_mapping is None:
        raise OperationError(
            f"Mapping '{backup.mapping_id}' for backup '{backup_id}' "
            f"was not found in project '{project.name}'."
        )
    mapping = backup_mapping
    _ensure_project_operational(store, project, mapping, require_runtime_paths=True)

    if backup.status != "available":
        raise OperationError(
            f"Backup '{backup_id}' is not available for restore (status: {backup.status})."
        )
    if backup.file_count <= 0:
        raise OperationError(f"Backup '{backup_id}' contains no restorable files.")
    stored = store.get_plan(backup.plan_id)
    if stored is None:
        raise OperationError(
            f"Stored plan '{backup.plan_id}' required for restore was not found."
        )
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
        raise OperationError("同期完了時のハッシュ記録が不足しています。")

    op = store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        kind="restore",
        details={"mode": "write", "backup_id": backup_id},
    )
    client, sftp = connect_sftp(
        host=project.ssh_host,
        port=project.ssh_port,
        username=project.ssh_username,
        private_key_path=project.private_key_reference,
        host_key_fingerprint=project.host_key_fingerprint,
        passphrase=_resolve_passphrase(store, project, passphrase),
    )
    try:
        # Pre-scan for temporary files
        recovery = scan_recovery_artifacts(
            sftp,
            mapping.remote_root,
            protect_patterns=_protect_patterns(project, mapping),
        )
        if recovery.temporary_files or recovery.issues:
            raise OperationError("残存一時ファイルがあります。先に安全性確認を実行してください。")

        auth = RestoreAuthorization(
            backup_id=backup_id,
            destination_root=mapping.remote_root,
            approved=True,
        )
        restore_engine = RemoteRestoreEngine()
        result = restore_engine.restore(
            sftp=sftp,
            manifest_path=backup.manifest_path,
            destination_root=mapping.remote_root,
            expected_current_hashes=expected_hashes,
            authorization=auth,
            protect_patterns=_protect_patterns(project, mapping),
        )
        store.set_backup_status(backup_id, "restored")
        store.finish_operation(
            op.operation_id,
            status="succeeded",
            details={
                "backup_id": backup_id,
                "restored_files": len(result.files),
                "total_bytes": result.total_bytes,
            },
        )
        return {
            "status": "success",
            "project": project.name,
            "mapping": mapping.name,
            "backup_id": backup_id,
            "restored_files_count": len(result.files),
            "total_bytes": result.total_bytes,
            "summary": f"バックアップ {backup_id} から {len(result.files)}件のファイルを正常に復元しました。",
        }
    except Exception as exc:
        store.finish_operation(op.operation_id, status="failed", error=str(exc))
        raise
    finally:
        sftp.close()
        client.close()


def execute_prune_backups(
    store: ProjectStore,
    project: Project,
    *,
    keep_latest: int = 10,
    max_age_days: int = 30,
    dry_run: bool = False,
    passphrase: str | None = None,
) -> dict[str, Any]:
    store.initialize()
    backups = store.list_backups(project.project_id)
    candidates = select_prune_candidates(
        backups,
        keep_latest=keep_latest,
        max_age_days=max_age_days,
    )
    candidate_payload = [
        {
            "backup_id": item.backup_id,
            "created_at": item.created_at.isoformat(),
            "file_count": item.file_count,
            "total_bytes": item.total_bytes,
            "status": item.status,
            "backup_directory": item.backup_directory,
        }
        for item in candidates
    ]
    if dry_run or not candidates:
        return {
            "status": "dry_run" if dry_run else "no_changes",
            "project": project.name,
            "keep_latest": keep_latest,
            "max_age_days": max_age_days,
            "candidate_count": len(candidates),
            "deleted_count": 0,
            "candidates": candidate_payload,
            "deleted": [],
            "missing_remote": [],
        }

    security = store.security_status(project.project_id)
    if security.state != "secure":
        raise OperationError(
            f"接続先の確認コードがOS資格情報ストアで保護されていないため実行できません: {security.message}"
        )

    mapping_id = project.mappings[0].mapping_id if project.mappings else "backup-prune"
    op = store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping_id,
        kind="backup_delete",
        details={
            "mode": "write",
            "keep_latest": keep_latest,
            "max_age_days": max_age_days,
            "candidate_count": len(candidates),
        },
    )
    client, sftp = connect_sftp(
        host=project.ssh_host,
        port=project.ssh_port,
        username=project.ssh_username,
        private_key_path=project.private_key_reference,
        host_key_fingerprint=project.host_key_fingerprint,
        passphrase=_resolve_passphrase(store, project, passphrase),
    )
    deleted: list[str] = []
    missing_remote: list[str] = []
    try:
        for candidate in candidates:
            removed = delete_remote_backup(sftp, candidate.backup_directory)
            store.set_backup_status(candidate.backup_id, "deleted")
            deleted.append(candidate.backup_id)
            if not removed:
                missing_remote.append(candidate.backup_id)
        store.finish_operation(
            op.operation_id,
            status="succeeded",
            details={
                "deleted_count": len(deleted),
                "missing_remote_count": len(missing_remote),
            },
        )
        return {
            "status": "success",
            "project": project.name,
            "keep_latest": keep_latest,
            "max_age_days": max_age_days,
            "candidate_count": len(candidates),
            "deleted_count": len(deleted),
            "candidates": candidate_payload,
            "deleted": deleted,
            "missing_remote": missing_remote,
        }
    except Exception as exc:
        for deleted_id in deleted:
            store.set_backup_status(deleted_id, "deleted")
        store.finish_operation(op.operation_id, status="failed", error=str(exc))
        raise
    finally:
        sftp.close()
        client.close()

