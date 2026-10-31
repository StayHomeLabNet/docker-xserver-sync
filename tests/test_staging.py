from pathlib import Path

import pytest

from dxsync.scanner import LocalScanner
from dxsync.staging import StagingArea, StagingError


def test_staging_copies_and_verifies_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.php").write_text("<?php", encoding="utf-8")
    scan = LocalScanner().scan(source)

    result = StagingArea().create(scan, tmp_path / "staging")

    assert (result.root / "index.php").read_text(encoding="utf-8") == "<?php"
    assert result.manifest_path.is_file()
    assert result.file_count == 1


def test_staging_requires_empty_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.php").write_text("<?php", encoding="utf-8")
    scan = LocalScanner().scan(source)
    destination = tmp_path / "staging"
    destination.mkdir()
    (destination / "existing").write_text("x", encoding="utf-8")

    with pytest.raises(StagingError):
        StagingArea().create(scan, destination)


def test_staging_verification_detects_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.php").write_text("original", encoding="utf-8")
    scan = LocalScanner().scan(source)
    result = StagingArea().create(scan, tmp_path / "staging")
    expected = {record.relative_path: record.sha256 for record in scan.files if record.sha256}

    assert StagingArea().verify(result.root, expected).valid
    (result.root / "index.php").write_text("tampered", encoding="utf-8")
    verification = StagingArea().verify(result.root, expected)
    assert not verification.valid
    assert any("hash changed" in issue for issue in verification.issues)

