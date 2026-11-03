import io
import stat
from dataclasses import dataclass
from pathlib import Path

from dxsync.models import ChangeType, SiteType
from dxsync.service import DryRunService


@dataclass
class Attr:
    filename: str
    st_mode: int
    st_size: int = 0
    st_mtime: int = 1


class FakeSftp:
    def __init__(self) -> None:
        self.directories = {
            "/remote/site": [
                Attr("index.php", stat.S_IFREG | 0o644, 3),
                Attr("wp-content", stat.S_IFDIR | 0o755),
            ],
            "/remote/site/wp-content": [Attr("uploads", stat.S_IFDIR | 0o755)],
        }

    def listdir_attr(self, path: str):
        return self.directories[path]

    def open(self, path: str, mode: str = "r"):
        return io.BytesIO(b"old")


class CustomPhpSftp:
    def __init__(self) -> None:
        self.directories = {
            "/remote/site": [
                Attr("index.php", stat.S_IFREG | 0o644, 3),
                Attr("userfiles", stat.S_IFDIR | 0o755),
            ],
            "/remote/site/userfiles": [Attr("remote.txt", stat.S_IFREG | 0o644, 3)],
        }

    def listdir_attr(self, path: str):
        return self.directories[path]

    def open(self, path: str, mode: str = "r"):
        return io.BytesIO(b"old")


def test_dry_run_compares_code_and_skips_wordpress_uploads(tmp_path: Path) -> None:
    (tmp_path / "index.php").write_bytes(b"new")
    uploads = tmp_path / "wp-content" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "photo.jpg").write_bytes(b"photo")

    result = DryRunService().execute(
        site_type=SiteType.WORDPRESS,
        mapping_id="mapping",
        local_root=tmp_path,
        remote_root="/remote/site",
        sftp=FakeSftp(),
        staging_parent=tmp_path / "staging",
    )

    assert result.plan.changes[0].relative_path == "index.php"
    assert result.plan.changes[0].change_type is ChangeType.UPDATE
    assert result.local.skipped_protected_paths == ("wp-content/uploads",)
    assert result.remote.skipped_protected_paths == ("wp-content/uploads",)
    assert result.staging is not None
    assert (result.staging.root / "index.php").read_bytes() == b"new"
    assert not (result.staging.root / "wp-content").exists()


def test_custom_php_extra_runtime_directory_is_not_scanned_or_staged(tmp_path: Path) -> None:
    (tmp_path / "index.php").write_bytes(b"new")
    runtime = tmp_path / "userfiles"
    runtime.mkdir()
    (runtime / "local.txt").write_bytes(b"local")

    result = DryRunService().execute(
        site_type=SiteType.CUSTOM_PHP,
        mapping_id="mapping",
        local_root=tmp_path,
        remote_root="/remote/site",
        sftp=CustomPhpSftp(),
        extra_protected=("userfiles/",),
        staging_parent=tmp_path / "staging",
    )

    assert result.local.skipped_protected_paths == ("userfiles",)
    assert result.remote.skipped_protected_paths == ("userfiles",)
    assert {change.relative_path for change in result.plan.changes} == {"index.php"}
    assert result.staging is not None
    assert not (result.staging.root / "userfiles").exists()
