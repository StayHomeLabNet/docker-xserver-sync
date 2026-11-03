from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .models import Project, SiteType, SyncMapping


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


def normalize_runtime_directory(value: str) -> str:
    """Normalize an exact project-relative writable directory for protection rules."""
    candidate = value.strip()
    if not candidate:
        raise ValueError("空のパスは登録できません。")
    if any(character in candidate for character in ("\x00", "\n", "\r")):
        raise ValueError("パスに改行やNUL文字は使えません。")
    if "\\" in candidate:
        raise ValueError("区切り文字は / を使ってください。")
    if candidate.startswith("/"):
        raise ValueError("同期元ルートからの相対パスを入力してください。")
    without_slash = candidate.rstrip("/")
    parts = PurePosixPath(without_slash).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(". や .. を含むパスは使えません。")
    if any(character in without_slash for character in "*?[]"):
        raise ValueError("ワイルドカードではなくディレクトリ名を入力してください。")
    return f"{PurePosixPath(without_slash).as_posix()}/"


def custom_runtime_paths_issue(
    project: Project, mapping: SyncMapping
) -> ValidationIssue | None:
    if project.site_type is SiteType.CUSTOM_PHP and not mapping.runtime_paths_confirmed:
        return ValidationIssue(
            "runtime_paths_unconfirmed",
            f"「{mapping.name}」の本番で書き込まれるディレクトリを確認してください。",
            "runtime_paths_confirmed",
        )
    return None


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
        if runtime_issue := custom_runtime_paths_issue(project, mapping):
            issues.append(
                ValidationIssue(
                    runtime_issue.code,
                    runtime_issue.message,
                    f"mappings[{index}].runtime_paths_confirmed",
                )
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

