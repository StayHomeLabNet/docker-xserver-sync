import json
from datetime import datetime
from pathlib import Path

import pytest

from dxsync.cli import main


def test_expected_errors_are_reported_without_traceback(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing"
    result = main(["scan", "--site", "wordpress", "--root", str(missing)])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.err.startswith("error: Local root is not a readable directory")
    assert "Traceback" not in captured.err


def test_plan_accepts_custom_validity_minutes_up_to_60(tmp_path: Path, capsys) -> None:
    root = tmp_path / "site"
    root.mkdir()
    manifest = tmp_path / "remote.json"
    manifest.write_text('{"files": []}', encoding="utf-8")

    result = main(
        [
            "plan",
            "--site",
            "static_site",
            "--root",
            str(root),
            "--remote-manifest",
            str(manifest),
            "--validity-minutes",
            "60",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    created = datetime.fromisoformat(payload["created_at"])
    expires = datetime.fromisoformat(payload["expires_at"])
    assert int((expires - created).total_seconds()) == 3600


def test_plan_rejects_validity_minutes_over_60(tmp_path: Path) -> None:
    root = tmp_path / "site"
    root.mkdir()
    manifest = tmp_path / "remote.json"
    manifest.write_text("[]", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "plan",
                "--site",
                "static_site",
                "--root",
                str(root),
                "--remote-manifest",
                str(manifest),
                "--validity-minutes",
                "61",
            ]
        )

    assert exc_info.value.code == 2
