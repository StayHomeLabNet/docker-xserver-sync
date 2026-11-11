from pathlib import Path

import pytest

from dxsync.cancellation import CancellationToken, OperationCancelled
from dxsync.scanner import LocalScanner


def test_scanner_hashes_regular_files_and_excludes(tmp_path: Path) -> None:
    (tmp_path / "index.php").write_text("<?php echo 'ok';", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=yes", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "autoload.php").write_text("<?php", encoding="utf-8")

    result = LocalScanner((".env",)).scan(tmp_path)

    assert [item.relative_path for item in result.files] == [
        "index.php",
        "vendor/autoload.php",
    ]
    assert all(item.sha256 for item in result.files)
    assert result.excluded_count == 1
    assert result.issues == ()


def test_scanner_reports_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        return

    result = LocalScanner().scan(tmp_path)
    assert any(issue.code == "symlink" and issue.relative_path == "link.txt" for issue in result.issues)


def test_scanner_stops_at_safe_checkpoint(tmp_path: Path) -> None:
    (tmp_path / "index.php").write_text("<?php", encoding="utf-8")
    token = CancellationToken()
    token.cancel()

    with pytest.raises(OperationCancelled):
        LocalScanner().scan(tmp_path, cancellation_token=token)

