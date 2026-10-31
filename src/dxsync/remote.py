from __future__ import annotations

import base64
import hashlib
import io
import socket
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Protocol

from .models import EntryKind, FileRecord, ScanIssue
from .rules import PathRules
from .validation import normalize_remote_root


class RemoteDependencyError(RuntimeError):
    pass


class HostKeyMismatchError(RuntimeError):
    pass


class RemoteScanError(RuntimeError):
    pass


class RemoteFileSystem(Protocol):
    def listdir_attr(self, path: str) -> list[Any]: ...
    def open(self, path: str, mode: str = "r") -> BinaryIO: ...


@dataclass(frozen=True)
class RemoteScanResult:
    root: str
    files: tuple[FileRecord, ...]
    issues: tuple[ScanIssue, ...]
    excluded_count: int
    skipped_protected_paths: tuple[str, ...] = ()


def host_key_sha256(key_bytes: bytes) -> str:
    digest = hashlib.sha256(key_bytes).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class FingerprintPolicy:
    """Paramiko MissingHostKeyPolicy-compatible strict fingerprint checker."""

    def __init__(self, expected_fingerprint: str) -> None:
        self.expected = expected_fingerprint.strip()

    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        actual = host_key_sha256(key.asbytes())
        if actual != self.expected:
            raise HostKeyMismatchError(
                f"Host key mismatch for {hostname}: expected {self.expected}, got {actual}"
            )
        client.get_host_keys().add(hostname, key.get_name(), key)


def connect_sftp(
    *,
    host: str,
    port: int,
    username: str,
    private_key_path: str,
    host_key_fingerprint: str,
    passphrase: str | None = None,
    timeout: float = 15.0,
) -> tuple[Any, Any]:
    try:
        import paramiko
    except ImportError as exc:
        raise RemoteDependencyError(
            "SSH support is not installed. Install the 'ssh' optional dependency."
        ) from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(FingerprintPolicy(host_key_fingerprint))
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            key_filename=private_key_path,
            passphrase=passphrase,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        return client, client.open_sftp()
    except (OSError, socket.error, paramiko.SSHException) as exc:
        client.close()
        raise RemoteScanError(f"SSH connection failed: {exc}") from exc


def _hash_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
    return digest.hexdigest()


class SftpScanner:
    def __init__(
        self,
        exclude_patterns: tuple[str, ...] = (),
        skip_patterns: tuple[str, ...] = (),
    ) -> None:
        self._excludes = PathRules(exclude_patterns)
        self._skips = PathRules(skip_patterns)

    def scan(self, sftp: RemoteFileSystem, root: str) -> RemoteScanResult:
        normalized_root = normalize_remote_root(root)
        files: list[FileRecord] = []
        issues: list[ScanIssue] = []
        excluded_count = 0
        skipped_protected_paths: list[str] = []
        pending: list[tuple[str, str]] = [(normalized_root, "")]

        while pending:
            absolute_directory, relative_directory = pending.pop()
            try:
                entries = sorted(sftp.listdir_attr(absolute_directory), key=lambda item: item.filename)
            except OSError as exc:
                label = relative_directory or "."
                issues.append(ScanIssue(label, "unreadable_directory", str(exc)))
                continue

            for entry in entries:
                relative = (
                    f"{relative_directory}/{entry.filename}"
                    if relative_directory
                    else entry.filename
                )
                absolute = str(PurePosixPath(absolute_directory) / entry.filename)
                mode = entry.st_mode
                if stat.S_ISLNK(mode):
                    issues.append(ScanIssue(relative, "symlink", "Symbolic links are not supported"))
                elif stat.S_ISDIR(mode):
                    if self._skips.matches(relative, is_directory=True):
                        skipped_protected_paths.append(relative)
                    elif self._excludes.matches(relative, is_directory=True):
                        excluded_count += 1
                    else:
                        pending.append((absolute, relative))
                elif stat.S_ISREG(mode):
                    if self._skips.matches(relative):
                        skipped_protected_paths.append(relative)
                        continue
                    if self._excludes.matches(relative):
                        excluded_count += 1
                        continue
                    try:
                        with sftp.open(absolute, "rb") as stream:
                            digest = _hash_stream(stream)
                        files.append(
                            FileRecord(
                                relative_path=relative,
                                kind=EntryKind.FILE,
                                size=entry.st_size,
                                modified_ns=(entry.st_mtime * 1_000_000_000),
                                sha256=digest,
                            )
                        )
                    except OSError as exc:
                        issues.append(ScanIssue(relative, "unreadable", str(exc)))
                else:
                    issues.append(ScanIssue(relative, "unsupported_type", "Unsupported remote entry type"))

        return RemoteScanResult(
            root=normalized_root,
            files=tuple(sorted(files, key=lambda item: item.relative_path)),
            issues=tuple(issues),
            excluded_count=excluded_count,
            skipped_protected_paths=tuple(sorted(skipped_protected_paths)),
        )

