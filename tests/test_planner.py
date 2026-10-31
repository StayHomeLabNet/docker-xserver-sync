from pathlib import Path

from dxsync.models import ChangeType, EntryKind, FileRecord, ScanResult
from dxsync.planner import PlanBuilder


def record(path: str, digest: str) -> FileRecord:
    return FileRecord(path, EntryKind.FILE, 1, 1, digest)


def test_plan_classifies_changes_and_protects_runtime_data(tmp_path: Path) -> None:
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
        "wp-content/uploads/local.jpg": ChangeType.PROTECTED,
    }
    assert not plan.has_blocking_changes

