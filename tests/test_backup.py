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


def test_backup_creates_manifest_when_remote_is_empty() -> None:
    sftp = MemorySftp()
    sftp.directories.update({"/home", "/home/user", "/home/user/site"})
    now = utc_now()
    plan = SyncPlan(
        "plan-empty",
        "mapping-1",
        now,
        now + timedelta(minutes=15),
        (
            FileChange(
                "index.php",
                ChangeType.ADD,
                FileRecord("index.php", EntryKind.FILE, 3, 1, digest(b"new")),
                None,
            ),
        ),
        "local",
        "remote",
    )

    result = RemoteBackupEngine().create_backup(
        sftp=sftp,
        plan=plan,
        project_id="project-1",
        remote_root="/home/user/site",
        backup_root="/home/user/.dxsync/backups",
    )

    assert result.files == ()
    assert result.total_bytes == 0
    assert result.manifest_path in sftp.files


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


def test_backup_copies_only_approved_remote_only_deletion_candidates() -> None:
    sftp = MemorySftp()
    sftp.directories.update({"/home", "/home/user", "/home/user/site"})
    sftp.files["/home/user/site/remove.php"] = b"remove"
    sftp.files["/home/user/site/keep.php"] = b"keep"
    now = utc_now()
    changes = tuple(
        FileChange(
            name,
            ChangeType.REMOTE_ONLY,
            None,
            FileRecord(name, EntryKind.FILE, len(content), 1, digest(content)),
        )
        for name, content in (("remove.php", b"remove"), ("keep.php", b"keep"))
    )
    plan = SyncPlan(
        "plan-delete",
        "mapping-1",
        now,
        now + timedelta(minutes=5),
        changes,
        "local",
        "remote",
    )

    result = RemoteBackupEngine().create_backup(
        sftp=sftp,
        plan=plan,
        project_id="project-1",
        remote_root="/home/user/site",
        backup_root="/home/user/.dxsync/backups",
        delete_paths=("remove.php",),
    )

    assert [item.relative_path for item in result.files] == ["remove.php"]
    assert result.files[0].change_type is ChangeType.REMOTE_ONLY
    assert result.files[0].mode == 0o600
    assert sftp.files["/home/user/site/remove.php"] == b"remove"
    assert sftp.files["/home/user/site/keep.php"] == b"keep"


def test_backup_reports_progress_and_supports_cancellation() -> None:
    from dxsync.cancellation import CancellationToken, OperationCancelled

    sftp = MemorySftp()
    sftp.directories.update({"/home", "/home/user", "/home/user/site"})
    sftp.files["/home/user/site/one.php"] = b"one"
    sftp.files["/home/user/site/two.php"] = b"two"
    now = utc_now()
    changes = tuple(
        FileChange(
            name,
            ChangeType.UPDATE,
            FileRecord(name, EntryKind.FILE, 3, 1, digest(b"new")),
            FileRecord(name, EntryKind.FILE, len(content), 1, digest(content)),
        )
        for name, content in (("one.php", b"one"), ("two.php", b"two"))
    )
    plan = SyncPlan(
        "plan-progress",
        "mapping-1",
        now,
        now + timedelta(minutes=5),
        changes,
        "local",
        "remote",
    )

    events: list[tuple[int, int, str]] = []
    RemoteBackupEngine().create_backup(
        sftp=sftp,
        plan=plan,
        project_id="project-1",
        remote_root="/home/user/site",
        backup_root="/home/user/.dxsync/backups",
        progress_callback=lambda cur, tot, path: events.append((cur, tot, path)),
    )
    assert events == [(0, 2, "one.php"), (1, 2, "one.php"), (2, 2, "two.php")]

    cancel = CancellationToken()

    def cancel_after_first(cur: int, tot: int, path: str) -> None:
        if cur >= 1:
            cancel.cancel()

    with pytest.raises(OperationCancelled):
        RemoteBackupEngine().create_backup(
            sftp=sftp,
            plan=plan,
            project_id="project-2",
            remote_root="/home/user/site",
            backup_root="/home/user/.dxsync/backups",
            progress_callback=cancel_after_first,
            cancellation_token=cancel,
        )
    assert not any("project-2" in path for path in sftp.files)


