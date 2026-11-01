from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Protocol

from .models import ChangeType, SyncPlan
from .validation import normalize_remote_root


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
) -> None:
    current = PurePosixPath("/")
    for part in PurePosixPath(path).parts[1:]:
        current /= part
        current_text = str(current)
        if not _exists(sftp, current_text):
            sftp.mkdir(current_text, mode=mode)
        remote_stat = sftp.stat(current_text)
        if not stat.S_ISDIR(remote_stat.st_mode):
            raise BackupError(f"Backup path component is not a directory: {current_text}")


def _hash_remote(sftp: WritableRemoteFileSystem, path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with sftp.open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
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
    ) -> BackupResult:
        project_id = _safe_identifier(project_id, "project identifier")
        backup_id = _safe_identifier(plan.plan_id, "backup identifier")
        source_root = normalize_remote_root(remote_root)
        normalized_backup_root = normalize_remote_root(backup_root)
        if normalized_backup_root == source_root or normalized_backup_root.startswith(
            source_root + "/"
        ):
            raise BackupError("Backup root must be outside the deployment root")

        backup_directory = str(
            PurePosixPath(normalized_backup_root) / project_id / backup_id
        )
        if _exists(sftp, backup_directory):
            raise BackupError(f"Backup directory already exists: {backup_directory}")
        _mkdir_parents(sftp, backup_directory, mode=0o700)

        backed_up: list[BackupFile] = []
        total_bytes = 0
        for change in plan.changes:
            if change.change_type is not ChangeType.UPDATE:
                continue
            if change.remote is None or not change.remote.sha256:
                raise BackupError(f"Missing approved remote hash: {change.relative_path}")
            relative = change.remote.relative_path
            source = str(PurePosixPath(source_root) / relative)
            destination = str(PurePosixPath(backup_directory) / relative)
            temporary = destination + f".dxsync-{backup_id}.tmp"
            _mkdir_parents(sftp, str(PurePosixPath(destination).parent), mode=0o700)

            digest = hashlib.sha256()
            size = 0
            try:
                with sftp.open(source, "rb") as reader, sftp.open(temporary, "wb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        writer.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                copied_hash = digest.hexdigest()
                if copied_hash != change.remote.sha256:
                    raise BackupError(f"Remote source changed before backup: {relative}")
                sftp.rename(temporary, destination)
            except Exception:
                if _exists(sftp, temporary):
                    sftp.remove(temporary)
                raise

            verified_hash, verified_size = _hash_remote(sftp, destination)
            if verified_hash != change.remote.sha256 or verified_size != size:
                raise BackupError(f"Backup verification failed: {relative}")
            backed_up.append(BackupFile(relative, verified_hash, size, destination))
            total_bytes += size

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
