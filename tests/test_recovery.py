import stat
import io
from dataclasses import dataclass

from dxsync.recovery import (
    analyze_recovery_artifacts,
    is_dxsync_temporary_name,
    scan_recovery_artifacts,
)


@dataclass
class Entry:
    filename: str
    st_mode: int


class MemorySftp:
    def __init__(self) -> None:
        self.listdir_calls: list[str] = []
        self.open_calls: list[str] = []
        self.stat_calls: list[str] = []
        self.files = {
            "/home/user/site/index.php": b"published",
            "/home/user/site/index.php.dxsync-plan-abc.new": b"published",
            "/home/user/site/nested/old.php.dxsync-restore-plan.old": b"old",
        }
        self.entries = {
            "/home/user/site": [
                Entry("nested", stat.S_IFDIR | 0o755),
                Entry("index.php", stat.S_IFREG | 0o644),
                Entry("index.php.dxsync-plan-abc.new", stat.S_IFREG | 0o600),
            ],
            "/home/user/site/nested": [
                Entry("old.php.dxsync-restore-plan.old", stat.S_IFREG | 0o600),
            ],
        }

    def listdir_attr(self, path: str):
        self.listdir_calls.append(path)
        return self.entries[path]

    def open(self, path: str, mode: str = "r"):
        self.open_calls.append(path)
        return io.BytesIO(self.files[path])

    def stat(self, path: str):
        self.stat_calls.append(path)
        if path in self.files:
            return type("RemoteStat", (), {"st_mode": stat.S_IFREG | 0o600})()
        raise FileNotFoundError(path)


def test_temporary_name_detection_is_narrow() -> None:
    assert is_dxsync_temporary_name("index.php.dxsync-plan.new")
    assert is_dxsync_temporary_name("index.php.dxsync-restore-plan.old")
    assert not is_dxsync_temporary_name(".dxsync-manifest.json")
    assert not is_dxsync_temporary_name("user-file.new")


def test_recovery_scan_lists_only_dxsync_temporary_files() -> None:
    result = scan_recovery_artifacts(MemorySftp(), "/home/user/site")
    assert result.temporary_files == (
        "index.php.dxsync-plan-abc.new",
        "nested/old.php.dxsync-restore-plan.old",
    )
    assert result.issues == ()


def test_recovery_analysis_compares_temporary_and_destination_hashes() -> None:
    result = analyze_recovery_artifacts(MemorySftp(), "/home/user/site")
    by_path = {item.temporary_path: item for item in result.artifacts}
    assert by_path["index.php.dxsync-plan-abc.new"].assessment == "公開済みコピーと一致"
    assert by_path["index.php.dxsync-plan-abc.new"].destination_sha256 is not None
    assert by_path["nested/old.php.dxsync-restore-plan.old"].assessment == "公開ファイルがなく旧退避だけ存在"


def test_recovery_does_not_enter_or_read_protected_targets() -> None:
    sftp = MemorySftp()
    result = analyze_recovery_artifacts(
        sftp,
        "/home/user/site",
        protect_patterns=("index.php", "nested/"),
    )

    assert result.artifacts == ()
    assert sftp.listdir_calls == ["/home/user/site"]
    assert sftp.open_calls == []
    assert sftp.stat_calls == []


def test_recovery_scan_and_analysis_report_progress_and_cancel() -> None:
    import pytest
    from dxsync.cancellation import CancellationToken, OperationCancelled

    events: list[tuple[int, int | None, str]] = []
    analyze_recovery_artifacts(
        MemorySftp(),
        "/home/user/site",
        progress_callback=lambda cur, tot, detail: events.append((cur, tot, detail)),
    )
    assert any(tot is None and cur >= 1 for cur, tot, _ in events)
    assert any(tot == 2 and cur == 0 for cur, tot, _ in events)
    assert any(tot == 2 and cur == 2 for cur, tot, _ in events)

    cancel = CancellationToken()
    cancel.cancel()
    with pytest.raises(OperationCancelled):
        scan_recovery_artifacts(
            MemorySftp(),
            "/home/user/site",
            cancellation_token=cancel,
        )

