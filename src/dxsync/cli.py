from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
from typing import Sequence

from .models import FileRecord, SiteType
from .planner import PlanBuilder
from .presets import get_preset
from .remote import connect_sftp
from .scanner import LocalScanner
from .service import DryRunService


def _site_type(value: str) -> SiteType:
    try:
        return SiteType(value)
    except ValueError as exc:
        choices = ", ".join(item.value for item in SiteType)
        raise argparse.ArgumentTypeError(f"site must be one of: {choices}") from exc


def _scan(args: argparse.Namespace) -> int:
    preset = get_preset(args.site)
    result = LocalScanner(preset.exclude_patterns).scan(args.root)
    output = {
        "root": str(result.root),
        "files": [record.to_dict() for record in result.files],
        "issues": [issue.__dict__ for issue in result.issues],
        "excluded_count": result.excluded_count,
        "scanned_at": result.scanned_at.isoformat(),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 2 if any(issue.fatal for issue in result.issues) else 0


def _plan(args: argparse.Namespace) -> int:
    preset = get_preset(args.site)
    local = LocalScanner(preset.exclude_patterns).scan(args.root)
    remote_payload = json.loads(args.remote_manifest.read_text(encoding="utf-8"))
    remote_items = remote_payload.get("files", remote_payload)
    remote = tuple(FileRecord.from_dict(item) for item in remote_items)
    plan = PlanBuilder(preset.protect_patterns).build(
        mapping_id=args.mapping_id,
        local=local,
        remote_records=remote,
    )
    counts = {kind.value: count for kind, count in plan.counts().items()}
    output = {
        "plan_id": plan.plan_id,
        "mapping_id": plan.mapping_id,
        "created_at": plan.created_at.isoformat(),
        "expires_at": plan.expires_at.isoformat(),
        "local_snapshot_sha256": plan.local_snapshot_sha256,
        "remote_snapshot_sha256": plan.remote_snapshot_sha256,
        "counts": counts,
        "blocking": plan.has_blocking_changes or any(issue.fatal for issue in local.issues),
        "issues": [issue.__dict__ for issue in local.issues],
        "changes": [
            {
                "relative_path": change.relative_path,
                "change_type": change.change_type.value,
                "reason": change.reason,
                "local": change.local.to_dict() if change.local else None,
                "remote": change.remote.to_dict() if change.remote else None,
            }
            for change in plan.changes
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 2 if output["blocking"] else 0


def _remote_dry_run(args: argparse.Namespace) -> int:
    passphrase = getpass.getpass("秘密鍵のパスフレーズ: ") if args.ask_passphrase else None
    client, sftp = connect_sftp(
        host=args.host,
        port=args.port,
        username=args.username,
        private_key_path=str(args.private_key),
        host_key_fingerprint=args.fingerprint,
        passphrase=passphrase,
    )
    try:
        result = DryRunService().execute(
            site_type=args.site,
            mapping_id=args.mapping_id,
            local_root=args.root,
            remote_root=args.remote_root,
            sftp=sftp,
        )
    finally:
        sftp.close()
        client.close()

    counts = {kind.value: count for kind, count in result.plan.counts().items()}
    output = {
        "plan_id": result.plan.plan_id,
        "mapping_id": result.plan.mapping_id,
        "created_at": result.plan.created_at.isoformat(),
        "expires_at": result.plan.expires_at.isoformat(),
        "counts": counts,
        "blocking": result.has_blocking_issues,
        "protected_skipped": {
            "local": list(result.local.skipped_protected_paths),
            "remote": list(result.remote.skipped_protected_paths),
        },
        "issues": {
            "local": [issue.__dict__ for issue in result.local.issues],
            "remote": [issue.__dict__ for issue in result.remote.issues],
        },
        "changes": [
            {
                "relative_path": change.relative_path,
                "change_type": change.change_type.value,
                "reason": change.reason,
                "local": change.local.to_dict() if change.local else None,
                "remote": change.remote.to_dict() if change.remote else None,
            }
            for change in result.plan.changes
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 2 if result.has_blocking_issues else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dxsync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="scan a local project without remote access")
    scan.add_argument("--site", type=_site_type, required=True)
    scan.add_argument("--root", type=Path, required=True)
    scan.set_defaults(handler=_scan)

    plan = subparsers.add_parser("plan", help="compare a local project with a remote manifest")
    plan.add_argument("--site", type=_site_type, required=True)
    plan.add_argument("--root", type=Path, required=True)
    plan.add_argument("--remote-manifest", type=Path, required=True)
    plan.add_argument("--mapping-id", default="cli")
    plan.set_defaults(handler=_plan)

    remote = subparsers.add_parser(
        "dry-run", help="perform a read-only comparison with XServer over SFTP"
    )
    remote.add_argument("--site", type=_site_type, required=True)
    remote.add_argument("--root", type=Path, required=True)
    remote.add_argument("--host", required=True)
    remote.add_argument("--port", type=int, default=10022)
    remote.add_argument("--username", required=True)
    remote.add_argument("--private-key", type=Path, required=True)
    remote.add_argument("--fingerprint", required=True, help="expected SHA256 host-key fingerprint")
    remote.add_argument("--remote-root", required=True)
    remote.add_argument("--mapping-id", default="cli")
    remote.add_argument("--ask-passphrase", action="store_true")
    remote.set_defaults(handler=_remote_dry_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

