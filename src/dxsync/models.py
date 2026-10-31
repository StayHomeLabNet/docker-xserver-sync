from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


class SiteType(str, Enum):
    WORDPRESS = "wordpress"
    LARAVEL = "laravel"
    CUSTOM_PHP = "custom_php"


class ChangeType(str, Enum):
    ADD = "add"
    UPDATE = "update"
    SAME = "same"
    REMOTE_ONLY = "remote_only"
    PROTECTED = "protected"
    TYPE_CONFLICT = "type_conflict"
    UNREADABLE = "unreadable"


class EntryKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    if not normalized or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Invalid relative path: {value!r}")
    if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
        raise ValueError("Path contains a forbidden control character")
    return normalized


@dataclass(frozen=True)
class FileRecord:
    relative_path: str
    kind: EntryKind
    size: int | None
    modified_ns: int | None
    sha256: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", normalize_relative_path(self.relative_path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "kind": self.kind.value,
            "size": self.size,
            "modified_ns": self.modified_ns,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FileRecord":
        return cls(
            relative_path=value["relative_path"],
            kind=EntryKind(value.get("kind", "file")),
            size=value.get("size"),
            modified_ns=value.get("modified_ns"),
            sha256=value.get("sha256"),
        )


@dataclass(frozen=True)
class ScanIssue:
    relative_path: str
    code: str
    message: str
    fatal: bool = True


@dataclass(frozen=True)
class ScanResult:
    root: Path
    files: tuple[FileRecord, ...]
    issues: tuple[ScanIssue, ...]
    excluded_count: int
    skipped_protected_paths: tuple[str, ...] = ()
    scanned_at: datetime = field(default_factory=utc_now)

    @property
    def by_path(self) -> dict[str, FileRecord]:
        return {item.relative_path: item for item in self.files}


@dataclass(frozen=True)
class FileChange:
    relative_path: str
    change_type: ChangeType
    local: FileRecord | None
    remote: FileRecord | None
    reason: str | None = None


@dataclass(frozen=True)
class SyncMapping:
    mapping_id: str
    name: str
    local_root: str
    remote_root: str
    exclude_patterns: tuple[str, ...] = ()
    protect_patterns: tuple[str, ...] = ()
    file_mode: int = 0o644
    directory_mode: int = 0o755

    @classmethod
    def create(cls, name: str, local_root: str, remote_root: str) -> "SyncMapping":
        return cls(
            mapping_id=str(uuid4()),
            name=name,
            local_root=local_root,
            remote_root=remote_root,
        )


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    site_type: SiteType
    domain: str
    ssh_host: str
    ssh_port: int
    ssh_username: str
    host_key_fingerprint: str
    private_key_reference: str
    mappings: tuple[SyncMapping, ...]
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        site_type: SiteType,
        domain: str,
        ssh_host: str,
        ssh_username: str,
        host_key_fingerprint: str,
        private_key_reference: str,
        mappings: tuple[SyncMapping, ...],
        ssh_port: int = 10022,
    ) -> "Project":
        if not mappings:
            raise ValueError("At least one sync mapping is required")
        return cls(
            project_id=str(uuid4()),
            name=name,
            site_type=site_type,
            domain=domain,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_username=ssh_username,
            host_key_fingerprint=host_key_fingerprint,
            private_key_reference=private_key_reference,
            mappings=mappings,
        )


@dataclass(frozen=True)
class SyncPlan:
    plan_id: str
    mapping_id: str
    created_at: datetime
    expires_at: datetime
    changes: tuple[FileChange, ...]
    local_snapshot_sha256: str
    remote_snapshot_sha256: str
    config_sha256: str = ""

    @property
    def has_blocking_changes(self) -> bool:
        return any(
            change.change_type in {ChangeType.TYPE_CONFLICT, ChangeType.UNREADABLE}
            for change in self.changes
        )

    def counts(self) -> dict[ChangeType, int]:
        result = {kind: 0 for kind in ChangeType}
        for change in self.changes:
            result[change.change_type] += 1
        return result

