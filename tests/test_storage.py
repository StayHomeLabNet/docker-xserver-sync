from datetime import timedelta
from pathlib import Path

from dxsync.models import EntryKind, FileChange, FileRecord, Project, SiteType, SyncMapping, SyncPlan, ChangeType, utc_now
from dxsync.storage import BackupRecord, ProjectStore


def test_project_round_trip(tmp_path: Path) -> None:
    mapping = SyncMapping.create("public", str(tmp_path / "site"), "/remote/public_html")
    project = Project.create(
        name="Example",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="example",
        host_key_fingerprint="SHA256:test",
        private_key_reference="keyring://example",
        mappings=(mapping,),
    )
    store = ProjectStore(tmp_path / "state.db")

    store.save(project)
    loaded = store.get(project.project_id)

    assert loaded == project
    assert store.list_projects() == (project,)

    assert store.delete(project.project_id)
    assert store.get(project.project_id) is None
    assert not store.delete(project.project_id)


def test_plan_round_trip_and_expiry(tmp_path: Path) -> None:
    mapping = SyncMapping.create("public", str(tmp_path), "/home/example/public_html")
    project = Project.create(
        name="Example",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="example",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/key",
        mappings=(mapping,),
    )
    now = utc_now()
    local = FileRecord("index.php", EntryKind.FILE, 5, 1, "new")
    plan = SyncPlan(
        plan_id="plan-1",
        mapping_id=mapping.mapping_id,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        changes=(FileChange("index.php", ChangeType.ADD, local, None),),
        local_snapshot_sha256="local",
        remote_snapshot_sha256="remote",
    )
    store = ProjectStore(tmp_path / "state.db")
    store.save(project)
    store.save_plan(project.project_id, plan, "/tmp/staging/plan-1")

    loaded = store.get_plan(plan.plan_id)
    assert loaded is not None
    assert loaded.project_id == project.project_id
    assert loaded.plan == plan
    assert loaded.status == "ready"
    assert loaded.staging_path == "/tmp/staging/plan-1"

    assert store.expire_plans(now + timedelta(minutes=6)) == 1
    assert store.get_plan(plan.plan_id).status == "expired"


def test_operation_journal_and_backup_history(tmp_path: Path) -> None:
    mapping = SyncMapping.create("public", str(tmp_path), "/home/example/public_html")
    project = Project.create(
        name="Example",
        site_type=SiteType.WORDPRESS,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="example",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/key",
        mappings=(mapping,),
    )
    store = ProjectStore(tmp_path / "state.db")
    store.save(project)
    operation = store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        kind="dry_run",
        details={"mode": "read_only"},
    )
    assert operation.status == "running"
    assert store.finish_operation(
        operation.operation_id,
        status="succeeded",
        details={"add": 1},
    )
    loaded_operation = store.list_operations(project.project_id)[0]
    assert loaded_operation.status == "succeeded"
    assert loaded_operation.details == {"add": 1}
    assert loaded_operation.completed_at is not None

    backup = BackupRecord(
        backup_id="backup-1",
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        plan_id="plan-1",
        backup_directory="/home/example/.dxsync/backups/backup-1",
        manifest_path="/home/example/.dxsync/backups/backup-1/manifest.json",
        created_at=utc_now(),
        file_count=2,
        total_bytes=123,
    )
    store.save_backup(backup)
    assert store.list_backups(project.project_id) == (backup,)
