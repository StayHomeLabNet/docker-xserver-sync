from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .backup import RemoteBackupEngine
from .cancellation import CancellationToken
from .deployment import (
    DeploymentAuthorization,
    DeployError,
    RemoteDeploymentEngine,
)
from .health import perform_https_health_check
from .models import ChangeType, Project, SyncMapping
from .recovery import is_dxsync_temporary_name, scan_recovery_artifacts
from .remote import connect_sftp
from .restore import RemoteRestoreEngine, RestoreAuthorization
from .revalidation import PlanRevalidator
from .service import DryRunProgress, DryRunService
from .storage import BackupRecord, ProjectStore, get_default_store


class OperationError(RuntimeError):
    pass


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


def execute_dry_run(
    store: ProjectStore,
    project: Project,
    mapping_name: str | None = None,
    staging_parent: Path | None = None,
    passphrase: str | None = None,
    progress_callback: Callable[[DryRunProgress], None] | None = None,
) -> dict[str, Any]:
    mapping = select_mapping(project, mapping_name)
    staging_dir = staging_parent or (store.database_path.parent / "staging")
    op = store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        kind="dry_run",
        details={"mode": "read_only"},
    )
    client, sftp = connect_sftp(
        host=project.ssh_host,
        port=project.ssh_port,
        username=project.ssh_username,
        private_key_path=project.private_key_reference,
        host_key_fingerprint=project.host_key_fingerprint,
        passphrase=passphrase,
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
) -> dict[str, Any]:
    mapping = select_mapping(project, mapping_name)
    staging_dir = staging_parent or (store.database_path.parent / "staging")
    backup_root = get_backup_root(project)

    op = store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        kind="auto_sync",
        details={"mode": "write"},
    )
    client, sftp = connect_sftp(
        host=project.ssh_host,
        port=project.ssh_port,
        username=project.ssh_username,
        private_key_path=project.private_key_reference,
        host_key_fingerprint=project.host_key_fingerprint,
        passphrase=passphrase,
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
        )
        if dry_result.has_blocking_issues:
            raise OperationError("Dry-run detected blocking issues. Auto-sync cannot proceed.")

        plan = dry_result.plan
        upload_changes = [
            c for c in plan.changes if c.change_type in {ChangeType.ADD, ChangeType.UPDATE}
        ]
        if not upload_changes:
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
            reasons = "; ".join(reval_result.reasons)
            raise OperationError(f"Plan revalidation failed: {reasons}")

        # Phase 3: Backup
        backup_engine = RemoteBackupEngine()
        backup_result = backup_engine.create_backup(
            sftp=sftp,
            plan=plan,
            project_id=project.project_id,
            remote_root=mapping.remote_root,
            backup_root=backup_root,
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

        # Phase 4: Deploy
        auth = DeploymentAuthorization(
            plan_id=plan.plan_id,
            backup_plan_id=backup_result.backup_id,
            remote_root=mapping.remote_root,
            approved=True,
            backup_skipped=False,
            delete_paths=(),
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

        # Phase 5: HTTPS Health Check
        health = perform_https_health_check(domain=project.domain)

        store.finish_operation(
            op.operation_id,
            status="succeeded" if health.healthy else "warning",
            details={
                "backup_id": backup_record.backup_id,
                "transferred_files": len(deploy_result.files),
                "total_bytes": deploy_result.total_bytes,
                "health_status": health.status_code,
            },
            error=None if health.healthy else f"HTTPS check returned {health.status_code}",
        )
        return {
            "status": "success",
            "project": project.name,
            "mapping": mapping.name,
            "transferred_files": len(deploy_result.files),
            "total_bytes": deploy_result.total_bytes,
            "backup_id": backup_record.backup_id,
            "backup_files": backup_record.file_count,
            "health_check": {
                "healthy": health.healthy,
                "status_code": health.status_code,
                "url": health.url,
            },
            "summary": (
                f"{len(deploy_result.files)}件のファイルを同期しました / "
                f"バックアップ: {backup_record.backup_id} / HTTPS {health.status_code}"
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
    client, sftp = connect_sftp(
        host=project.ssh_host,
        port=project.ssh_port,
        username=project.ssh_username,
        private_key_path=project.private_key_reference,
        host_key_fingerprint=project.host_key_fingerprint,
        passphrase=passphrase,
    )
    try:
        scan_res = scan_recovery_artifacts(sftp, mapping.remote_root)
        cleaned_up = []
        if cleanup and scan_res.temporary_files:
            for rel in scan_res.temporary_files:
                abs_path = f"{mapping.remote_root.rstrip('/')}/{rel.lstrip('/')}"
                try:
                    sftp.remove(abs_path)
                    cleaned_up.append(rel)
                except Exception as exc:
                    scan_res.issues.append(f"Failed to remove {rel}: {exc}")
        return {
            "status": "success",
            "project": project.name,
            "mapping": mapping.name,
            "remote_root": scan_res.remote_root,
            "temporary_files_count": len(scan_res.temporary_files),
            "temporary_files": list(scan_res.temporary_files),
            "cleaned_up_count": len(cleaned_up),
            "cleaned_up": cleaned_up,
            "issues": list(scan_res.issues),
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
    mapping = select_mapping(project, mapping_name)
    store.initialize()
    backups = {b.backup_id: b for b in store.list_backups(project.project_id)}
    backup = backups.get(backup_id)
    if not backup:
        raise OperationError(f"Backup '{backup_id}' not found for project '{project.name}'.")

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
        passphrase=passphrase,
    )
    try:
        # Pre-scan for temporary files
        recovery = scan_recovery_artifacts(sftp, mapping.remote_root)
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
            expected_current_hashes={},
            authorization=auth,
        )
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
