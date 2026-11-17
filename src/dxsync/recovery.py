from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Callable, Protocol

from .cancellation import CancellationToken
from .validation import normalize_remote_root


class RecoveryRemoteFileSystem(Protocol):
    def listdir_attr(self, path: str) -> list[Any]: ...
    def open(self, path: str, mode: str = "r") -> BinaryIO: ...
    def stat(self, path: str) -> Any: ...


@dataclass(frozen=True)
class RecoveryScanResult:
    remote_root: str
    temporary_files: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryArtifact:
    temporary_path: str
    destination_path: str
    kind: str
    temporary_sha256: str
    temporary_size: int
    destination_sha256: str | None
    destination_size: int | None
    assessment: str
    recommendation: str


@dataclass(frozen=True)
class RecoveryAnalysisResult:
    remote_root: str
    artifacts: tuple[RecoveryArtifact, ...]
    issues: tuple[str, ...]


def is_dxsync_temporary_name(filename: str) -> bool:
    return ".dxsync-" in filename and filename.endswith((".new", ".old", ".tmp"))


def scan_recovery_artifacts(
    sftp: RecoveryRemoteFileSystem,
    remote_root: str,
    *,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
    cancellation_token: CancellationToken | None = None,
) -> RecoveryScanResult:
    """Find recovery artifacts without reading, changing, or deleting remote files."""
    cancel = cancellation_token or CancellationToken()
    report = progress_callback or (lambda _current, _total, _detail: None)
    root = normalize_remote_root(remote_root)
    pending = [(root, "")]
    found: list[str] = []
    issues: list[str] = []
    scanned_directories = 0
    report(0, None, "XServerのフォルダを列挙中")
    while pending:
        cancel.raise_if_cancelled()
        absolute_directory, relative_directory = pending.pop()
        scanned_directories += 1
        report(scanned_directories, None, relative_directory or ".")
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


def _hash_remote(
    sftp: RecoveryRemoteFileSystem,
    path: str,
    cancellation_token: CancellationToken | None = None,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with sftp.open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _exists_regular(sftp: RecoveryRemoteFileSystem, path: str) -> bool:
    try:
        return stat.S_ISREG(sftp.stat(path).st_mode)
    except OSError:
        return False


def analyze_recovery_artifacts(
    sftp: RecoveryRemoteFileSystem,
    remote_root: str,
    *,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
    cancellation_token: CancellationToken | None = None,
) -> RecoveryAnalysisResult:
    cancel = cancellation_token or CancellationToken()
    report = progress_callback or (lambda _current, _total, _detail: None)
    scan = scan_recovery_artifacts(
        sftp,
        remote_root,
        progress_callback=progress_callback,
        cancellation_token=cancel,
    )
    root = PurePosixPath(scan.remote_root)
    artifacts: list[RecoveryArtifact] = []
    issues = list(scan.issues)
    total = len(scan.temporary_files)
    if total > 0:
        report(0, total, scan.temporary_files[0])
    for index, temporary_relative in enumerate(scan.temporary_files, start=1):
        cancel.raise_if_cancelled()
        marker = temporary_relative.rfind(".dxsync-")
        kind = temporary_relative.rsplit(".", 1)[-1]
        destination_relative = temporary_relative[:marker]
        temporary_path = str(root / temporary_relative)
        destination_path = str(root / destination_relative)
        try:
            temporary_hash, temporary_size = _hash_remote(
                sftp,
                temporary_path,
                cancellation_token=cancel,
            )
            destination_hash = None
            destination_size = None
            if _exists_regular(sftp, destination_path):
                destination_hash, destination_size = _hash_remote(
                    sftp,
                    destination_path,
                    cancellation_token=cancel,
                )
            same = destination_hash == temporary_hash and destination_hash is not None
            if kind == "new" and same:
                assessment = "公開済みコピーと一致"
                recommendation = "一時ファイルは削除候補です"
            elif kind == "new" and destination_hash is None:
                assessment = "公開切り替え前に中断"
                recommendation = "自動操作せず、計画とバックアップを確認してください"
            elif kind == "new":
                assessment = "公開ファイルと内容が異なる未公開コピー"
                recommendation = "自動操作せず、どちらを採用するか確認してください"
            elif kind == "old" and same:
                assessment = "公開済みファイルと旧退避が一致"
                recommendation = "旧退避は削除候補です"
            elif kind == "old" and destination_hash is None:
                assessment = "公開ファイルがなく旧退避だけ存在"
                recommendation = "旧退避からの復帰候補です。内容確認が必要です"
            elif kind == "old":
                assessment = "公開ファイルと異なる旧退避"
                recommendation = "ロールバック候補として保持してください"
            elif same:
                assessment = "対象ファイルと一致する一時コピー"
                recommendation = "一時ファイルは削除候補です"
            else:
                assessment = "未完了の一時コピー"
                recommendation = "自動操作せず、作成元の操作を確認してください"
            artifacts.append(
                RecoveryArtifact(
                    temporary_relative,
                    destination_relative,
                    kind,
                    temporary_hash,
                    temporary_size,
                    destination_hash,
                    destination_size,
                    assessment,
                    recommendation,
                )
            )
        except OSError as exc:
            issues.append(f"{temporary_relative}: {exc}")
        report(index, total, temporary_relative)
    return RecoveryAnalysisResult(scan.remote_root, tuple(artifacts), tuple(sorted(issues)))
