from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .storage import ProjectStore


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
