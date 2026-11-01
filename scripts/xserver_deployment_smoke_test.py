from __future__ import annotations

import argparse
from pathlib import Path

from dxsync.backup import RemoteBackupEngine
from dxsync.deployment import DeploymentAuthorization, RemoteDeploymentEngine
from dxsync.integrity import project_config_sha256
from dxsync.models import ChangeType
from dxsync.remote import connect_sftp
from dxsync.revalidation import PlanRevalidator
from dxsync.service import DryRunService
from dxsync.storage import BackupRecord, ProjectStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the approved backup and deployment flow against a saved test mapping."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--mapping-id", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Required acknowledgement that the selected remote test mapping may be written.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.apply:
        raise SystemExit("Refusing to write without --apply")

    store = ProjectStore(args.database.expanduser())
    project = store.get(args.project_id)
    if project is None:
        raise SystemExit("Saved project was not found")
    mapping = next(
        (item for item in project.mappings if item.mapping_id == args.mapping_id),
        None,
    )
    if mapping is None:
        raise SystemExit("Saved mapping was not found")
    if not mapping.remote_root.rstrip("/").endswith("/test"):
        raise SystemExit("Smoke test refuses a remote root that does not end in /test")

    client = None
    sftp = None
    try:
        client, sftp = connect_sftp(
            host=project.ssh_host,
            port=project.ssh_port,
            username=project.ssh_username,
            private_key_path=project.private_key_reference,
            host_key_fingerprint=project.host_key_fingerprint,
        )
        staging_parent = store.database_path.parent / "staging"
        config_hash = project_config_sha256(project, mapping)
        dry_run = DryRunService().execute(
            site_type=project.site_type,
            mapping_id=mapping.mapping_id,
            local_root=Path(mapping.local_root),
            remote_root=mapping.remote_root,
            sftp=sftp,
            extra_excludes=mapping.exclude_patterns,
            extra_protected=mapping.protect_patterns,
            staging_parent=staging_parent,
            config_sha256=config_hash,
        )
        counts = dry_run.plan.counts()
        print(
            "dry-run:",
            f"ADD={counts[ChangeType.ADD]}",
            f"UPDATE={counts[ChangeType.UPDATE]}",
            f"REMOTE_ONLY={counts[ChangeType.REMOTE_ONLY]}",
        )
        if dry_run.has_blocking_issues or dry_run.staging is None:
            raise RuntimeError("Dry-run has blocking issues")
        store.save_plan(
            project.project_id,
            dry_run.plan,
            str(dry_run.staging.root),
        )
        stored = store.get_plan(dry_run.plan.plan_id)
        if stored is None:
            raise RuntimeError("Saved plan could not be reloaded")

        revalidation = PlanRevalidator().revalidate(
            stored=stored,
            project=project,
            mapping=mapping,
            sftp=sftp,
        )
        if not revalidation.valid:
            raise RuntimeError("Revalidation failed: " + "; ".join(revalidation.reasons))
        print("revalidation: succeeded")

        backup_root = f"/home/{project.ssh_username}/.dxsync/backups"
        backup_result = RemoteBackupEngine().create_backup(
            sftp=sftp,
            plan=stored.plan,
            project_id=project.project_id,
            remote_root=mapping.remote_root,
            backup_root=backup_root,
        )
        store.save_backup(
            BackupRecord(
                backup_id=backup_result.backup_id,
                project_id=project.project_id,
                mapping_id=mapping.mapping_id,
                plan_id=stored.plan.plan_id,
                backup_directory=backup_result.backup_directory,
                manifest_path=backup_result.manifest_path,
                created_at=backup_result.created_at,
                file_count=len(backup_result.files),
                total_bytes=backup_result.total_bytes,
            )
        )
        print(
            "backup:",
            backup_result.backup_id,
            f"files={len(backup_result.files)}",
        )

        deployment = RemoteDeploymentEngine().deploy(
            sftp=sftp,
            plan=stored.plan,
            staging_root=Path(stored.staging_path or ""),
            remote_root=mapping.remote_root,
            authorization=DeploymentAuthorization(
                plan_id=stored.plan.plan_id,
                backup_plan_id=backup_result.backup_id,
                remote_root=mapping.remote_root,
                approved=True,
            ),
            file_mode=mapping.file_mode,
            directory_mode=mapping.directory_mode,
        )
        store.set_plan_status(stored.plan.plan_id, "used")
        print("deployment:", f"files={len(deployment.files)}", f"bytes={deployment.total_bytes}")

        verification = DryRunService().execute(
            site_type=project.site_type,
            mapping_id=mapping.mapping_id,
            local_root=Path(mapping.local_root),
            remote_root=mapping.remote_root,
            sftp=sftp,
            extra_excludes=mapping.exclude_patterns,
            extra_protected=mapping.protect_patterns,
            config_sha256=config_hash,
        )
        verified_counts = verification.plan.counts()
        print(
            "verification:",
            f"SAME={verified_counts[ChangeType.SAME]}",
            f"ADD={verified_counts[ChangeType.ADD]}",
            f"UPDATE={verified_counts[ChangeType.UPDATE]}",
            f"REMOTE_ONLY={verified_counts[ChangeType.REMOTE_ONLY]}",
        )
        if (
            verification.has_blocking_issues
            or verified_counts[ChangeType.ADD]
            or verified_counts[ChangeType.UPDATE]
            or verified_counts[ChangeType.REMOTE_ONLY]
        ):
            raise RuntimeError("Post-deployment verification did not converge")
        return 0
    finally:
        if sftp is not None:
            sftp.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
