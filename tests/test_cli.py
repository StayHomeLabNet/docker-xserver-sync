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


def test_auto_sync_cli_accepts_full_policy_flags() -> None:
    from dxsync.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "auto-sync",
            "my-site",
            "--backup-mode",
            "add_only",
            "--remote-only-policy",
            "ask",
            "--delete-path",
            "old/a.js",
            "--delete-path",
            "old/b.css",
            "--no-health-check",
            "--health-check-path",
            "/healthz",
            "--validity-minutes",
            "45",
            "--max-changed-files",
            "200",
            "--max-update-files",
            "50",
            "--max-upload-mib",
            "128",
            "--yes",
            "--json",
        ]
    )
    assert args.backup_mode == "add_only"
    assert args.remote_only_policy == "ask"
    assert args.delete_path == ["old/a.js", "old/b.css"]
    assert args.no_health_check is True
    assert args.health_check_path == "/healthz"
    assert args.validity_minutes == 45
    assert args.max_changed_files == 200
    assert args.max_update_files == 50
    assert args.max_upload_mib == 128
    assert args.yes is True
    assert args.json is True


def test_backup_prune_cli_flags_and_dry_run_vs_confirm() -> None:
    from dxsync.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "backup",
            "prune",
            "my-site",
            "--keep-latest",
            "5",
            "--max-age-days",
            "14",
            "--dry-run",
            "--json",
        ]
    )
    assert args.keep_latest == 5
    assert args.max_age_days == 14
    assert args.dry_run is True
    assert args.json is True

