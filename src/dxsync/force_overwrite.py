from __future__ import annotations

import errno
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Protocol

from .models import normalize_relative_path
from .rules import PathRules
from .validation import normalize_remote_root


class ForceOverwriteError(RuntimeError):
    pass


class ForceOverwriteRemoteFileSystem(Protocol):
    def open(self, path: str, mode: str = "r") -> BinaryIO: ...
    def stat(self, path: str) -> Any: ...
    def mkdir(self, path: str, mode: int = 0o755) -> None: ...
    def remove(self, path: str) -> None: ...
    def rename(self, oldpath: str, newpath: str) -> None: ...
    def chmod(self, path: str, mode: int) -> None: ...


@dataclass(frozen=True)
class ForceOverwriteResult:
    remote_root: str
    files: tuple[str, ...]
    total_bytes: int


def _exists(sftp: ForceOverwriteRemoteFileSystem, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ENOENT:
            return False
        raise


def _local_files(
    root: Path,
    excludes: tuple[str, ...],
    protected: tuple[str, ...],
) -> tuple[Path, ...]:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise ForceOverwriteError(f"Local root is not a readable directory: {resolved}")
    exclude_rules = PathRules(excludes)
    protect_rules = PathRules(protected)
    files: list[Path] = []
    for current, directory_names, file_names in os.walk(resolved, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directory_names):
            absolute = current_path / name
            relative = absolute.relative_to(resolved).as_posix()
            if absolute.is_symlink():
                raise ForceOverwriteError(f"Symbolic link is not allowed: {relative}")
            if exclude_rules.matches(relative, is_directory=True) or protect_rules.matches(
                relative, is_directory=True
            ):
                continue
            kept.append(name)
        directory_names[:] = kept
        for name in sorted(file_names):
            absolute = current_path / name
            relative = absolute.relative_to(resolved).as_posix()
            if exclude_rules.matches(relative) or protect_rules.matches(relative):
                continue
            if absolute.is_symlink() or not absolute.is_file():
                raise ForceOverwriteError(f"Only regular files may be copied: {relative}")
            files.append(absolute)
    return tuple(sorted(files, key=lambda item: item.relative_to(resolved).as_posix()))


def collect_overwrite_candidates(
    local_root: Path,
    *,
    exclude_patterns: tuple[str, ...] = (),
    protect_patterns: tuple[str, ...] = (),
) -> tuple[str, ...]:
    resolved_root = local_root.expanduser().resolve()
    return tuple(
        path.relative_to(resolved_root).as_posix()
        for path in _local_files(resolved_root, exclude_patterns, protect_patterns)
    )


def _ensure_parents(
    sftp: ForceOverwriteRemoteFileSystem,
    root: str,
    directory: str,
    mode: int,
) -> None:
    root_path = PurePosixPath(root)
    directory_path = PurePosixPath(directory)
    try:
        parts = directory_path.relative_to(root_path).parts
    except ValueError as exc:
        raise ForceOverwriteError(f"Upload path escaped the remote root: {directory}") from exc
    current = root_path
    for part in parts:
        current /= part
        current_text = str(current)
        if not _exists(sftp, current_text):
            sftp.mkdir(current_text, mode=mode)
        if not stat.S_ISDIR(sftp.stat(current_text).st_mode):
            raise ForceOverwriteError(f"Remote path is not a directory: {current_text}")


class ForceOverwriteEngine:
    def overwrite(
        self,
        *,
        sftp: ForceOverwriteRemoteFileSystem,
        local_root: Path,
        remote_root: str,
        exclude_patterns: tuple[str, ...] = (),
        protect_patterns: tuple[str, ...] = (),
        include_paths: tuple[str, ...] | None = None,
        file_mode: int = 0o644,
        directory_mode: int = 0o755,
        progress_callback: Callable[[int, int, str], None] | None = None,
        file_started_callback: Callable[[int, int, str], None] | None = None,
    ) -> ForceOverwriteResult:
        resolved_root = local_root.expanduser().resolve()
        normalized_root = normalize_remote_root(remote_root)
        if not _exists(sftp, normalized_root):
            raise ForceOverwriteError(f"Remote root does not exist: {normalized_root}")
        if not stat.S_ISDIR(sftp.stat(normalized_root).st_mode):
            raise ForceOverwriteError("Remote root is not a directory")
        all_files = _local_files(resolved_root, exclude_patterns, protect_patterns)
        if include_paths is None:
            files = all_files
        else:
            available = {
                path.relative_to(resolved_root).as_posix(): path for path in all_files
            }
            selected = tuple(dict.fromkeys(normalize_relative_path(path) for path in include_paths))
            missing = sorted(set(selected) - set(available))
            if missing:
                raise ForceOverwriteError(
                    "Selected local file is no longer available: " + ", ".join(missing)
                )
            files = tuple(available[path] for path in selected)
        copied: list[str] = []
        total_bytes = 0
        total = len(files)
        token = uuid.uuid4().hex[:12]
        for index, local_path in enumerate(files, start=1):
            relative = local_path.relative_to(resolved_root).as_posix()
            if file_started_callback is not None:
                file_started_callback(index - 1, total, relative)
            destination = str(PurePosixPath(normalized_root) / relative)
            temporary = destination + f".dxsync-force-{token}.tmp"
            old_temporary = destination + f".dxsync-force-{token}.old"
            _ensure_parents(
                sftp,
                normalized_root,
                str(PurePosixPath(destination).parent),
                directory_mode,
            )
            if _exists(sftp, temporary):
                sftp.remove(temporary)
            size = 0
            try:
                with local_path.open("rb") as reader, sftp.open(temporary, "wb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        writer.write(chunk)
                        size += len(chunk)
                sftp.chmod(temporary, file_mode)
                posix_rename = getattr(sftp, "posix_rename", None)
                if callable(posix_rename):
                    posix_rename(temporary, destination)
                else:
                    destination_existed = _exists(sftp, destination)
                    if destination_existed:
                        if _exists(sftp, old_temporary):
                            raise ForceOverwriteError(
                                f"Temporary rollback file already exists: {relative}"
                            )
                        sftp.rename(destination, old_temporary)
                    try:
                        sftp.rename(temporary, destination)
                    except Exception:
                        if destination_existed and _exists(sftp, old_temporary):
                            sftp.rename(old_temporary, destination)
                        raise
                    if destination_existed and _exists(sftp, old_temporary):
                        sftp.remove(old_temporary)
            except Exception:
                if _exists(sftp, temporary):
                    sftp.remove(temporary)
                raise
            copied.append(relative)
            total_bytes += size
            if progress_callback is not None:
                progress_callback(index, total, relative)
        return ForceOverwriteResult(normalized_root, tuple(copied), total_bytes)
