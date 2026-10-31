from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .models import ScanResult
from .scanner import sha256_file


@dataclass(frozen=True)
class StagingResult:
    root: Path
    manifest_path: Path
    file_count: int
    total_bytes: int


class StagingError(RuntimeError):
    pass


@dataclass(frozen=True)
class StagingVerification:
    valid: bool
    issues: tuple[str, ...]


class StagingArea:
    def create(
        self,
        scan: ScanResult,
        destination: Path,
        include_paths: set[str] | None = None,
    ) -> StagingResult:
        root = destination.resolve()
        if root.exists() and any(root.iterdir()):
            raise StagingError(f"Staging directory is not empty: {root}")
        root.mkdir(parents=True, exist_ok=True)

        manifest: list[dict[str, object]] = []
        total_bytes = 0
        records = (
            scan.files
            if include_paths is None
            else tuple(record for record in scan.files if record.relative_path in include_paths)
        )
        for record in records:
            if record.sha256 is None:
                raise StagingError(f"Cannot stage a file without a hash: {record.relative_path}")
            source = scan.root / Path(record.relative_path)
            target = root / Path(record.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(source, target, follow_symlinks=False)
            except OSError as exc:
                raise StagingError(f"Failed to stage {record.relative_path}: {exc}") from exc
            staged_hash = sha256_file(target)
            if staged_hash != record.sha256:
                raise StagingError(f"Source changed while staging: {record.relative_path}")
            total_bytes += record.size or 0
            manifest.append(record.to_dict())

        manifest_path = root / ".dxsync-manifest.json"
        manifest_path.write_text(
            json.dumps({"files": manifest}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return StagingResult(root, manifest_path, len(manifest), total_bytes)

    def verify(self, root: Path, expected_hashes: dict[str, str]) -> StagingVerification:
        resolved_root = root.resolve()
        issues: list[str] = []
        manifest_path = resolved_root / ".dxsync-manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return StagingVerification(False, ("Staging manifest is missing or unsafe",))
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_records = {
                item["relative_path"]: item["sha256"] for item in payload["files"]
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return StagingVerification(False, (f"Staging manifest is invalid: {exc}",))

        if manifest_records != expected_hashes:
            issues.append("Staging manifest does not match the approved upload set")

        actual_paths: set[str] = set()
        for candidate in resolved_root.rglob("*"):
            relative = candidate.relative_to(resolved_root).as_posix()
            if relative == ".dxsync-manifest.json" or candidate.is_dir():
                continue
            actual_paths.add(relative)
            if candidate.is_symlink() or not candidate.is_file():
                issues.append(f"Unsafe staged entry: {relative}")
                continue
            expected = expected_hashes.get(relative)
            if expected is None:
                issues.append(f"Unexpected staged file: {relative}")
            elif sha256_file(candidate) != expected:
                issues.append(f"Staged file hash changed: {relative}")

        missing = set(expected_hashes) - actual_paths
        issues.extend(f"Staged file is missing: {path}" for path in sorted(missing))
        return StagingVerification(not issues, tuple(issues))

