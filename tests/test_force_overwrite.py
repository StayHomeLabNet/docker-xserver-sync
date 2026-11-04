import io
import stat
from dataclasses import dataclass
from pathlib import Path

from dxsync.force_overwrite import ForceOverwriteEngine


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

    def stat(self, path: str):
        if path in self.directories:
            return RemoteStat(stat.S_IFDIR | 0o755)
        if path in self.files:
            return RemoteStat(stat.S_IFREG | self.modes.get(path, 0o644))
        raise FileNotFoundError(path)

    def mkdir(self, path: str, mode: int = 0o755) -> None:
        self.directories.add(path)

    def open(self, path: str, mode: str = "r"):
        if "w" in mode:
            return MemoryWriter(self, path)
        return io.BytesIO(self.files[path])

    def remove(self, path: str) -> None:
        self.files.pop(path)

    def rename(self, oldpath: str, newpath: str) -> None:
        self.files[newpath] = self.files.pop(oldpath)

    def chmod(self, path: str, mode: int) -> None:
        self.modes[path] = mode


def test_force_overwrite_copies_without_deleting_remote_only(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "index.php").write_bytes(b"local")
    (tmp_path / "nested" / "new.php").write_bytes(b"new")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "keep.jpg").write_bytes(b"protected")
    sftp = MemorySftp()
    sftp.files["/home/user/site/index.php"] = b"remote"
    sftp.files["/home/user/site/remote-only.php"] = b"keep"
    progress: list[tuple[int, int, str]] = []

    result = ForceOverwriteEngine().overwrite(
        sftp=sftp,
        local_root=tmp_path,
        remote_root="/home/user/site",
        protect_patterns=("uploads/",),
        progress_callback=lambda current, total, path: progress.append(
            (current, total, path)
        ),
    )

    assert sftp.files["/home/user/site/index.php"] == b"local"
    assert sftp.files["/home/user/site/nested/new.php"] == b"new"
    assert sftp.files["/home/user/site/remote-only.php"] == b"keep"
    assert "/home/user/site/uploads/keep.jpg" not in sftp.files
    assert result.files == ("index.php", "nested/new.php")
    assert progress[-1][:2] == (2, 2)
