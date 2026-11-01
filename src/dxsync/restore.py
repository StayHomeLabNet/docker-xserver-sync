from __future__ import annotations

import hashlib
import json
import stat
import uuid
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
    def chmod(self, path: str, mode: int) -> None: ...


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


@dataclass
class RestoreAuthorization:
    backup_id: str
    destination_root: str
    approved: bool
    used: bool = False

    def consume(self, *, backup_id: str, destination_root: str) -> None:
        if not self.approved:
            raise RestoreError("Restore was not explicitly approved")
        if self.used:
            raise RestoreError("Restore approval has already been used")
        if self.backup_id != backup_id:
            raise RestoreError("Restore approval does not match the backup")
        if normalize_remote_root(self.destination_root) != normalize_remote_root(destination_root):
            raise RestoreError("Restore approval does not match the destination")
        self.used = True


@dataclass(frozen=True)
class _RestoreItem:
    relative_path: str
    destination: str
    backup_path: str
    backup_hash: str
    backup_size: int
    expected_current_hash: str
    new_temporary: str
    old_temporary: str
    mode: int


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
        authorization: RestoreAuthorization,
    ) -> RestoreResult:
        destination_root = normalize_remote_root(destination_root)
        manifest_path = str(PurePosixPath(manifest_path))
        try:
            with sftp.open(manifest_path, "rb") as stream:
                manifest = json.loads(stream.read().decode("utf-8"))
            backup_id = str(manifest["backup_id"])
            source_root = normalize_remote_root(str(manifest["source_root"]))
            manifest_files = list(manifest["files"])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RestoreError(f"Cannot read backup manifest: {exc}") from exc
        if source_root != destination_root:
            raise RestoreError("Backup source root does not match the restore destination")
        authorization.consume(backup_id=backup_id, destination_root=destination_root)

        manifest_directory = str(PurePosixPath(manifest_path).parent)
        token = f"{backup_id[:24]}-{uuid.uuid4().hex[:12]}"
        items: list[_RestoreItem] = []
        seen: set[str] = set()
        for record in manifest_files:
            try:
                relative = normalize_relative_path(str(record["relative_path"]))
                backup_hash = str(record["sha256"])
                backup_size = int(record["size"])
                backup_path = str(PurePosixPath(str(record["backup_path"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise RestoreError(f"Invalid backup file record: {exc}") from exc
            if relative in seen:
                raise RestoreError(f"Duplicate backup path: {relative}")
            seen.add(relative)
            if not backup_path.startswith(manifest_directory + "/"):
                raise RestoreError(f"Backup file is outside its job directory: {relative}")
            expected_current = expected_current_hashes.get(relative)
            if not expected_current:
                raise RestoreError(f"Missing expected current hash: {relative}")
            destination = str(PurePosixPath(destination_root) / relative)
            if not _exists(sftp, destination):
                raise RestoreError(f"Current destination file is missing: {relative}")
            current_hash, _ = _hash_remote(sftp, destination)
            if current_hash != expected_current:
                raise RestoreError(f"Current file was externally modified: {relative}")
            actual_hash, actual_size = _hash_remote(sftp, backup_path)
            if actual_hash != backup_hash or actual_size != backup_size:
                raise RestoreError(f"Backup file verification failed: {relative}")
            new_temporary = destination + f".dxsync-restore-{token}.new"
            old_temporary = destination + f".dxsync-restore-{token}.old"
            if _exists(sftp, new_temporary) or _exists(sftp, old_temporary):
                raise RestoreError(f"Restore temporary file already exists: {relative}")
            items.append(_RestoreItem(
                relative, destination, backup_path, backup_hash, backup_size,
                expected_current, new_temporary, old_temporary,
                stat.S_IMODE(sftp.stat(destination).st_mode),
            ))

        prepared: list[_RestoreItem] = []
        try:
            for item in items:
                with sftp.open(item.backup_path, "rb") as reader, sftp.open(
                    item.new_temporary, "wb"
                ) as writer:
                    while chunk := reader.read(1024 * 1024):
                        writer.write(chunk)
                sftp.chmod(item.new_temporary, item.mode)
                copied_hash, copied_size = _hash_remote(sftp, item.new_temporary)
                if copied_hash != item.backup_hash or copied_size != item.backup_size:
                    raise RestoreError(f"Restore copy verification failed: {item.relative_path}")
                prepared.append(item)
        except Exception:
            self._cleanup_new(sftp, items)
            raise

        applied: list[_RestoreItem] = []
        try:
            for item in prepared:
                current_hash, _ = _hash_remote(sftp, item.destination)
                if current_hash != item.expected_current_hash:
                    raise RestoreError(f"Current file changed during restore: {item.relative_path}")
                sftp.rename(item.destination, item.old_temporary)
                applied.append(item)
                sftp.rename(item.new_temporary, item.destination)
                restored_hash, restored_size = _hash_remote(sftp, item.destination)
                if restored_hash != item.backup_hash or restored_size != item.backup_size:
                    raise RestoreError(f"Restored file verification failed: {item.relative_path}")
        except Exception as exc:
            errors = self._rollback(sftp, applied, prepared)
            if errors:
                raise RestoreError(
                    f"Restore failed and rollback needs attention: {exc}; " + "; ".join(errors)
                ) from exc
            raise

        for item in applied:
            if _exists(sftp, item.old_temporary):
                sftp.remove(item.old_temporary)
        return RestoreResult(
            backup_id=backup_id,
            destination_root=destination_root,
            files=tuple(RestoreFile(item.relative_path, item.backup_hash, item.backup_size) for item in applied),
            total_bytes=sum(item.backup_size for item in applied),
        )

    @staticmethod
    def _cleanup_new(sftp: RestoreRemoteFileSystem, items: list[_RestoreItem]) -> None:
        for item in items:
            if _exists(sftp, item.new_temporary):
                sftp.remove(item.new_temporary)

    @staticmethod
    def _rollback(
        sftp: RestoreRemoteFileSystem,
        applied: list[_RestoreItem],
        prepared: list[_RestoreItem],
    ) -> list[str]:
        errors: list[str] = []
        for item in reversed(applied):
            try:
                if _exists(sftp, item.destination):
                    sftp.remove(item.destination)
                if _exists(sftp, item.old_temporary):
                    sftp.rename(item.old_temporary, item.destination)
            except Exception as exc:
                errors.append(f"{item.relative_path}: {exc}")
        for item in prepared:
            try:
                if _exists(sftp, item.new_temporary):
                    sftp.remove(item.new_temporary)
            except Exception as exc:
                errors.append(f"temporary {item.relative_path}: {exc}")
        return errors