def test_select_prune_candidates_protects_latest_and_recent_backups() -> None:
    from datetime import datetime, timezone
    from dxsync.backup import select_prune_candidates
    from dxsync.storage import BackupRecord

    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    records = [
        BackupRecord("b-new-1", "p1", "m1", "pl1", "/home/u/.dxsync/backups/p1/b-new-1", "/m1", now - timedelta(days=1), 1, 10, "available"),
        BackupRecord("b-new-2", "p1", "m1", "pl2", "/home/u/.dxsync/backups/p1/b-new-2", "/m2", now - timedelta(days=10), 1, 10, "restored"),
        BackupRecord("b-old-3", "p1", "m1", "pl3", "/home/u/.dxsync/backups/p1/b-old-3", "/m3", now - timedelta(days=45), 1, 10, "available"),
        BackupRecord("b-del-4", "p1", "m1", "pl4", "/home/u/.dxsync/backups/p1/b-del-4", "/m4", now - timedelta(days=90), 1, 10, "deleted"),
    ]

    # keep_latest=2, max_age_days=30: b-new-1 and b-new-2 kept, b-old-3 pruned, b-del-4 ignored
    candidates = select_prune_candidates(records, keep_latest=2, max_age_days=30, now=now)
    assert [c.backup_id for c in candidates] == ["b-old-3"]

    # keep_latest=3 protects b-old-3 even if older than 30 days
    assert select_prune_candidates(records, keep_latest=3, max_age_days=30, now=now) == ()

    # max_age_days=0 prunes everything beyond keep_latest=1
    candidates_count_only = select_prune_candidates(records, keep_latest=1, max_age_days=0, now=now)
    assert [c.backup_id for c in candidates_count_only] == ["b-new-2", "b-old-3"]


def test_delete_remote_backup_removes_tree_and_rejects_unsafe_paths() -> None:
    from types import SimpleNamespace
    from dxsync.backup import delete_remote_backup

    class TreeSftp(MemorySftp):
        def listdir_attr(self, path: str):
            prefix = path.rstrip("/") + "/"
            seen: dict[str, int] = {}
            for d in self.directories:
                if d.startswith(prefix):
                    rest = d[len(prefix):]
                    if rest and "/" not in rest:
                        seen[rest] = stat.S_IFDIR | 0o700
            for f in self.files:
                if f.startswith(prefix):
                    rest = f[len(prefix):]
                    if rest and "/" not in rest:
                        seen[rest] = stat.S_IFREG | 0o600
            return [SimpleNamespace(filename=name, st_mode=mode) for name, mode in seen.items()]

        def rmdir(self, path: str) -> None:
            self.directories.discard(path)

    sftp = TreeSftp()
    sftp.directories.update({
        "/home",
        "/home/u",
        "/home/u/.dxsync",
        "/home/u/.dxsync/backups",
        "/home/u/.dxsync/backups/proj-1",
        "/home/u/.dxsync/backups/proj-1/bk-1",
        "/home/u/.dxsync/backups/proj-1/bk-1/sub",
    })
    sftp.files["/home/u/.dxsync/backups/proj-1/bk-1/sub/file.php"] = b"data"
    sftp.files["/home/u/.dxsync/backups/proj-1/bk-1/.dxsync-backup-manifest.json"] = b"{}"

    with pytest.raises(BackupError, match="Refusing to delete"):
        delete_remote_backup(sftp, "/home/u/public_html")
    with pytest.raises(BackupError, match="Refusing to delete"):
        delete_remote_backup(sftp, "/home/u/.dxsync/backups/proj-1")

    assert delete_remote_backup(sftp, "/home/u/.dxsync/backups/proj-1/bk-1") is True
    assert "/home/u/.dxsync/backups/proj-1/bk-1" not in sftp.directories
    assert not any("bk-1" in f for f in sftp.files)
    assert delete_remote_backup(sftp, "/home/u/.dxsync/backups/proj-1/bk-1") is False


