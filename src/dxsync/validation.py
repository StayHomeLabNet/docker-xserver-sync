from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .models import Project, SyncMapping


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    field: str | None = None


def normalize_remote_root(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("Remote path contains a forbidden control character")
    if not value.startswith("/"):
        raise ValueError("Remote path must be absolute")
    path = PurePosixPath(value)
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("Remote path cannot contain . or .. segments")
    normalized = str(path)
    if normalized in {"/", "/home"}:
        raise ValueError("Remote path is too broad")
    return normalized.rstrip("/")


def _is_path_parent(parent: str, child: str) -> bool:
    parent_parts = PurePosixPath(parent).parts
    child_parts = PurePosixPath(child).parts
    return len(parent_parts) <= len(child_parts) and child_parts[: len(parent_parts)] == parent_parts


def validate_mapping(mapping: SyncMapping) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if not mapping.name.strip():
        issues.append(ValidationIssue("mapping_name", "Mapping name is required", "name"))
    local = Path(mapping.local_root).expanduser()
    if not local.is_absolute():
        issues.append(ValidationIssue("local_not_absolute", "Local root must be absolute", "local_root"))
    elif not local.is_dir():
        issues.append(ValidationIssue("local_missing", "Local root does not exist", "local_root"))
    try:
        normalize_remote_root(mapping.remote_root)
    except ValueError as exc:
        issues.append(ValidationIssue("remote_path", str(exc), "remote_root"))
    if not 0 <= mapping.file_mode <= 0o777:
        issues.append(ValidationIssue("file_mode", "File mode must be between 000 and 777", "file_mode"))
    if not 0 <= mapping.directory_mode <= 0o777:
        issues.append(
            ValidationIssue("directory_mode", "Directory mode must be between 000 and 777", "directory_mode")
        )
    return tuple(issues)


def validate_project(project: Project) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if not project.name.strip():
        issues.append(ValidationIssue("project_name", "Project name is required", "name"))
    if not re.fullmatch(r"[A-Za-z0-9.-]+", project.domain) or "." not in project.domain:
        issues.append(ValidationIssue("domain", "Domain is not valid", "domain"))
    if not project.ssh_host.strip() or any(character.isspace() for character in project.ssh_host):
        issues.append(ValidationIssue("ssh_host", "SSH host is required", "ssh_host"))
    if not project.ssh_username.strip() or any(
        character.isspace() for character in project.ssh_username
    ):
        issues.append(ValidationIssue("ssh_username", "SSH username is required", "ssh_username"))
    if not 1 <= project.ssh_port <= 65535:
        issues.append(ValidationIssue("ssh_port", "SSH port must be between 1 and 65535", "ssh_port"))
    if not project.host_key_fingerprint.startswith("SHA256:"):
        issues.append(
            ValidationIssue(
                "host_key_fingerprint",
                "An SHA256 host-key fingerprint is required",
                "host_key_fingerprint",
            )
        )

    for index, mapping in enumerate(project.mappings):
        for issue in validate_mapping(mapping):
            issues.append(
                ValidationIssue(issue.code, issue.message, f"mappings[{index}].{issue.field}")
            )

    mapping_ids = [mapping.mapping_id for mapping in project.mappings]
    if len(mapping_ids) != len(set(mapping_ids)):
        issues.append(
            ValidationIssue(
                "duplicate_mapping_id",
                "Mapping identifiers must be unique",
                "mappings",
            )
        )

    valid_roots: list[tuple[int, str]] = []
    for index, mapping in enumerate(project.mappings):
        try:
            valid_roots.append((index, normalize_remote_root(mapping.remote_root)))
        except ValueError:
            continue
    for position, (left_index, left) in enumerate(valid_roots):
        for right_index, right in valid_roots[position + 1 :]:
            if _is_path_parent(left, right) or _is_path_parent(right, left):
                issues.append(
                    ValidationIssue(
                        "overlapping_remote_roots",
                        f"Remote mappings overlap: {left} and {right}",
                        f"mappings[{left_index}],mappings[{right_index}]",
                    )
                )
    return tuple(issues)

