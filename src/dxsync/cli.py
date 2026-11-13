from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Sequence

from .models import FileRecord, SiteType
from .operations import (
    OperationError,
    execute_auto_sync,
    execute_dry_run,
    execute_restore,
    execute_verify,
    find_project,
    get_project_detail,
    list_backups_summary,
    list_projects_summary,
)
from .planner import PlanBuilder
from .presets import get_preset
from .remote import (
    HostKeyMismatchError,
    RemoteDependencyError,
    RemoteScanError,
    connect_sftp,
)
from .scanner import LocalScanner
from .service import DryRunService
from .storage import ProjectStore, get_default_store


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


# --- Project-based Handlers ---


def _get_store_from_args(args: argparse.Namespace) -> ProjectStore:
    return getattr(args, "_store", None) or get_default_store()


def _resolve_project(store: ProjectStore, identifier: str):
    project = find_project(store, identifier)
    if not project:
        raise OperationError(f"プロジェクト '{identifier}' が見つかりません。")
    return project


def _project_list(args: argparse.Namespace) -> int:
    store = _get_store_from_args(args)
    projects = list_projects_summary(store)
    if args.json:
        print(json.dumps(projects, ensure_ascii=False, indent=2))
    else:
        if not projects:
            print("登録されているプロジェクトはありません。GUIから新規作成してください。")
            return 0
        print(f"{'PROJECT':<24} {'SITE TYPE':<12} {'DOMAIN':<24} {'SSH HOST'}")
        print("-" * 75)
        for p in projects:
            print(f"{p['name']:<24} {p['site_type']:<12} {p['domain']:<24} {p['ssh_host']}")
    return 0


def _project_show(args: argparse.Namespace) -> int:
    store = _get_store_from_args(args)
    project = _resolve_project(store, args.project)
    detail = get_project_detail(store, project)
    if args.json:
        print(json.dumps(detail, ensure_ascii=False, indent=2))
    else:
        print(f"プロジェクト名: {detail['name']}")
        print(f"ID:           {detail['project_id']}")
        print(f"サイト種別:     {detail['site_type']}")
        print(f"ドメイン:       {detail['domain']}")
        print(f"SSHホスト:      {detail['ssh_host']}:{detail['ssh_port']}")
        print(f"SSHユーザー:    {detail['ssh_username']}")
        print(f"秘密鍵:         {detail['private_key_path']}")
        print(f"確認コード:     {'設定済み' if detail['has_fingerprint'] else '未設定'}")
        print("\nマッピング一覧:")
        for m in detail["mappings"]:
            print(f"  • [{m['name']}]")
            print(f"      ローカル: {m['local_root']}")
            print(f"      リモート: {m['remote_root']}")
            if m["protect_patterns"]:
                print(f"      保護パス: {', '.join(m['protect_patterns'])}")
    return 0


