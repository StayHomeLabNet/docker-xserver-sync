from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import PurePosixPath
from uuid import uuid4

from dxsync.remote import connect_sftp
from dxsync.restore import RemoteRestoreEngine, RestoreAuthorization


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def exists(sftp, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def read_file(sftp, path: str) -> bytes:
    with sftp.open(path, "rb") as stream:
        return stream.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=10022)
    parser.add_argument("--username", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--fixture-root", required=True)
    parser.add_argument("--manifest", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    fixture_path = str(PurePosixPath(args.fixture_root) / "fixture.txt")
    expected_original = b"dxsync backup smoke test: remote version 1\n"
    deployed_content = b"dxsync backup smoke test: local version 2\n"
    test_id = uuid4().hex
    new_temporary = fixture_path + f".smoke-{test_id}.new"
    original_temporary = fixture_path + f".smoke-{test_id}.original"

    client, sftp = connect_sftp(
        host=args.host,
        port=args.port,
        username=args.username,
        private_key_path=args.private_key,
        host_key_fingerprint=args.fingerprint,
    )
    changed_fixture = False
    try:
        current = read_file(sftp, fixture_path)
        if current != expected_original:
            raise RuntimeError("Fixture has unexpected content; refusing to overwrite it")
        if exists(sftp, new_temporary) or exists(sftp, original_temporary):
            raise RuntimeError("Restore smoke-test temporary file already exists")

        with sftp.open(new_temporary, "wb") as stream:
            stream.write(deployed_content)
        if read_file(sftp, new_temporary) != deployed_content:
            raise RuntimeError("Could not verify temporary deployed content")
        sftp.rename(fixture_path, original_temporary)
        sftp.rename(new_temporary, fixture_path)
        changed_fixture = True

        backup_id = str(json.loads(read_file(sftp, args.manifest))["backup_id"])
        result = RemoteRestoreEngine().restore(
            sftp=sftp,
            manifest_path=args.manifest,
            destination_root=args.fixture_root,
            expected_current_hashes={"fixture.txt": sha256(deployed_content)},
            authorization=RestoreAuthorization(
                backup_id=backup_id,
                destination_root=args.fixture_root,
                approved=True,
            ),
        )
        restored = read_file(sftp, fixture_path)
        if restored != expected_original:
            raise RuntimeError("Restored fixture does not match the original content")
        sftp.remove(original_temporary)
        changed_fixture = False
        print(
            json.dumps(
                {
                    "status": "success",
                    "backup_id": result.backup_id,
                    "fixture_path": fixture_path,
                    "restored_files": len(result.files),
                    "total_bytes": result.total_bytes,
                    "restored_sha256": result.files[0].restored_sha256,
                    "temporary_files_remaining": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if exists(sftp, new_temporary):
            sftp.remove(new_temporary)
        if changed_fixture and exists(sftp, original_temporary):
            if exists(sftp, fixture_path):
                sftp.remove(fixture_path)
            sftp.rename(original_temporary, fixture_path)
        sftp.close()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
