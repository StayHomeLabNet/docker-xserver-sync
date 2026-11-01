from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from .validation import normalize_remote_root


class RecoveryRemoteFileSystem(Protocol):
    def listdir_attr(self, path: str) -> list[Any]: ...


@dataclass(frozen=True)
class RecoveryScanResult:
    remote_root: str
    temporary_files: tuple[str, ...]
    issues: tuple[str, ...]


def is_dxsync_temporary_name(filename: str) -> bool:
    return ".dxsync-" in filename and filename.endswith((".new", ".old", ".tmp"))


def scan_recovery_artifacts(
    sftp: RecoveryRemoteFileSystem,
    remote_root: str,
) -> RecoveryScanResult:
    """Find recovery artifacts without reading, changing, or deleting remote files."""
    root = normalize_remote_root(remote_root)
    pending = [(root, "")]
    found: list[str] = []
    issues: list[str] = []
    while pending:
        absolute_directory, relative_directory = pending.pop()
        try:
            entries = sftp.listdir_attr(absolute_directory)
        except OSError as exc:
            issues.append(f"{relative_directory or '.'}: {exc}")
            continue
        for entry in entries:
            relative = (
                f"{relative_directory}/{entry.filename}"
                if relative_directory
                else entry.filename
            )
            absolute = str(PurePosixPath(absolute_directory) / entry.filename)
            if stat.S_ISLNK(entry.st_mode):
                continue
            if stat.S_ISDIR(entry.st_mode):
                pending.append((absolute, relative))
            elif stat.S_ISREG(entry.st_mode) and is_dxsync_temporary_name(entry.filename):
                found.append(relative)
    return RecoveryScanResult(root, tuple(sorted(found)), tuple(sorted(issues)))
