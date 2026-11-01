from __future__ import annotations

import argparse
import hashlib
import json
import stat
from datetime import timedelta
from pathlib import PurePosixPath
from uuid import uuid4

from dxsync.backup import RemoteBackupEngine
from dxsync.models import (
    ChangeType,
    EntryKind,
    FileChange,
    FileRecord,
    SyncPlan,
    utc_now,
)
from dxsync.remote import connect_sftp


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def exists(sftp, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def ensure_directory(sftp, path: str) -> None:
    current = PurePosixPath("/")
    for part in PurePosixPath(path).parts[1:]:
        current /= part
        current_text = str(current)
        if not exists(sftp, current_text):
            sftp.mkdir(current_text, mode=0o700)
        if not stat.S_ISDIR(sftp.stat(current_text).st_mode):
            raise RuntimeError(f"Not a directory: {current_text}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=10022)
    parser.add_argument("--username", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--project-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    fixture_root = str(PurePosixPath(args.home) / ".dxsync/test-fixtures/backup-engine")
    fixture_path = str(PurePosixPath(fixture_root) / "fixture.txt")
    backup_root = str(PurePosixPath(args.home) / ".dxsync/backups")
    remote_content = b"dxsync backup smoke test: remote version 1\n"
    local_content = b"dxsync backup smoke test: local version 2\n"
    plan_id = "smoke-" + uuid4().hex

    client, sftp = connect_sftp(
        host=args.host,
        port=args.port,
        username=args.username,
        private_key_path=args.private_key,
        host_key_fingerprint=args.fingerprint,
    )
    try:
        ensure_directory(sftp, fixture_root)
        if exists(sftp, fixture_path):
            raise RuntimeError(
                f"Fixture already exists; refusing to overwrite it: {fixture_path}"
            )
        fixture_temporary = fixture_path + f".{plan_id}.tmp"
        with sftp.open(fixture_temporary, "wb") as stream:
            stream.write(remote_content)
        sftp.rename(fixture_temporary, fixture_path)

        now = utc_now()
        remote_record = FileRecord(
            "fixture.txt",
            EntryKind.FILE,
            len(remote_content),
            1,
            sha256(remote_content),
        )
        local_record = FileRecord(
            "fixture.txt",
            EntryKind.FILE,
            len(local_content),
            1,
            sha256(local_content),
        )
        plan = SyncPlan(
            plan_id=plan_id,
            mapping_id="backup-smoke-test",
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            changes=(
                FileChange(
                    "fixture.txt",
                    ChangeType.UPDATE,
                    local_record,
                    remote_record,
                ),
            ),
            local_snapshot_sha256=local_record.sha256 or "",
            remote_snapshot_sha256=remote_record.sha256 or "",
        )
        result = RemoteBackupEngine().create_backup(
            sftp=sftp,
            plan=plan,
            project_id=args.project_id,
            remote_root=fixture_root,
            backup_root=backup_root,
        )
        with sftp.open(result.files[0].backup_path, "rb") as stream:
            backed_up_content = stream.read()
        if backed_up_content != remote_content:
            raise RuntimeError("Backed-up content differs from the fixture")
        print(
            json.dumps(
                {
                    "status": "success",
                    "plan_id": plan_id,
                    "fixture_path": fixture_path,
                    "backup_directory": result.backup_directory,
                    "manifest_path": result.manifest_path,
                    "file_count": len(result.files),
                    "total_bytes": result.total_bytes,
                    "sha256": result.files[0].sha256,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
