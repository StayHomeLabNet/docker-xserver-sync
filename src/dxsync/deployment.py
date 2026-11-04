from __future__ import annotations

import hashlib
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Protocol

from .health import HealthCheckResult
from .models import ChangeType, FileChange, SyncPlan
from .validation import normalize_remote_root


class DeployError(RuntimeError):
    pass


class DeployRemoteFileSystem(Protocol):
    def open(self, path: str, mode: str = "r") -> BinaryIO: ...
    def stat(self, path: str) -> Any: ...
    def mkdir(self, path: str, mode: int = 0o755) -> None: ...
    def remove(self, path: str) -> None: ...
    def rename(self, oldpath: str, newpath: str) -> None: ...
    def rmdir(self, path: str) -> None: ...
    def chmod(self, path: str, mode: int) -> None: ...


@dataclass(frozen=True)
class DeployedFile:
    relative_path: str
    change_type: ChangeType
    sha256: str
    size: int


@dataclass(frozen=True)
class DeploymentResult:
    plan_id: str
    remote_root: str
    files: tuple[DeployedFile, ...]
    total_bytes: int
    health_check: HealthCheckResult | None = None
    deleted_files: tuple[str, ...] = ()


@dataclass
class DeploymentAuthorization:
    plan_id: str
    backup_plan_id: str | None
    remote_root: str
    approved: bool
    backup_skipped: bool = False
    delete_paths: tuple[str, ...] = ()
    used: bool = False

    def consume(self, *, plan_id: str, remote_root: str) -> None:
        normalized_root = normalize_remote_root(remote_root)
        if not self.approved:
            raise DeployError("Deployment was not explicitly approved")
        if self.used:
            raise DeployError("Deployment approval has already been used")
        if self.plan_id != plan_id:
            raise DeployError("Deployment approval does not match the plan")
        if self.backup_skipped:
            if self.backup_plan_id is not None:
                raise DeployError("Skipped backup approval contains an unexpected backup")
            if self.delete_paths:
                raise DeployError("Files cannot be deleted when backup is skipped")
        elif self.backup_plan_id != plan_id:
            raise DeployError("Deployment approval does not match the plan and backup")
        if normalize_remote_root(self.remote_root) != normalized_root:
            raise DeployError("Deployment approval does not match the remote root")
        self.used = True


@dataclass(frozen=True)
class _PreparedFile:
    change: FileChange
    destination: str
    new_temporary: str
    old_temporary: str | None
    sha256: str
    size: int
    mode: int


@dataclass(frozen=True)
class _PreparedDeletion:
    change: FileChange
    destination: str
    old_temporary: str


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]", "", value)
    if not token:
        raise DeployError("Plan identifier cannot be used for temporary files")
    return token[:48]


def _exists(sftp: DeployRemoteFileSystem, path: str) -> bool:
    try:
        remote_stat = sftp.stat(path)
        if stat.S_ISLNK(remote_stat.st_mode):
            raise DeployError(f"Symbolic link is not allowed: {path}")
        return True
    except FileNotFoundError:
        return False


