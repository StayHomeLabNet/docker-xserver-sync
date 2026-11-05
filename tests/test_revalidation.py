import io
import stat
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

from dxsync.integrity import project_config_sha256
from dxsync.maintenance import cleanup_stale_staging
from dxsync.models import Project, SiteType, SyncMapping, utc_now
from dxsync.revalidation import PlanRevalidator
from dxsync.service import DryRunService
from dxsync.storage import ProjectStore
from dxsync.secure_store import MemoryHostKeyStore


@dataclass
class Attr:
    filename: str
    st_mode: int
    st_size: int = 0
    st_mtime: int = 1


class FakeSftp:
    def __init__(self, content: bytes = b"old") -> None:
        self.content = content

    def listdir_attr(self, path: str):
        return [Attr("index.php", stat.S_IFREG | 0o644, len(self.content))]

    def open(self, path: str, mode: str = "r"):
        return io.BytesIO(self.content)


def make_project(tmp_path: Path) -> tuple[Project, SyncMapping]:
    source = tmp_path / "site"
    source.mkdir()
    (source / "index.php").write_bytes(b"new")
    mapping = replace(
        SyncMapping.create("public", str(source), "/home/user/public_html"),
        runtime_paths_confirmed=True,
    )
    project = Project.create(
        name="Example",
        site_type=SiteType.CUSTOM_PHP,
        domain="example.com",
        ssh_host="example.xsrv.jp",
        ssh_username="user",
        host_key_fingerprint="SHA256:test",
        private_key_reference="/tmp/key",
        mappings=(mapping,),
    )
    return project, mapping


def test_config_hash_changes_only_for_deployment_configuration(tmp_path: Path) -> None:
    project, mapping = make_project(tmp_path)
    original = project_config_sha256(project, mapping)
    renamed = replace(project, name="Renamed", updated_at=utc_now())
    changed_destination = replace(mapping, remote_root="/home/user/other")

    assert project_config_sha256(renamed, mapping) == original
    assert project_config_sha256(project, changed_destination) != original


def test_revalidation_accepts_unchanged_plan_and_rejects_source_change(tmp_path: Path) -> None:
    project, mapping = make_project(tmp_path)
    config_hash = project_config_sha256(project, mapping)
    initial = DryRunService().execute(
        site_type=project.site_type,
        mapping_id=mapping.mapping_id,
        local_root=Path(mapping.local_root),
        remote_root=mapping.remote_root,
        sftp=FakeSftp(),
        staging_parent=tmp_path / "staging",
        config_sha256=config_hash,
    )
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.save(project)
    store.save_plan(
        project.project_id,
        initial.plan,
        str(initial.staging.root) if initial.staging else None,
    )
    stored = store.get_plan(initial.plan.plan_id)
    assert stored is not None

    valid = PlanRevalidator().revalidate(
        stored=stored,
        project=project,
        mapping=mapping,
        sftp=FakeSftp(),
    )
    assert valid.valid

    (Path(mapping.local_root) / "index.php").write_bytes(b"changed")
    invalid = PlanRevalidator().revalidate(
        stored=stored,
        project=project,
        mapping=mapping,
        sftp=FakeSftp(),
    )
    assert not invalid.valid
    assert "Local source changed after the plan was created" in invalid.reasons


def test_revalidation_blocks_custom_php_when_runtime_paths_become_unconfirmed(
    tmp_path: Path,
) -> None:
    project, mapping = make_project(tmp_path)
    config_hash = project_config_sha256(project, mapping)
    initial = DryRunService().execute(
        site_type=project.site_type,
        mapping_id=mapping.mapping_id,
        local_root=Path(mapping.local_root),
        remote_root=mapping.remote_root,
        sftp=FakeSftp(),
        staging_parent=tmp_path / "staging",
        config_sha256=config_hash,
    )
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.save(project)
    store.save_plan(project.project_id, initial.plan, str(initial.staging.root))
    stored = store.get_plan(initial.plan.plan_id)
    assert stored is not None

    unconfirmed_mapping = replace(mapping, runtime_paths_confirmed=False)
    unconfirmed_project = replace(project, mappings=(unconfirmed_mapping,))
    result = PlanRevalidator().revalidate(
        stored=stored,
        project=unconfirmed_project,
        mapping=unconfirmed_mapping,
        sftp=FakeSftp(),
    )

    assert not result.valid
    assert any("本番で書き込まれる" in reason for reason in result.reasons)


def test_cleanup_removes_only_stale_referenced_staging(tmp_path: Path) -> None:
    project, mapping = make_project(tmp_path)
    config_hash = project_config_sha256(project, mapping)
    result = DryRunService().execute(
        site_type=project.site_type,
        mapping_id=mapping.mapping_id,
        local_root=Path(mapping.local_root),
        remote_root=mapping.remote_root,
        sftp=FakeSftp(),
        staging_parent=tmp_path / "staging",
        config_sha256=config_hash,
    )
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.save(project)
    store.save_plan(project.project_id, result.plan, str(result.staging.root))
    store.expire_plans(result.plan.expires_at + timedelta(seconds=1))

    cleanup = cleanup_stale_staging(store, tmp_path / "staging")
    assert cleanup.removed == (result.staging.root.resolve(),)
    assert not result.staging.root.exists()
