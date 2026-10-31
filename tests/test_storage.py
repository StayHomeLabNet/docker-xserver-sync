from datetime import timedelta
from pathlib import Path

from dxsync.models import EntryKind, FileChange, FileRecord, Project, SiteType, SyncMapping, SyncPlan, ChangeType, utc_now
from dxsync.storage import ProjectStore


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
