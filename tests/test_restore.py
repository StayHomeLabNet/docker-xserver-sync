import io
import json
import stat
from dataclasses import dataclass

import pytest

from dxsync.restore import RemoteRestoreEngine, RestoreError


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

    def stat(self, path: str):
        if path in self.files:
            return RemoteStat(stat.S_IFREG | 0o600)
        raise FileNotFoundError(path)

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


def test_restore_replaces_expected_current_file_with_verified_backup() -> None:
    sftp, root, destination, _, manifest_path = setup_files()
    result = RemoteRestoreEngine().restore(
        sftp=sftp,
        manifest_path=manifest_path,
        destination_root=root,
        expected_current_hashes={"index.php": digest(b"new")},
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
        )
    assert sftp.files[destination] == b"new"