def _hash_remote(sftp: DeployRemoteFileSystem, path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with sftp.open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _hash_local(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _ensure_parents(
    sftp: DeployRemoteFileSystem,
    root: str,
    directory: str,
    created: list[str],
    mode: int,
) -> None:
    root_path = PurePosixPath(root)
    directory_path = PurePosixPath(directory)
    try:
        relative_parts = directory_path.relative_to(root_path).parts
    except ValueError as exc:
        raise DeployError(f"Upload path escaped the remote root: {directory}") from exc
    current = root_path
    for part in relative_parts:
        current /= part
        current_text = str(current)
        if not _exists(sftp, current_text):
            sftp.mkdir(current_text, mode=mode)
            created.append(current_text)
        if not stat.S_ISDIR(sftp.stat(current_text).st_mode):
            raise DeployError(f"Remote path is not a directory: {current_text}")


class RemoteDeploymentEngine:
    def deploy(
        self,
        *,
        sftp: DeployRemoteFileSystem,
        plan: SyncPlan,
        staging_root: Path,
        remote_root: str,
        authorization: DeploymentAuthorization,
        file_mode: int = 0o644,
        directory_mode: int = 0o755,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> DeploymentResult:
        normalized_root = normalize_remote_root(remote_root)
        authorization.consume(plan_id=plan.plan_id, remote_root=normalized_root)
        if not _exists(sftp, normalized_root):
            raise DeployError(f"Remote root does not exist: {normalized_root}")
        if not stat.S_ISDIR(sftp.stat(normalized_root).st_mode):
            raise DeployError("Remote root is not a directory")
        token = f"{_safe_token(plan.plan_id)[:24]}-{uuid.uuid4().hex[:12]}"
        prepared: list[_PreparedFile] = []
        prepared_deletions: list[_PreparedDeletion] = []
        created_directories: list[str] = []

        upload_changes = [
            change
            for change in plan.changes
            if change.change_type in {ChangeType.ADD, ChangeType.UPDATE}
        ]
        total_changes = len(upload_changes) + len(authorization.delete_paths)
        try:
            for index, change in enumerate(upload_changes, start=1):
                if change.local is None or not change.local.sha256:
                    raise DeployError(f"Missing approved local hash: {change.relative_path}")
                local_path = staging_root / Path(change.relative_path)
                if local_path.is_symlink() or not local_path.is_file():
                    raise DeployError(f"Staged file is missing or unsafe: {change.relative_path}")
                local_hash, local_size = _hash_local(local_path)
                if local_hash != change.local.sha256 or local_size != change.local.size:
                    raise DeployError(f"Staged file changed: {change.relative_path}")

                destination = str(PurePosixPath(normalized_root) / change.relative_path)
                parent = str(PurePosixPath(destination).parent)
                _ensure_parents(
                    sftp,
                    normalized_root,
                    parent,
                    created_directories,
                    directory_mode,
                )
                exists = _exists(sftp, destination)
                destination_mode = file_mode
                if change.change_type is ChangeType.ADD and exists:
                    raise DeployError(f"ADD destination already exists: {change.relative_path}")
                if change.change_type is ChangeType.UPDATE:
                    if not exists or change.remote is None or not change.remote.sha256:
                        raise DeployError(f"UPDATE destination is unavailable: {change.relative_path}")
                    current_hash, _ = _hash_remote(sftp, destination)
                    if current_hash != change.remote.sha256:
                        raise DeployError(f"Remote file changed before upload: {change.relative_path}")
                    destination_mode = stat.S_IMODE(sftp.stat(destination).st_mode)

                new_temporary = destination + f".dxsync-{token}.new"
                old_temporary = (
                    destination + f".dxsync-{token}.old"
                    if change.change_type is ChangeType.UPDATE
                    else None
                )
                if _exists(sftp, new_temporary) or (
                    old_temporary and _exists(sftp, old_temporary)
                ):
                    raise DeployError(f"Deployment temporary file already exists: {change.relative_path}")

                digest = hashlib.sha256()
                size = 0
                with local_path.open("rb") as reader, sftp.open(new_temporary, "wb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        writer.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                sftp.chmod(new_temporary, destination_mode)
                uploaded_hash, uploaded_size = _hash_remote(sftp, new_temporary)
                if (
                    digest.hexdigest() != change.local.sha256
                    or uploaded_hash != change.local.sha256
                    or uploaded_size != size
                ):
                    raise DeployError(f"Uploaded temporary file verification failed: {change.relative_path}")
                prepared.append(
                    _PreparedFile(
                        change,
                        destination,
                        new_temporary,
                        old_temporary,
                        uploaded_hash,
                        uploaded_size,
                        destination_mode,
                    )
                )
                if progress_callback is not None:
                    progress_callback(index, total_changes, f"転送準備: {change.relative_path}")
        except Exception:
            self._clean_prepared(sftp, prepared, created_directories)
            raise

        remote_only = {
            change.relative_path: change
            for change in plan.changes
            if change.change_type is ChangeType.REMOTE_ONLY
        }
        requested_deletions = set(authorization.delete_paths)
        if requested_deletions - set(remote_only):
            self._clean_prepared(sftp, prepared, created_directories)
            raise DeployError("Deletion approval contains files outside the plan")
        for delete_index, relative in enumerate(sorted(requested_deletions), start=1):
            change = remote_only[relative]
            if change.remote is None or not change.remote.sha256:
                self._clean_prepared(sftp, prepared, created_directories)
                raise DeployError(f"Missing approved remote hash: {relative}")
            destination = str(PurePosixPath(normalized_root) / relative)
            if not _exists(sftp, destination):
                self._clean_prepared(sftp, prepared, created_directories)
                raise DeployError(f"Deletion candidate is missing: {relative}")
            current_hash, _ = _hash_remote(sftp, destination)
            if current_hash != change.remote.sha256:
                self._clean_prepared(sftp, prepared, created_directories)
                raise DeployError(f"Deletion candidate changed: {relative}")
            old_temporary = destination + f".dxsync-{token}.old"
            if _exists(sftp, old_temporary):
                self._clean_prepared(sftp, prepared, created_directories)
                raise DeployError(f"Deletion temporary file already exists: {relative}")
            prepared_deletions.append(_PreparedDeletion(change, destination, old_temporary))
            if progress_callback is not None:
                progress_callback(
                    len(upload_changes) + delete_index,
                    total_changes,
                    f"削除準備: {relative}",
                )

        applied: list[_PreparedFile] = []
        applied_deletions: list[_PreparedDeletion] = []
        try:
            for publish_index, item in enumerate(prepared, start=1):
                if item.change.change_type is ChangeType.UPDATE:
                    assert item.old_temporary is not None
                    current_hash, _ = _hash_remote(sftp, item.destination)
                    if current_hash != item.change.remote.sha256:
                        raise DeployError(
                            f"Remote file changed during deployment: {item.change.relative_path}"
                        )
                    sftp.rename(item.destination, item.old_temporary)
                elif _exists(sftp, item.destination):
                    raise DeployError(
                        f"ADD destination appeared during deployment: {item.change.relative_path}"
                    )
                applied.append(item)
                sftp.rename(item.new_temporary, item.destination)
                deployed_hash, deployed_size = _hash_remote(sftp, item.destination)
                if deployed_hash != item.sha256 or deployed_size != item.size:
                    raise DeployError(f"Published file verification failed: {item.change.relative_path}")
                if progress_callback is not None:
                    progress_callback(
                        publish_index,
                        total_changes,
                        f"公開切り替え: {item.change.relative_path}",
                    )
            for delete_index, item in enumerate(prepared_deletions, start=1):
                assert item.change.remote is not None
                current_hash, _ = _hash_remote(sftp, item.destination)
                if current_hash != item.change.remote.sha256:
                    raise DeployError(
                        f"Deletion candidate changed during deployment: {item.change.relative_path}"
                    )
                sftp.rename(item.destination, item.old_temporary)
                applied_deletions.append(item)
                if progress_callback is not None:
                    progress_callback(
                        len(prepared) + delete_index,
                        total_changes,
                        f"削除適用: {item.change.relative_path}",
                    )
        except Exception as exc:
            rollback_errors = self._rollback(
                sftp,
                applied,
                prepared,
                created_directories,
                applied_deletions,
            )
            if rollback_errors:
                raise DeployError(
                    f"Deployment failed and rollback needs attention: {exc}; "
                    + "; ".join(rollback_errors)
                ) from exc
            raise

        for item in prepared:
            if item.old_temporary and _exists(sftp, item.old_temporary):
                sftp.remove(item.old_temporary)
        for item in prepared_deletions:
            if _exists(sftp, item.old_temporary):
                sftp.remove(item.old_temporary)

        files = tuple(
            DeployedFile(
                relative_path=item.change.relative_path,
                change_type=item.change.change_type,
                sha256=item.sha256,
                size=item.size,
            )
            for item in prepared
        )
        return DeploymentResult(
            plan_id=plan.plan_id,
            remote_root=normalized_root,
            files=files,
            total_bytes=sum(item.size for item in prepared),
            deleted_files=tuple(item.change.relative_path for item in prepared_deletions),
        )

    def _clean_prepared(
        self,
        sftp: DeployRemoteFileSystem,
        prepared: list[_PreparedFile],
        created_directories: list[str],
    ) -> None:
        for item in prepared:
            if _exists(sftp, item.new_temporary):
                sftp.remove(item.new_temporary)
        for directory in reversed(created_directories):
            try:
                sftp.rmdir(directory)
            except OSError:
                pass

    def _rollback(
        self,
        sftp: DeployRemoteFileSystem,
        applied: list[_PreparedFile],
        prepared: list[_PreparedFile],
        created_directories: list[str],
        applied_deletions: list[_PreparedDeletion] | None = None,
    ) -> list[str]:
        errors: list[str] = []
        for item in reversed(applied_deletions or []):
            try:
                if _exists(sftp, item.destination):
                    raise DeployError(f"Deletion destination reappeared: {item.change.relative_path}")
                if _exists(sftp, item.old_temporary):
                    sftp.rename(item.old_temporary, item.destination)
            except Exception as exc:
                errors.append(f"deleted {item.change.relative_path}: {exc}")
        for item in reversed(applied):
            try:
                if _exists(sftp, item.destination):
                    sftp.remove(item.destination)
                if item.old_temporary and _exists(sftp, item.old_temporary):
                    sftp.rename(item.old_temporary, item.destination)
            except Exception as exc:
                errors.append(f"{item.change.relative_path}: {exc}")
        for item in prepared:
            try:
                if _exists(sftp, item.new_temporary):
                    sftp.remove(item.new_temporary)
            except Exception as exc:
                errors.append(f"temporary {item.change.relative_path}: {exc}")
        for directory in reversed(created_directories):
            try:
                sftp.rmdir(directory)
            except OSError:
                pass
        return errors
