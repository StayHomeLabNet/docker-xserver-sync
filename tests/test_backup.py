import io
import stat
from dataclasses import dataclass
from datetime import timedelta

import pytest

from dxsync.backup import BackupError, RemoteBackupEngine
from dxsync.models import (
    ChangeType,
    EntryKind,
    FileChange,
    FileRecord,
    SyncPlan,
    utc_now,
)
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
        self.directories = {"/"}
        self.files: dict[str, bytes] = {}

    def stat(self, path: str):
        if path in self.directories:
            return RemoteStat(stat.S_IFDIR | 0o700)
        if path in self.files:
            return RemoteStat(stat.S_IFREG | 0o600)
        raise FileNotFoundError(path)

    def mkdir(self, path: str, mode: int = 0o755) -> None:
        self.directories.add(path)

    def open(self, path: str, mode: str = "r"):
        if "w" in mode:
            return MemoryWriter(self, path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return io.BytesIO(self.files[path])

    def remove(self, path: str) -> None:
        del self.files[path]

    def rename(self, oldpath: str, newpath: str) -> None:
        self.files[newpath] = self.files.pop(oldpath)


def digest(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def make_plan(remote_content: bytes, remote_hash: str | None = None) -> SyncPlan:
    now = utc_now()
    remote = FileRecord(
        "index.php",
        EntryKind.FILE,
        len(remote_content),
        1,
        remote_hash or digest(remote_content),
    )
    local = FileRecord("index.php", EntryKind.FILE, 3, 1, digest(b"new"))
    return SyncPlan(
        plan_id="plan-123",
        mapping_id="mapping-1",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        changes=(FileChange("index.php", ChangeType.UPDATE, local, remote),),
        local_snapshot_sha256="local",
        remote_snapshot_sha256="remote",
    )


def test_backup_copies_update_and_verifies_manifest() -> None:
    sftp = MemorySftp()
    sftp.directories.update({"/home", "/home/user", "/home/user/site"})
    sftp.files["/home/user/site/index.php"] = b"old"

    result = RemoteBackupEngine().create_backup(
        sftp=sftp,
        plan=make_plan(b"old"),
        project_id="project-1",
        remote_root="/home/user/site",
        backup_root="/home/user/.dxsync/backups",
    )

    assert result.total_bytes == 3
    assert len(result.files) == 1
    assert sftp.files[result.files[0].backup_path] == b"old"
    assert result.manifest_path in sftp.files
    assert sftp.files["/home/user/site/index.php"] == b"old"


def test_backup_stops_if_remote_changed_after_plan() -> None:
    sftp = MemorySftp()
    sftp.directories.update({"/home", "/home/user", "/home/user/site"})
    sftp.files["/home/user/site/index.php"] = b"changed"

    with pytest.raises(BackupError, match="Remote source changed"):
        RemoteBackupEngine().create_backup(
            sftp=sftp,
            plan=make_plan(b"old"),
            project_id="project-1",
            remote_root="/home/user/site",
            backup_root="/home/user/.dxsync/backups",
        )
    assert sftp.files["/home/user/site/index.php"] == b"changed"


def test_backup_root_cannot_be_inside_deployment_root() -> None:
    sftp = MemorySftp()
    with pytest.raises(BackupError, match="outside the deployment root"):
        RemoteBackupEngine().create_backup(
            sftp=sftp,
            plan=make_plan(b"old"),
            project_id="project-1",
            remote_root="/home/user/site",
            backup_root="/home/user/site/backups",
        )
