from __future__ import annotations

import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .models import Project, SiteType, SyncMapping
from .storage import ProjectStore
from .validation import normalize_remote_root


@dataclass(frozen=True)
class CleanupResult:
    removed: tuple[Path, ...]
    skipped: tuple[str, ...]


def cleanup_stale_staging(store: ProjectStore, staging_parent: Path) -> CleanupResult:
    """Remove only DB-referenced staging directories for non-runnable plans."""
    parent = staging_parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    removed: list[Path] = []
    skipped: list[str] = []
    resumable_plan_ids = {
        str(operation.details.get("plan_id"))
        for operation in store.list_operations(limit=10_000)
        if operation.status in {"cancelled", "failed", "interrupted"}
        and operation.details.get("resumable") is True
        and operation.details.get("plan_id")
    }
    stale = store.list_plans({"expired", "superseded", "invalid", "used"})
    for stored in stale:
        if stored.plan.plan_id in resumable_plan_ids:
            continue
        if not stored.staging_path:
            continue
        candidate = Path(stored.staging_path)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            continue
        if resolved.parent != parent or candidate.is_symlink() or not resolved.is_dir():
            skipped.append(f"Unsafe staging path was not removed: {candidate}")
            continue
        shutil.rmtree(resolved)
        removed.append(resolved)
    return CleanupResult(tuple(removed), tuple(skipped))


def wordpress_maintenance_path(remote_root: str) -> str:
    return str(PurePosixPath(normalize_remote_root(remote_root)) / ".maintenance")


def enable_wordpress_maintenance(sftp: Any, remote_root: str) -> str:
    path = wordpress_maintenance_path(remote_root)
    payload = f"<?php $upgrading = {int(time.time())}; ?>\n".encode("utf-8")
    with sftp.open(path, "wb") as stream:
        stream.write(payload)
    return path


def disable_wordpress_maintenance(sftp: Any, remote_root: str) -> bool:
    path = wordpress_maintenance_path(remote_root)
    try:
        sftp.remove(path)
        return True
    except FileNotFoundError:
        return False


def laravel_app_root(project: Project, mapping: SyncMapping) -> str:
    normalized = normalize_remote_root(mapping.remote_root)
    if normalized.endswith("/public") and len(normalized) > len("/public"):
        return normalized[: -len("/public")]
    for candidate in project.mappings:
        cand_root = normalize_remote_root(candidate.remote_root)
        if not cand_root.endswith("/public"):
            return cand_root
    return normalized


def _run_remote_artisan(ssh_client: Any, app_root: str, subcommand: str) -> None:
    cmd = f"cd {shlex.quote(app_root)} && php artisan {subcommand}"
    _stdin, stdout, stderr = ssh_client.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        err = stderr.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Laravel maintenance ({subcommand}) failed (exit {exit_code}): {err or 'unknown error'}"
        )


def enter_maintenance_mode(
    ssh_client: Any,
    sftp: Any,
    project: Project,
    mapping: SyncMapping,
) -> str | None:
    if project.site_type is SiteType.WORDPRESS:
        enable_wordpress_maintenance(sftp, mapping.remote_root)
        return "WordPress .maintenance を有効化しました"
    if project.site_type is SiteType.LARAVEL:
        app_root = laravel_app_root(project, mapping)
        _run_remote_artisan(ssh_client, app_root, "down")
        return "Laravel メンテナンスモード (php artisan down) を有効化しました"
    return None


def exit_maintenance_mode(
    ssh_client: Any,
    sftp: Any,
    project: Project,
    mapping: SyncMapping,
) -> str | None:
    if project.site_type is SiteType.WORDPRESS:
        disable_wordpress_maintenance(sftp, mapping.remote_root)
        return "WordPress .maintenance を解除しました"
    if project.site_type is SiteType.LARAVEL:
        app_root = laravel_app_root(project, mapping)
        _run_remote_artisan(ssh_client, app_root, "up")
        return "Laravel メンテナンスモード (php artisan up) を解除しました"
    return None