def _run_dry_run_project(args: argparse.Namespace) -> int:
    store = _get_store_from_args(args)
    project = _resolve_project(store, args.project)
    passphrase = getpass.getpass("秘密鍵のパスフレーズ: ") if args.ask_passphrase else None
    result = execute_dry_run(
        store=store,
        project=project,
        mapping_name=args.mapping,
        passphrase=passphrase,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[{project.name}] ドライラン結果:")
        print(f"  Plan ID: {result['plan_id']}")
        print("  差分集計:")
        for kind, count in result["counts"].items():
            if count > 0:
                print(f"    • {kind}: {count}件")
        if result["changes"]:
            print("\n  主な変更点 (上位20件):")
            for c in result["changes"][:20]:
                print(f"    [{c['change_type']}] {c['relative_path']}")
        if result["has_blocking_issues"]:
            print("\n  [警告] ブロック理由または重大なエラーがあります。")
    return 2 if result["has_blocking_issues"] else 0


def _run_auto_sync_project(args: argparse.Namespace) -> int:
    store = _get_store_from_args(args)
    project = _resolve_project(store, args.project)
    passphrase = getpass.getpass("秘密鍵のパスフレーズ: ") if args.ask_passphrase else None
    result = execute_auto_sync(
        store=store,
        project=project,
        mapping_name=args.mapping,
        passphrase=passphrase,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["status"] == "no_changes":
            print(f"[{project.name}] {result['message']}")
        else:
            print(f"[{project.name}] 安全同期が正常に完了しました:")
            print(f"  • 転送ファイル数: {result['transferred_files']}件 ({result['total_bytes']:,} bytes)")
            print(f"  • バックアップ:   {result['backup_id']} ({result['backup_files']}件退避)")
            print(f"  • HTTPS状態:      {result['health_check']['status_code']} ({result['health_check']['url']})")
    return 0


def _run_verify_project(args: argparse.Namespace) -> int:
    store = _get_store_from_args(args)
    project = _resolve_project(store, args.project)
    passphrase = getpass.getpass("秘密鍵のパスフレーズ: ") if args.ask_passphrase else None
    result = execute_verify(
        store=store,
        project=project,
        mapping_name=args.mapping,
        cleanup=args.cleanup,
        passphrase=passphrase,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[{project.name}] リモート安全性検査結果:")
        print(f"  • リモートルート:         {result['remote_root']}")
        print(f"  • 検出一時ファイル数:     {result['temporary_files_count']}件")
        if result["temporary_files"]:
            for f in result["temporary_files"]:
                print(f"      - {f}")
        if args.cleanup:
            print(f"  • 削除完了ファイル数:     {result['cleaned_up_count']}件")
    return 0


def _backup_list(args: argparse.Namespace) -> int:
    store = _get_store_from_args(args)
    project = _resolve_project(store, args.project)
    backups = list_backups_summary(store, project)
    if args.json:
        print(json.dumps(backups, ensure_ascii=False, indent=2))
    else:
        if not backups:
            print(f"[{project.name}] バックアップ履歴はありません。")
            return 0
        print(f"{'BACKUP ID':<28} {'CREATED AT':<22} {'FILES':<8} {'BYTES'}")
        print("-" * 75)
        for b in backups:
            print(f"{b['backup_id']:<28} {b['created_at'][:19]:<22} {b['file_count']:<8} {b['total_bytes']:,}")
    return 0


def _backup_restore(args: argparse.Namespace) -> int:
    store = _get_store_from_args(args)
    project = _resolve_project(store, args.project)
    passphrase = getpass.getpass("秘密鍵のパスフレーズ: ") if args.ask_passphrase else None
    result = execute_restore(
        store=store,
        project=project,
        backup_id=args.backup_id,
        mapping_name=args.mapping,
        passphrase=passphrase,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[{project.name}] {result['summary']}")
    return 0


def _mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run_stdio_server

    store = _get_store_from_args(args)
    return run_stdio_server(store=store)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dxsync", description="Docker → XServer Sync CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Project subcommands
    project_parser = subparsers.add_parser("project", help="manage and inspect configured projects")
    project_subs = project_parser.add_subparsers(dest="subcommand", required=True)

    p_list = project_subs.add_parser("list", help="list all configured projects")
    p_list.add_argument("--json", action="store_true", help="output JSON")
    p_list.set_defaults(handler=_project_list)

    p_show = project_subs.add_parser("show", help="show project details")
    p_show.add_argument("project", help="project name, domain, or ID")
    p_show.add_argument("--json", action="store_true", help="output JSON")
    p_show.set_defaults(handler=_project_show)

    # 2. Run subcommands
    run_parser = subparsers.add_parser("run", help="execute sync, dry-run, or verification operations")
    run_subs = run_parser.add_subparsers(dest="subcommand", required=True)

    r_dry = run_subs.add_parser("dry-run", help="run a read-only dry run on a configured project")
    r_dry.add_argument("project", help="project name, domain, or ID")
    r_dry.add_argument("--mapping", help="optional mapping name or ID")
    r_dry.add_argument("--ask-passphrase", action="store_true", help="prompt for SSH key passphrase")
    r_dry.add_argument("--json", action="store_true", help="output JSON")
    r_dry.set_defaults(handler=_run_dry_run_project)

    r_auto = run_subs.add_parser("auto-sync", help="run safe automated 4-stage sync pipeline")
    r_auto.add_argument("project", help="project name, domain, or ID")
    r_auto.add_argument("--mapping", help="optional mapping name or ID")
    r_auto.add_argument("--ask-passphrase", action="store_true", help="prompt for SSH key passphrase")
    r_auto.add_argument("--yes", "-y", action="store_true", help="confirm execution")
    r_auto.add_argument("--json", action="store_true", help="output JSON")
    r_auto.set_defaults(handler=_run_auto_sync_project)

    r_verify = run_subs.add_parser("verify", help="inspect remote storage for temporary artifacts")
    r_verify.add_argument("project", help="project name, domain, or ID")
    r_verify.add_argument("--mapping", help="optional mapping name or ID")
    r_verify.add_argument("--cleanup", action="store_true", help="delete detected temporary artifacts")
    r_verify.add_argument("--ask-passphrase", action="store_true", help="prompt for SSH key passphrase")
    r_verify.add_argument("--json", action="store_true", help="output JSON")
    r_verify.set_defaults(handler=_run_verify_project)

    # 3. Backup subcommands
    backup_parser = subparsers.add_parser("backup", help="view backups and roll back changes")
    backup_subs = backup_parser.add_subparsers(dest="subcommand", required=True)

    b_list = backup_subs.add_parser("list", help="list backups for a project")
    b_list.add_argument("project", help="project name, domain, or ID")
    b_list.add_argument("--json", action="store_true", help="output JSON")
    b_list.set_defaults(handler=_backup_list)

    b_restore = backup_subs.add_parser("restore", help="restore remote files from a backup")
    b_restore.add_argument("project", help="project name, domain, or ID")
    b_restore.add_argument("--backup-id", required=True, help="backup ID to restore from")
    b_restore.add_argument("--mapping", help="optional mapping name or ID")
    b_restore.add_argument("--ask-passphrase", action="store_true", help="prompt for SSH key passphrase")
    b_restore.add_argument("--yes", "-y", action="store_true", help="confirm restoration")
    b_restore.add_argument("--json", action="store_true", help="output JSON")
    b_restore.set_defaults(handler=_backup_restore)

    # 4. MCP Server subcommand
    mcp_parser = subparsers.add_parser("mcp", help="run Model Context Protocol (MCP) stdio server")
    mcp_parser.set_defaults(handler=_mcp)

    # 5. Legacy low-level commands (for backwards compatibility)
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
    try:
        return args.handler(args)
    except (
        HostKeyMismatchError,
        RemoteDependencyError,
        RemoteScanError,
        OperationError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
