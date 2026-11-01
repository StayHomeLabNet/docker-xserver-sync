from pathlib import Path

from dxsync.cli import main


def test_expected_errors_are_reported_without_traceback(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing"
    result = main(["scan", "--site", "wordpress", "--root", str(missing)])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.err.startswith("error: Local root is not a readable directory")
    assert "Traceback" not in captured.err
