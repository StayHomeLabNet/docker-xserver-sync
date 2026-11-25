from __future__ import annotations

import errno
import hashlib
import json
import re
import stat
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, Callable, Protocol, Sequence

from .cancellation import CancellationToken
from .models import ChangeType, SyncPlan
from .validation import normalize_remote_root

if TYPE_CHECKING:
    from .storage import BackupRecord


MANIFEST_NAME = ".dxsync-backup-manifest.json"


class BackupError(RuntimeError):
    pass


class WritableRemoteFileSystem(Protocol):
    def open(self, path: str, mode: str = "r") -> BinaryIO: ...
    def stat(self, path: str) -> Any: ...
    def mkdir(self, path: str, mode: int = 0o755) -> None: ...
    def remove(self, path: str) -> None: ...
    def rename(self, oldpath: str, newpath: str) -> None: ...


@dataclass(frozen=True)
class BackupFile:
    relative_path: str
    sha256: str
    size: int
    backup_path: str
    change_type: ChangeType = ChangeType.UPDATE
    mode: int = 0o644


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    backup_directory: str
    manifest_path: str
    files: tuple[BackupFile, ...]
    total_bytes: int
    created_at: datetime


def _safe_identifier(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise BackupError(f"Unsafe {label}: {value!r}")
    return value


def _exists(sftp: WritableRemoteFileSystem, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def _mkdir_parents(
    sftp: WritableRemoteFileSystem,
    path: str,
    *,
    mode: int = 0o700,
    verified_dirs: set[str] | None = None,
) -> None:
    current = PurePosixPath("/")
    for part in PurePosixPath(path).parts[1:]:
        current /= part
        current_text = str(current)
        if verified_dirs is not None and current_text in verified_dirs:
            continue
        if not _exists(sftp, current_text):
            sftp.mkdir(current_text, mode=mode)
        remote_stat = sftp.stat(current_text)
        if not stat.S_ISDIR(remote_stat.st_mode):
            raise BackupError(f"Backup path component is not a directory: {current_text}")
        if verified_dirs is not None:
            verified_dirs.add(current_text)


def _hash_remote(
    sftp: WritableRemoteFileSystem,
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


class RemoteBackupEngine:
    def create_backup(
        self,
        *,
        sftp: WritableRemoteFileSystem,
        plan: SyncPlan,
        project_id: str,
        remote_root: str,
        backup_root: str,
        delete_paths: tuple[str, ...] = (),
        progress_callback: Callable[[int, int, str], None] | None = None,
        cancellation_token: CancellationToken | None = None,
        sftp_factory: Callable[[], Any] | None = None,
        max_workers: int = 1,
    ) -> BackupResult:
        cancel = cancellation_token or CancellationToken()
        cancel.raise_if_cancelled()
        project_id = _safe_identifier(project_id, "project identifier")
        backup_id = _safe_identifier(plan.plan_id, "backup identifier")
        source_root = normalize_remote_root(remote_root)
        normalized_backup_root = normalize_remote_root(backup_root)
        if normalized_backup_root == source_root or normalized_backup_root.startswith(
            source_root + "/"
        ):
            raise BackupError("Backup root must be outside the deployment root")

        remote_only = {
            change.relative_path: change
            for change in plan.changes
            if change.change_type is ChangeType.REMOTE_ONLY
        }
        requested_deletions = set(delete_paths)
        unknown_deletions = requested_deletions - set(remote_only)
        if unknown_deletions:
            raise BackupError("Deletion approval contains files outside the plan")

        backup_directory = str(
            PurePosixPath(normalized_backup_root) / project_id / backup_id
        )
        if _exists(sftp, backup_directory):
            raise BackupError(f"Backup directory already exists: {backup_directory}")
        _mkdir_parents(sftp, backup_directory, mode=0o700)

        backup_targets = [
            change
            for change in plan.changes
            if change.change_type is ChangeType.UPDATE
            or (
                change.change_type is ChangeType.REMOTE_ONLY
                and change.relative_path in requested_deletions
            )
        ]
        total_targets = len(backup_targets)
        if progress_callback is not None:
            progress_callback(
                0,
                total_targets,
                backup_targets[0].relative_path if backup_targets else "",
            )

        from .cancellation import OperationCancelled

        backed_up: list[BackupFile] = []
        total_bytes = 0

        def backup_one(change: Any, thread_sftp: Any) -> BackupFile:
            cancel.raise_if_cancelled()
            if change.remote is None or not change.remote.sha256:
                raise BackupError(f"Missing approved remote hash: {change.relative_path}")
            relative = change.remote.relative_path
            source = str(PurePosixPath(source_root) / relative)
            destination = str(PurePosixPath(backup_directory) / relative)
            temporary = destination + f".dxsync-{backup_id}.tmp"

            digest = hashlib.sha256()
            size = 0
            try:
                with thread_sftp.open(source, "rb") as reader, thread_sftp.open(temporary, "wb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        cancel.raise_if_cancelled()
                        writer.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                copied_hash = digest.hexdigest()
                if copied_hash != change.remote.sha256:
                    raise BackupError(f"Remote source changed before backup: {relative}")
                thread_sftp.rename(temporary, destination)
            except Exception:
                if _exists(thread_sftp, temporary):
                    thread_sftp.remove(temporary)
                raise

            verified_hash, verified_size = _hash_remote(
                thread_sftp,
                destination,
                cancellation_token=cancel,
            )
            if verified_hash != change.remote.sha256 or verified_size != size:
                raise BackupError(f"Backup verification failed: {relative}")
            mode = stat.S_IMODE(thread_sftp.stat(source).st_mode)
            return BackupFile(
                relative,
                verified_hash,
                size,
                destination,
                change.change_type,
                mode,
            )

        try:
            verified_dirs: set[str] = set()
            for change in backup_targets:
                cancel.raise_if_cancelled()
                if change.remote is None or not change.remote.sha256:
                    raise BackupError(f"Missing approved remote hash: {change.relative_path}")
                destination = str(PurePosixPath(backup_directory) / change.remote.relative_path)
                _mkdir_parents(
                    sftp,
                    str(PurePosixPath(destination).parent),
                    mode=0o700,
                    verified_dirs=verified_dirs,
                )

            if max_workers > 1 and sftp_factory is not None and len(backup_targets) > 1:
                thread_local = threading.local()
                progress_lock = threading.Lock()
                completed_count = 0
                backed_up_by_index: dict[int, BackupFile] = {}

                def run_backup(index: int, change: Any) -> tuple[int, BackupFile]:
                    thread_sftp = getattr(thread_local, "sftp", None)
                    if thread_sftp is None:
                        thread_sftp = sftp_factory()
                        thread_local.sftp = thread_sftp
                    return index, backup_one(change, thread_sftp)

                with ThreadPoolExecutor(
                    max_workers=min(max_workers, len(backup_targets))
                ) as executor:
                    futures = [
                        executor.submit(run_backup, idx, change)
                        for idx, change in enumerate(backup_targets)
                    ]
                    for future in as_completed(futures):
                        idx, item = future.result()
                        backed_up_by_index[idx] = item
                        with progress_lock:
                            completed_count += 1
                            if progress_callback is not None:
                                progress_callback(completed_count, total_targets, item.relative_path)
                backed_up = [backed_up_by_index[i] for i in range(len(backup_targets))]
                total_bytes = sum(item.size for item in backed_up)
            else:
                for index, change in enumerate(backup_targets, start=1):
                    item = backup_one(change, sftp)
                    backed_up.append(item)
                    total_bytes += item.size
                    if progress_callback is not None:
                        progress_callback(index, total_targets, item.relative_path)
        except OperationCancelled:
            for item in backed_up:
                try:
                    if _exists(sftp, item.backup_path):
                        sftp.remove(item.backup_path)
                except OSError:
                    pass
            rmdir = getattr(sftp, "rmdir", None)
            if callable(rmdir):
                try:
                    rmdir(backup_directory)
                except OSError:
                    pass
            raise

        created_at = datetime.now(timezone.utc)
        manifest_path = str(PurePosixPath(backup_directory) / MANIFEST_NAME)
        manifest_temporary = manifest_path + f".dxsync-{backup_id}.tmp"
        manifest = {
            "backup_id": backup_id,
            "plan_id": plan.plan_id,
            "project_id": project_id,
            "created_at": created_at.isoformat(),
            "source_root": source_root,
            "files": [
                {
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                    "size": item.size,
                    "backup_path": item.backup_path,
                    "change_type": item.change_type.value,
                    "mode": item.mode,
                }
                for item in backed_up
            ],
        }
        encoded_manifest = json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        try:
            with sftp.open(manifest_temporary, "wb") as stream:
                stream.write(encoded_manifest)
            sftp.rename(manifest_temporary, manifest_path)
        except Exception:
            if _exists(sftp, manifest_temporary):
                sftp.remove(manifest_temporary)
            raise
        manifest_hash, manifest_size = _hash_remote(sftp, manifest_path)
        expected_manifest_hash = hashlib.sha256(encoded_manifest).hexdigest()
        if manifest_size != len(encoded_manifest) or manifest_hash != expected_manifest_hash:
            raise BackupError("Backup manifest verification failed")

        return BackupResult(
            backup_id=backup_id,
            backup_directory=backup_directory,
            manifest_path=manifest_path,
            files=tuple(backed_up),
            total_bytes=total_bytes,
            created_at=created_at,
        )


def select_prune_candidates(
    backups: Sequence["BackupRecord"],
    *,
    keep_latest: int = 10,
    max_age_days: int = 30,
    now: datetime | None = None,
) -> tuple["BackupRecord", ...]:
    if keep_latest < 0 or max_age_days < 0:
        raise ValueError("keep_latest and max_age_days must be zero or positive")
    if keep_latest == 0 and max_age_days == 0:
        return ()
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)

    active = sorted(
        (backup for backup in backups if backup.status in {"available", "restored"}),
        key=lambda item: item.created_at,
        reverse=True,
    )
    max_age = timedelta(days=max_age_days) if max_age_days > 0 else None
    candidates: list["BackupRecord"] = []
    for index, backup in enumerate(active):
        if keep_latest > 0 and index < keep_latest:
            continue
        created_at = backup.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if max_age is not None and (reference_now - created_at) <= max_age:
            continue
        candidates.append(backup)
    return tuple(candidates)


def validate_backup_directory_for_delete(backup_directory: str) -> str:
    normalized = normalize_remote_root(backup_directory)
    parts = PurePosixPath(normalized).parts
    if ".dxsync" not in parts:
        raise BackupError(
            f"Refusing to delete directory outside .dxsync/backups: {backup_directory}"
        )
    idx = parts.index(".dxsync")
    if idx + 3 >= len(parts) or parts[idx + 1] != "backups":
        raise BackupError(
            f"Refusing to delete non-backup directory: {backup_directory}"
        )
    return normalized


def delete_remote_backup(
    sftp: Any,
    backup_directory: str,
    cancellation_token: CancellationToken | None = None,
) -> bool:
    cancel = cancellation_token or CancellationToken()
    cancel.raise_if_cancelled()
    normalized = validate_backup_directory_for_delete(backup_directory)

    lstat_fn = getattr(sftp, "lstat", None) or sftp.stat
    try:
        root_stat = lstat_fn(normalized)
    except FileNotFoundError:
        return False
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ENOENT:
            return False
        raise
    if stat.S_ISLNK(root_stat.st_mode):
        raise BackupError(f"Refusing to delete symbolic link: {normalized}")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise BackupError(f"Backup path is not a directory: {normalized}")

    def _remove_dir_recursive(path: str) -> None:
        cancel.raise_if_cancelled()
        listdir_attr = sftp.listdir_attr
        for entry in listdir_attr(path):
            cancel.raise_if_cancelled()
            name = getattr(entry, "filename", "")
            if name in {"", ".", ".."}:
                continue
            child = f"{path}/{name}"
            mode = entry.st_mode
            if stat.S_ISLNK(mode):
                raise BackupError(f"Refusing to delete symbolic link inside backup: {child}")
            if stat.S_ISDIR(mode):
                _remove_dir_recursive(child)
            else:
                sftp.remove(child)
        rmdir_fn = getattr(sftp, "rmdir", None)
        if callable(rmdir_fn):
            rmdir_fn(path)

    _remove_dir_recursive(normalized)
    return True

