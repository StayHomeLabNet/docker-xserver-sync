from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from .cancellation import CancellationToken
from .models import EntryKind, FileRecord, ScanIssue, ScanResult
from .rules import PathRules


def sha256_file(
    path: Path,
    chunk_size: int = 1024 * 1024,
    cancellation_token: CancellationToken | None = None,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            digest.update(chunk)
    return digest.hexdigest()


class LocalScanner:
    def __init__(
        self,
        exclude_patterns: tuple[str, ...] = (),
        skip_patterns: tuple[str, ...] = (),
    ) -> None:
        self._excludes = PathRules(exclude_patterns)
        self._skips = PathRules(skip_patterns)

    def scan(
        self,
        root: Path,
        *,
        progress_callback: Callable[[int, int | None, str], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> ScanResult:
        resolved_root = root.expanduser().resolve()
        if not resolved_root.is_dir():
            raise ValueError(f"Local root is not a readable directory: {resolved_root}")

        files: list[FileRecord] = []
        issues: list[ScanIssue] = []
        excluded_count = 0
        skipped_protected_paths: list[str] = []
        candidates: list[tuple[Path, str]] = []
        scanned_directories = 0
        report = progress_callback or (lambda _current, _total, _detail: None)
        report(0, None, "ローカルファイルを列挙中")

        for current, directory_names, file_names in os.walk(resolved_root, followlinks=False):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            current_path = Path(current)
            scanned_directories += 1
            report(
                scanned_directories,
                None,
                current_path.relative_to(resolved_root).as_posix() or ".",
            )
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                absolute = current_path / name
                relative = absolute.relative_to(resolved_root).as_posix()
                if absolute.is_symlink():
                    issues.append(
                        ScanIssue(relative, "symlink", "Symbolic links are not supported")
                    )
                    continue
                if self._skips.matches(relative, is_directory=True):
                    skipped_protected_paths.append(relative)
                    continue
                if self._excludes.matches(relative, is_directory=True):
                    excluded_count += 1
                    continue
                kept_directories.append(name)
            directory_names[:] = kept_directories

            for name in sorted(file_names):
                absolute = current_path / name
                relative = absolute.relative_to(resolved_root).as_posix()
                if self._skips.matches(relative):
                    skipped_protected_paths.append(relative)
                    continue
                if self._excludes.matches(relative):
                    excluded_count += 1
                    continue
                if absolute.is_symlink():
                    issues.append(
                        ScanIssue(relative, "symlink", "Symbolic links are not supported")
                    )
                    continue
                candidates.append((absolute, relative))

        total = len(candidates)
        report(0, total, "")
        for index, (absolute, relative) in enumerate(candidates, start=1):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            try:
                stat = absolute.stat()
                if not absolute.is_file():
                    issues.append(
                        ScanIssue(
                            relative,
                            "unsupported_type",
                            "Only regular files are supported",
                        )
                    )
                else:
                    files.append(
                        FileRecord(
                            relative_path=relative,
                            kind=EntryKind.FILE,
                            size=stat.st_size,
                            modified_ns=stat.st_mtime_ns,
                            sha256=sha256_file(
                                absolute,
                                cancellation_token=cancellation_token,
                            ),
                        )
                    )
            except (OSError, PermissionError) as exc:
                issues.append(ScanIssue(relative, "unreadable", str(exc)))
            report(index, total, relative)

        return ScanResult(
            root=resolved_root,
            files=tuple(sorted(files, key=lambda item: item.relative_path)),
            issues=tuple(issues),
            excluded_count=excluded_count,
            skipped_protected_paths=tuple(sorted(skipped_protected_paths)),
        )

