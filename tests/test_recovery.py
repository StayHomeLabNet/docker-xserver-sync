import stat
from dataclasses import dataclass

from dxsync.recovery import is_dxsync_temporary_name, scan_recovery_artifacts


@dataclass
class Entry:
    filename: str
    st_mode: int


class MemorySftp:
    def __init__(self) -> None:
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
