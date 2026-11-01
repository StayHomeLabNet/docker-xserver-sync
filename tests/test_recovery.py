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
        return self.entries[path]

    def open(self, path: str, mode: str = "r"):
        return io.BytesIO(self.files[path])

    def stat(self, path: str):
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
