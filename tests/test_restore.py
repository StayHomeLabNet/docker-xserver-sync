import io
import json
import stat
from dataclasses import dataclass

import pytest

from dxsync.restore import RemoteRestoreEngine, RestoreAuthorization, RestoreError


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
        self.files: dict[str, bytes] = {}
        self.modes: dict[str, int] = {}
        self.fail_rename_to: str | None = None

    def stat(self, path: str):
        if path in self.files:
            return RemoteStat(stat.S_IFREG | self.modes.get(path, 0o600))
        raise FileNotFoundError(path)

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
        self.modes[path] = mode


def digest(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def setup_files(current: bytes = b"new", backup: bytes = b"old"):
    sftp = MemorySftp()
    root = "/home/user/site"
    backup_directory = "/home/user/.dxsync/backups/project/plan"
    destination = root + "/index.php"
    backup_path = backup_directory + "/index.php"
    manifest_path = backup_directory + "/.dxsync-backup-manifest.json"
    manifest = {
        "backup_id": "plan",
        "source_root": root,
        "files": [
            {
                "relative_path": "index.php",
                "sha256": digest(backup),
                "size": len(backup),
                "backup_path": backup_path,
            }
        ],
    }
    sftp.files[destination] = current
    sftp.files[backup_path] = backup
    sftp.files[manifest_path] = json.dumps(manifest).encode()
    return sftp, root, destination, backup_path, manifest_path


def authorize(root: str, backup_id: str = "plan") -> RestoreAuthorization:
    return RestoreAuthorization(backup_id, root, True)


def test_restore_replaces_expected_current_file_with_verified_backup() -> None:
    sftp, root, destination, _, manifest_path = setup_files()
    result = RemoteRestoreEngine().restore(
        sftp=sftp,
        manifest_path=manifest_path,
        destination_root=root,
        expected_current_hashes={"index.php": digest(b"new")},
        authorization=authorize(root),
    )

    assert sftp.files[destination] == b"old"
    assert result.total_bytes == 3
    assert len(result.files) == 1
    assert not any(".dxsync-restore" in path for path in sftp.files)


def test_restore_refuses_externally_modified_current_file() -> None:
    sftp, root, destination, _, manifest_path = setup_files(current=b"external")
    with pytest.raises(RestoreError, match="externally modified"):
        RemoteRestoreEngine().restore(
            sftp=sftp,
            manifest_path=manifest_path,
            destination_root=root,
            expected_current_hashes={"index.php": digest(b"new")},
            authorization=authorize(root),
        )
    assert sftp.files[destination] == b"external"


def test_restore_refuses_corrupt_backup() -> None:
    sftp, root, destination, backup_path, manifest_path = setup_files()
    sftp.files[backup_path] = b"corrupt"
    with pytest.raises(RestoreError, match="Backup file verification failed"):
        RemoteRestoreEngine().restore(
            sftp=sftp,
            manifest_path=manifest_path,
            destination_root=root,
            expected_current_hashes={"index.php": digest(b"new")},
            authorization=authorize(root),
        )
    assert sftp.files[destination] == b"new"


def test_restore_requires_matching_one_time_authorization() -> None:
    sftp, root, _, _, manifest_path = setup_files()
    approval = RestoreAuthorization("wrong", root, True)
    with pytest.raises(RestoreError, match="does not match the backup"):
        RemoteRestoreEngine().restore(
            sftp=sftp,
            manifest_path=manifest_path,
            destination_root=root,
            expected_current_hashes={"index.php": digest(b"new")},
            authorization=approval,
        )

    approval = authorize(root)
    RemoteRestoreEngine().restore(
        sftp=sftp,
        manifest_path=manifest_path,
        destination_root=root,
        expected_current_hashes={"index.php": digest(b"new")},
        authorization=approval,
    )
    with pytest.raises(RestoreError, match="already been used"):
        RemoteRestoreEngine().restore(
            sftp=sftp,
            manifest_path=manifest_path,
            destination_root=root,
            expected_current_hashes={"index.php": digest(b"old")},
            authorization=approval,
        )


def test_restore_rolls_back_all_files_when_later_publish_fails() -> None:
    sftp = MemorySftp()
    root = "/home/user/site"
    backup_directory = "/home/user/.dxsync/backups/project/plan"
    records = []
    expected = {}
    for name, current, backup in (
        ("one.php", b"new-one", b"old-one"),
        ("two.php", b"new-two", b"old-two"),
    ):
        destination = f"{root}/{name}"
        backup_path = f"{backup_directory}/{name}"
        sftp.files[destination] = current
        sftp.files[backup_path] = backup
        expected[name] = digest(current)
        records.append({
            "relative_path": name,
            "sha256": digest(backup),
            "size": len(backup),
            "backup_path": backup_path,
        })
    manifest_path = backup_directory + "/.dxsync-backup-manifest.json"
    sftp.files[manifest_path] = json.dumps({
        "backup_id": "plan",
        "source_root": root,
        "files": records,
    }).encode()
    sftp.fail_rename_to = f"{root}/two.php"

    with pytest.raises(OSError, match="injected rename failure"):
        RemoteRestoreEngine().restore(
            sftp=sftp,
            manifest_path=manifest_path,
            destination_root=root,
            expected_current_hashes=expected,
            authorization=authorize(root),
        )
    assert sftp.files[f"{root}/one.php"] == b"new-one"
    assert sftp.files[f"{root}/two.php"] == b"new-two"
    assert not any(".dxsync-restore" in path for path in sftp.files)


def test_restore_recreates_approved_deleted_file() -> None:
    sftp = MemorySftp()
    root = "/home/user/site"
    backup_directory = "/home/user/.dxsync/backups/project/plan"
    destination = root + "/removed.php"
    backup_path = backup_directory + "/removed.php"
    manifest_path = backup_directory + "/.dxsync-backup-manifest.json"
    sftp.files[backup_path] = b"original"
    sftp.files[manifest_path] = json.dumps({
        "backup_id": "plan",
        "source_root": root,
        "files": [{
            "relative_path": "removed.php",
            "sha256": digest(b"original"),
            "size": len(b"original"),
            "backup_path": backup_path,
            "change_type": "remote_only",
            "mode": 0o640,
        }],
    }).encode()

    result = RemoteRestoreEngine().restore(
        sftp=sftp,
        manifest_path=manifest_path,
        destination_root=root,
        expected_current_hashes={"removed.php": None},
        authorization=authorize(root),
    )

    assert sftp.files[destination] == b"original"
    assert sftp.modes[destination] == 0o640
    assert result.files[0].relative_path == "removed.php"
