import hashlib
import io
import stat
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from dxsync.deployment import (
    DeployError,
    DeploymentAuthorization,
    RemoteDeploymentEngine,
)
from dxsync.models import ChangeType, EntryKind, FileChange, FileRecord, SyncPlan, utc_now


@dataclass
class RemoteStat:
    st_mode: int


class MemoryWriter(io.BytesIO):
    def __init__(self, filesystem, path: str) -> None:
        super().__init__()
        self.filesystem = filesystem
        self.path = path

    def close(self) -> None:
        if not self.closed:
            self.filesystem.files[self.path] = self.getvalue()
        super().close()


class MemorySftp:
    def __init__(self) -> None:
        self.directories = {"/", "/home", "/home/user", "/home/user/site"}
        self.files: dict[str, bytes] = {}
        self.modes: dict[str, int] = {}
        self.fail_rename_to: str | None = None

    def stat(self, path: str):
        if path in self.directories:
            return RemoteStat(stat.S_IFDIR | 0o755)
        if path in self.files:
            return RemoteStat(stat.S_IFREG | self.modes.get(path, 0o644))
        raise FileNotFoundError(path)

    def mkdir(self, path: str, mode: int = 0o755) -> None:
        self.directories.add(path)

    def rmdir(self, path: str) -> None:
        prefix = path + "/"
        if any(item.startswith(prefix) for item in set(self.files) | self.directories):
            raise OSError("not empty")
        self.directories.remove(path)

    def open(self, path: str, mode: str = "r"):
        if "w" in mode:
            return MemoryWriter(self, path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return io.BytesIO(self.files[path])

    def remove(self, path: str) -> None:
        del self.files[path]
        self.modes.pop(path, None)

    def rename(self, oldpath: str, newpath: str) -> None:
        if newpath == self.fail_rename_to:
            self.fail_rename_to = None
            raise OSError("injected rename failure")
        self.files[newpath] = self.files.pop(oldpath)
        if oldpath in self.modes:
            self.modes[newpath] = self.modes.pop(oldpath)

    def chmod(self, path: str, mode: int) -> None:
        if path not in self.files:
            raise FileNotFoundError(path)
        self.modes[path] = mode


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def record(path: str, content: bytes) -> FileRecord:
    return FileRecord(path, EntryKind.FILE, len(content), 1, digest(content))


def make_plan(changes: tuple[FileChange, ...]) -> SyncPlan:
    now = utc_now()
    return SyncPlan(
        plan_id="plan-123",
        mapping_id="mapping",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        changes=changes,
        local_snapshot_sha256="local",
        remote_snapshot_sha256="remote",
    )


def authorize(plan: SyncPlan, remote_root: str = "/home/user/site") -> DeploymentAuthorization:
    return DeploymentAuthorization(
        plan_id=plan.plan_id,
        backup_plan_id=plan.plan_id,
        remote_root=remote_root,
        approved=True,
    )


def stage(root: Path, path: str, content: bytes) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_deploy_add_and_update_after_full_temporary_upload(tmp_path: Path) -> None:
    sftp = MemorySftp()
    sftp.files["/home/user/site/update.php"] = b"old"
    sftp.modes["/home/user/site/update.php"] = 0o600
    stage(tmp_path, "update.php", b"new")
    stage(tmp_path, "nested/add.php", b"add")
    plan = make_plan(
        (
            FileChange(
                "update.php",
                ChangeType.UPDATE,
                record("update.php", b"new"),
                record("update.php", b"old"),
            ),
            FileChange(
                "nested/add.php",
                ChangeType.ADD,
                record("nested/add.php", b"add"),
                None,
            ),
        )
    )

    result = RemoteDeploymentEngine().deploy(
        sftp=sftp,
        plan=plan,
        staging_root=tmp_path,
        remote_root="/home/user/site",
        authorization=authorize(plan),
    )

    assert sftp.files["/home/user/site/update.php"] == b"new"
    assert sftp.files["/home/user/site/nested/add.php"] == b"add"
    assert sftp.modes["/home/user/site/update.php"] == 0o600
    assert sftp.modes["/home/user/site/nested/add.php"] == 0o644
    assert result.total_bytes == 6
    assert not any(".dxsync-" in path for path in sftp.files)


def test_deploy_rolls_back_all_applied_files_on_later_failure(tmp_path: Path) -> None:
    sftp = MemorySftp()
    sftp.files["/home/user/site/one.php"] = b"old-one"
    sftp.files["/home/user/site/two.php"] = b"old-two"
    stage(tmp_path, "one.php", b"new-one")
    stage(tmp_path, "two.php", b"new-two")
    plan = make_plan(
        tuple(
            FileChange(
                name,
                ChangeType.UPDATE,
                record(name, new),
                record(name, old),
            )
            for name, old, new in (
                ("one.php", b"old-one", b"new-one"),
                ("two.php", b"old-two", b"new-two"),
            )
        )
    )
    sftp.fail_rename_to = "/home/user/site/two.php"

    with pytest.raises(OSError, match="injected rename failure"):
        RemoteDeploymentEngine().deploy(
            sftp=sftp,
            plan=plan,
            staging_root=tmp_path,
            remote_root="/home/user/site",
            authorization=authorize(plan),
        )
    assert sftp.files["/home/user/site/one.php"] == b"old-one"
    assert sftp.files["/home/user/site/two.php"] == b"old-two"
    assert not any(".dxsync-" in path for path in sftp.files)


def test_deploy_refuses_if_remote_update_changed(tmp_path: Path) -> None:
    sftp = MemorySftp()
    sftp.files["/home/user/site/index.php"] = b"external"
    stage(tmp_path, "index.php", b"new")
    plan = make_plan(
        (
            FileChange(
                "index.php",
                ChangeType.UPDATE,
                record("index.php", b"new"),
                record("index.php", b"old"),
            ),
        )
    )
    with pytest.raises(DeployError, match="changed before upload"):
        RemoteDeploymentEngine().deploy(
            sftp=sftp,
            plan=plan,
            staging_root=tmp_path,
            remote_root="/home/user/site",
            authorization=authorize(plan),
        )
    assert sftp.files["/home/user/site/index.php"] == b"external"


def test_deploy_requires_matching_one_time_authorization(tmp_path: Path) -> None:
    sftp = MemorySftp()
    stage(tmp_path, "index.php", b"new")
    plan = make_plan(
        (
            FileChange(
                "index.php",
                ChangeType.ADD,
                record("index.php", b"new"),
                None,
            ),
        )
    )
    approval = authorize(plan, "/home/user/other")

    with pytest.raises(DeployError, match="remote root"):
        RemoteDeploymentEngine().deploy(
            sftp=sftp,
            plan=plan,
            staging_root=tmp_path,
            remote_root="/home/user/site",
            authorization=approval,
        )
    assert "/home/user/site/index.php" not in sftp.files

    approval = authorize(plan)
    RemoteDeploymentEngine().deploy(
        sftp=sftp,
        plan=plan,
        staging_root=tmp_path,
        remote_root="/home/user/site",
        authorization=approval,
    )
    with pytest.raises(DeployError, match="already been used"):
        RemoteDeploymentEngine().deploy(
            sftp=sftp,
            plan=plan,
            staging_root=tmp_path,
            remote_root="/home/user/site",
            authorization=approval,
        )
