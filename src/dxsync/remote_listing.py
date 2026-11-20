from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .validation import normalize_remote_root


@dataclass(frozen=True)
class RemoteProtectionEntry:
    relative_path: str
    is_directory: bool

    @property
    def protection_pattern(self) -> str:
        return f"{self.relative_path}/" if self.is_directory else self.relative_path


def list_remote_protection_entries(
    sftp: Any,
    remote_root: str,
    *,
    limit: int = 5000,
) -> tuple[RemoteProtectionEntry, ...]:
    """List regular files and directories without reading remote file contents."""
    root = normalize_remote_root(remote_root)
    pending: list[tuple[str, str]] = [(root, "")]
    result: list[RemoteProtectionEntry] = []

    while pending:
        absolute_directory, relative_directory = pending.pop()
        entries = sorted(
            sftp.listdir_attr(absolute_directory),
            key=lambda entry: entry.filename,
            reverse=True,
        )
        for entry in entries:
            relative_path = (
                f"{relative_directory}/{entry.filename}"
                if relative_directory
                else entry.filename
            )
            absolute_path = str(PurePosixPath(absolute_directory) / entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                result.append(RemoteProtectionEntry(relative_path, True))
                pending.append((absolute_path, relative_path))
            elif stat.S_ISREG(entry.st_mode):
                result.append(RemoteProtectionEntry(relative_path, False))
            else:
                continue
            if len(result) > limit:
                raise RuntimeError(
                    f"同期先の項目数が上限（{limit:,}件）を超えました。"
                    "保護対象はパスを直接入力してください。"
                )

    return tuple(sorted(result, key=lambda entry: entry.relative_path))
