from datetime import timedelta
from pathlib import Path

import pytest

from dxsync.models import ChangeType, EntryKind, FileRecord, ScanResult
from dxsync.planner import (
    DEFAULT_PLAN_VALIDITY_MINUTES,
    MAX_PLAN_VALIDITY_MINUTES,
    PlanBuilder,
    normalize_plan_validity_minutes,
)


def record(path: str, digest: str) -> FileRecord:
    return FileRecord(path, EntryKind.FILE, 1, 1, digest)


def test_plan_classifies_changes_and_omits_protected_data(tmp_path: Path) -> None:
    local = ScanResult(
        root=tmp_path,
        files=(
            record("add.php", "a"),
            record("same.php", "same"),
            record("update.php", "new"),
            record("wp-content/uploads/local.jpg", "local"),
        ),
        issues=(),
        excluded_count=0,
    )
    remote = (
        record("same.php", "same"),
        record("update.php", "old"),
        record("remote.php", "remote"),
        record("wp-content/uploads/local.jpg", "remote-upload"),
    )

    plan = PlanBuilder(("wp-content/uploads/",)).build(
        mapping_id="mapping", local=local, remote_records=remote
    )
    by_path = {change.relative_path: change.change_type for change in plan.changes}

    assert by_path == {
        "add.php": ChangeType.ADD,
        "remote.php": ChangeType.REMOTE_ONLY,
        "same.php": ChangeType.SAME,
        "update.php": ChangeType.UPDATE,
    }
    assert not plan.has_blocking_changes
    assert plan.expires_at - plan.created_at == timedelta(minutes=15)


def test_plan_supports_custom_validity_up_to_60_minutes(tmp_path: Path) -> None:
    local = ScanResult(root=tmp_path, files=(), issues=(), excluded_count=0)
    plan = PlanBuilder().build(
        mapping_id="mapping",
        local=local,
        remote_records=(),
        validity=timedelta(minutes=MAX_PLAN_VALIDITY_MINUTES),
    )

    assert plan.expires_at - plan.created_at == timedelta(minutes=60)


@pytest.mark.parametrize("bad_minutes", [0, -5, 61, 120])
def test_plan_rejects_out_of_range_validity(tmp_path: Path, bad_minutes: int) -> None:
    local = ScanResult(root=tmp_path, files=(), issues=(), excluded_count=0)

    with pytest.raises(ValueError, match="at most 60 minutes"):
        PlanBuilder().build(
            mapping_id="mapping",
            local=local,
            remote_records=(),
            validity=timedelta(minutes=bad_minutes),
        )


def test_normalize_plan_validity_minutes_clamps_and_defaults() -> None:
    assert normalize_plan_validity_minutes(None) == DEFAULT_PLAN_VALIDITY_MINUTES
    assert normalize_plan_validity_minutes("abc") == DEFAULT_PLAN_VALIDITY_MINUTES
    assert normalize_plan_validity_minutes(True) == DEFAULT_PLAN_VALIDITY_MINUTES
    assert normalize_plan_validity_minutes(0) == 1
    assert normalize_plan_validity_minutes(-10) == 1
    assert normalize_plan_validity_minutes("30") == 30
    assert normalize_plan_validity_minutes(90) == 60

