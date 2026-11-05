from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3

from dxsync.models import EntryKind, FileChange, FileRecord, Project, SiteType, SyncMapping, SyncPlan, ChangeType, utc_now
from dxsync.storage import BackupRecord, ProjectStore
from dxsync.secure_store import MemoryHostKeyStore


def test_project_round_trip(tmp_path: Path) -> None:
    mapping = replace(
        SyncMapping.create("public", str(tmp_path / "site"), "/remote/public_html"),
        protect_patterns=("userfiles/",),
        runtime_paths_confirmed=True,
    )
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
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())

    store.save(project)
    loaded = store.get(project.project_id)

    assert loaded == project
    assert store.list_projects() == (project,)

    assert store.delete(project.project_id)
    assert store.get(project.project_id) is None
    assert not store.delete(project.project_id)


def test_legacy_host_key_is_migrated_out_of_sqlite(tmp_path: Path) -> None:
    secure_store = MemoryHostKeyStore()
    database = tmp_path / "state.db"
    store = ProjectStore(database, secure_store)
    mapping = SyncMapping.create("public", str(tmp_path), "/remote")
    project = Project.create(
        name="Legacy",
        site_type=SiteType.CUSTOM_PHP,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:legacy",
        private_key_reference="/tmp/key",
        mappings=(mapping,),
    )
    store.save(project)
    secure_store.values.clear()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE projects SET host_key_fingerprint = ?, host_key_reference = '' "
            "WHERE project_id = ?",
            ("SHA256:legacy", project.project_id),
        )

    migrated = ProjectStore(database, secure_store)
    loaded = migrated.get(project.project_id)

    assert loaded is not None
    assert loaded.host_key_fingerprint == "SHA256:legacy"
    assert migrated.security_status(project.project_id).state == "secure"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT host_key_fingerprint, host_key_reference FROM projects"
        ).fetchone()
    assert row[0] == ""
    assert row[1].startswith("keyring://")


def test_failed_host_key_migration_preserves_plaintext_and_blocks_status(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    working_store = MemoryHostKeyStore()
    store = ProjectStore(database, working_store)
    mapping = SyncMapping.create("public", str(tmp_path), "/remote")
    project = Project.create(
        name="Legacy",
        site_type=SiteType.CUSTOM_PHP,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:legacy",
        private_key_reference="/tmp/key",
        mappings=(mapping,),
    )
    store.save(project)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE projects SET host_key_fingerprint = ?, host_key_reference = '' "
            "WHERE project_id = ?",
            ("SHA256:legacy", project.project_id),
        )
    failing = MemoryHostKeyStore(fail_writes=True)

    failed_store = ProjectStore(database, failing)
    assert failed_store.security_status(project.project_id).state == "legacy"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT host_key_fingerprint, host_key_reference FROM projects"
        ).fetchone()
    assert row == ("SHA256:legacy", "")


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
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
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
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
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
    interrupted = store.start_operation(
        project_id=project.project_id,
        mapping_id=mapping.mapping_id,
        kind="deployment",
        details={"mode": "write"},
    )
    assert store.recover_interrupted_operations() == (interrupted,)
    recovered = next(
        item for item in store.list_operations(project.project_id)
        if item.operation_id == interrupted.operation_id
    )
    assert recovered.status == "interrupted"
    assert recovered.details["detected_on_startup"] is True
    loaded_operation = next(
        item for item in store.list_operations(project.project_id)
        if item.operation_id == operation.operation_id
    )
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
    assert store.get_backup(backup.backup_id) == backup
    assert store.set_backup_status(backup.backup_id, "restored")
    assert store.get_backup(backup.backup_id).status == "restored"
