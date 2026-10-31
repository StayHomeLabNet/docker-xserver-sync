from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from .models import (
    ChangeType,
    FileChange,
    FileRecord,
    ScanResult,
    SyncPlan,
    utc_now,
)
from .rules import PathRules


def snapshot_hash(records: dict[str, FileRecord]) -> str:
    payload = [records[path].to_dict() for path in sorted(records)]
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PlanBuilder:
    def __init__(self, protect_patterns: tuple[str, ...] = ()) -> None:
        self._protected = PathRules(protect_patterns)

    def build(
        self,
        *,
        mapping_id: str,
        local: ScanResult,
        remote_records: tuple[FileRecord, ...],
        validity: timedelta = timedelta(minutes=5),
        config_sha256: str = "",
    ) -> SyncPlan:
        local_by_path = local.by_path
        remote_by_path = {record.relative_path: record for record in remote_records}
        changes: list[FileChange] = []

        for path in sorted(local_by_path.keys() | remote_by_path.keys()):
            local_record = local_by_path.get(path)
            remote_record = remote_by_path.get(path)
            if self._protected.matches(path):
                change_type = ChangeType.PROTECTED
                reason = "Path is protected by the site or project policy"
            elif local_record is None:
                change_type = ChangeType.REMOTE_ONLY
                reason = "Remote-only files are preserved"
            elif remote_record is None:
                change_type = ChangeType.ADD
                reason = None
            elif local_record.kind != remote_record.kind:
                change_type = ChangeType.TYPE_CONFLICT
                reason = "Local and remote entry types differ"
            elif local_record.sha256 is None or remote_record.sha256 is None:
                change_type = ChangeType.UNREADABLE
                reason = "A content hash is unavailable"
            elif local_record.sha256 == remote_record.sha256:
                change_type = ChangeType.SAME
                reason = None
            else:
                change_type = ChangeType.UPDATE
                reason = None

            changes.append(
                FileChange(
                    relative_path=path,
                    change_type=change_type,
                    local=local_record,
                    remote=remote_record,
                    reason=reason,
                )
            )

        created_at = utc_now()
        return SyncPlan(
            plan_id=str(uuid4()),
            mapping_id=mapping_id,
            created_at=created_at,
            expires_at=created_at + validity,
            changes=tuple(changes),
            local_snapshot_sha256=snapshot_hash(local_by_path),
            remote_snapshot_sha256=snapshot_hash(remote_by_path),
            config_sha256=config_sha256,
        )

