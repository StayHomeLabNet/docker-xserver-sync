import stat
from types import SimpleNamespace

import pytest

from dxsync.remote_listing import list_remote_protection_entries


class FakeSftp:
    def __init__(self) -> None:
        self.listdir_calls: list[str] = []
        self.directories = {
            "/home/user/public_html": (
                SimpleNamespace(filename="index.html", st_mode=stat.S_IFREG | 0o644),
                SimpleNamespace(filename="uploads", st_mode=stat.S_IFDIR | 0o755),
                SimpleNamespace(filename="current", st_mode=stat.S_IFLNK | 0o777),
            ),
            "/home/user/public_html/uploads": (
                SimpleNamespace(filename="photo.jpg", st_mode=stat.S_IFREG | 0o644),
            ),
        }

    def listdir_attr(self, path: str):
        self.listdir_calls.append(path)
        return self.directories[path]


def test_lists_only_root_files_and_directories_by_default() -> None:
    entries = list_remote_protection_entries(
        FakeSftp(),
        "/home/user/public_html",
    )

    assert tuple(entry.protection_pattern for entry in entries) == (
        "index.html",
        "uploads/",
    )


def test_recursively_lists_remote_entries_without_following_symlinks() -> None:
    entries = list_remote_protection_entries(
        FakeSftp(),
        "/home/user/public_html",
        recursive=True,
    )

    assert tuple(entry.protection_pattern for entry in entries) == (
        "index.html",
        "uploads/",
        "uploads/photo.jpg",
    )


def test_recursive_listing_does_not_enter_an_existing_protected_directory() -> None:
    sftp = FakeSftp()
    entries = list_remote_protection_entries(
        sftp,
        "/home/user/public_html",
        recursive=True,
        skip_patterns=("uploads/",),
    )

    assert tuple(entry.protection_pattern for entry in entries) == (
        "index.html",
        "uploads/",
    )
    assert sftp.listdir_calls == ["/home/user/public_html"]


def test_remote_listing_has_a_safety_limit() -> None:
    with pytest.raises(RuntimeError, match="上限"):
        list_remote_protection_entries(
            FakeSftp(),
            "/home/user/public_html",
            recursive=True,
            limit=1,
        )
