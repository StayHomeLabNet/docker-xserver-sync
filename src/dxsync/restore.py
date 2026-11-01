from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Protocol

from .models import normalize_relative_path
from .validation import normalize_remote_root


class RestoreError(RuntimeError):
    pass


class RestoreRemoteFileSystem(Protocol):
    def open(self, path: str, mode: str = "r") -> BinaryIO: ...
    def stat(self, path: str) -> Any: ...
    def remove(self, path: str) -> None: ...
    def rename(self, oldpath: str, newpath: str) -> None: ...


@dataclass(frozen=True)
class RestoreFile:
    relative_path: str
    restored_sha256: str
    size: int


@dataclass(frozen=True)
class RestoreResult:
    backup_id: str
    destination_root: str
    files: tuple[RestoreFile, ...]
    total_bytes: int


def _exists(sftp: RestoreRemoteFileSystem, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def _hash_remote(sftp: RestoreRemoteFileSystem, path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with sftp.open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class RemoteRestoreEngine:
    def restore(
        self,
        *,
        sftp: RestoreRemoteFileSystem,
        manifest_path: str,
        destination_root: str,
        expected_current_hashes: dict[str, str],
    ) -> RestoreResult:
        normalized_destination = normalize_remote_root(destination_root)
        normalized_manifest = str(PurePosixPath(manifest_path))
        try:
            with sftp.open(normalized_manifest, "rb") as stream:
                manifest = json.loads(stream.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
            raise RestoreError(f"Cannot read backup manifest: {exc}") from exc

        try:
            backup_id = str(manifest["backup_id"])
            source_root = normalize_remote_root(str(manifest["source_root"]))
            manifest_files = list(manifest["files"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RestoreError(f"Invalid backup manifest: {exc}") from exc
        if source_root != normalized_destination:
            raise RestoreError("Backup source root does not match the restore destination")

        manifest_directory = str(PurePosixPath(normalized_manifest).parent)
        approved: list[tuple[str, str, str, int]] = []
        manifest_paths: set[str] = set()
        for item in manifest_files:
            try:
                relative = normalize_relative_path(str(item["relative_path"]))
                backup_hash = str(item["sha256"])
                backup_size = int(item["size"])
                backup_path = str(PurePosixPath(str(item["backup_path"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise RestoreError(f"Invalid backup file record: {exc}") from exc
            if relative in manifest_paths:
                raise RestoreError(f"Duplicate backup path: {relative}")
            manifest_paths.add(relative)
            if not (
                backup_path == manifest_directory
                or backup_path.startswith(manifest_directory + "/")
            ):
                raise RestoreError(f"Backup file is outside its job directory: {relative}")
            expected_current = expected_current_hashes.get(relative)
            if not expected_current:
                raise RestoreError(f"Missing expected current hash: {relative}")
            destination = str(PurePosixPath(normalized_destination) / relative)
            if not _exists(sftp, destination):
                raise RestoreError(f"Current destination file is missing: {relative}")
            current_hash, _ = _hash_remote(sftp, destination)
            if current_hash != expected_current:
                raise RestoreError(f"Current file was externally modified: {relative}")
            actual_backup_hash, actual_backup_size = _hash_remote(sftp, backup_path)
            if actual_backup_hash != backup_hash or actual_backup_size != backup_size:
                raise RestoreError(f"Backup file verification failed: {relative}")
            approved.append((relative, destination, backup_path, backup_size))

        restored: list[RestoreFile] = []
        total_bytes = 0
        for relative, destination, backup_path, backup_size in approved:
            expected_current = expected_current_hashes[relative]
            current_hash, _ = _hash_remote(sftp, destination)
            if current_hash != expected_current:
                raise RestoreError(f"Current file changed during restore: {relative}")

            new_temporary = destination + f".dxsync-restore-{backup_id}.new"
            old_temporary = destination + f".dxsync-restore-{backup_id}.old"
            if _exists(sftp, new_temporary) or _exists(sftp, old_temporary):
                raise RestoreError(f"Restore temporary file already exists: {relative}")
            digest = hashlib.sha256()
            size = 0
            try:
                with sftp.open(backup_path, "rb") as reader, sftp.open(
                    new_temporary, "wb"
                ) as writer:
                    while chunk := reader.read(1024 * 1024):
                        writer.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                expected_backup_hash = next(
                    str(item["sha256"])
                    for item in manifest_files
                    if str(item["relative_path"]) == relative
                )
                if digest.hexdigest() != expected_backup_hash or size != backup_size:
                    raise RestoreError(f"Restore copy verification failed: {relative}")

                sftp.rename(destination, old_temporary)
                try:
                    sftp.rename(new_temporary, destination)
                    restored_hash, restored_size = _hash_remote(sftp, destination)
                    if restored_hash != expected_backup_hash or restored_size != backup_size:
                        raise RestoreError(f"Restored file verification failed: {relative}")
                except Exception:
                    if _exists(sftp, destination):
                        sftp.remove(destination)
                    sftp.rename(old_temporary, destination)
                    raise
                sftp.remove(old_temporary)
            except Exception:
                if _exists(sftp, new_temporary):
                    sftp.remove(new_temporary)
                raise

            restored.append(RestoreFile(relative, digest.hexdigest(), size))
            total_bytes += size

        return RestoreResult(
            backup_id=backup_id,
            destination_root=normalized_destination,
            files=tuple(restored),
            total_bytes=total_bytes,
        )
