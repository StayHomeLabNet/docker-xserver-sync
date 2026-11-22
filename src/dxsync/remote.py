from __future__ import annotations

import base64
import hashlib
import re
import socket
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Callable, Protocol, Sequence

from .cancellation import CancellationToken
from .models import EntryKind, FileRecord, ScanIssue
from .rules import PathRules
from .validation import normalize_remote_root

_SHA256_LINE_RE = re.compile(r"^([0-9a-f]{64})  (.+)$")
_REMOTE_HASH_BATCH_SIZE = 200
_SSH_EXEC_TIMEOUT = 60.0


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


def _hash_stream(
    stream: BinaryIO,
    chunk_size: int = 1024 * 1024,
    cancellation_token: CancellationToken | None = None,
) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(chunk_size):
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        digest.update(chunk)
    return digest.hexdigest()


def _exec_ssh_with_stdin(
    sftp: Any,
    command: str,
    stdin_bytes: bytes,
    *,
    timeout: float = _SSH_EXEC_TIMEOUT,
) -> tuple[int, str, str] | None:
    """Run a command on the SSH transport backing `sftp`, or return None if unavailable."""
    get_channel = getattr(sftp, "get_channel", None)
    if not callable(get_channel):
        return None
    channel = get_channel()
    get_transport = getattr(channel, "get_transport", None)
    if not callable(get_transport):
        return None
    transport = get_transport()
    if transport is None:
        return None
    exec_channel = transport.open_session(timeout=timeout)
    try:
        exec_channel.settimeout(timeout)
        exec_channel.exec_command(command)
        if stdin_bytes:
            exec_channel.sendall(stdin_bytes)
        exec_channel.shutdown_write()
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        while True:
            data = exec_channel.recv(65536)
            if not data:
                break
            stdout_chunks.append(data)
        while True:
            err = exec_channel.recv_stderr(65536)
            if not err:
                break
            stderr_chunks.append(err)
        exit_status = exec_channel.recv_exit_status()
        return (
            exit_status,
            b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        )
    except Exception:
        return None
    finally:
        try:
            exec_channel.close()
        except Exception:
            pass


def batch_hash_remote_files(
    sftp: Any,
    absolute_paths: Sequence[str],
    *,
    cancellation_token: CancellationToken | None = None,
    ssh_exec: Callable[[str, bytes], tuple[int, str, str] | None] | None = None,
) -> dict[str, str]:
    """Compute SHA-256 hashes on the server via `xargs -0 sha256sum` when SSH exec is available.

    Any path containing a newline or NUL byte, or any file omitted from verified
    output, is excluded from the returned mapping so callers fall back to SFTP.
    """
    safe_paths = [
        path for path in absolute_paths if "\n" not in path and "\r" not in path and "\0" not in path
    ]
    if not safe_paths:
        return {}
    expected = set(safe_paths)
    runner = ssh_exec or (lambda cmd, data: _exec_ssh_with_stdin(sftp, cmd, data))
    hashes: dict[str, str] = {}
    for start in range(0, len(safe_paths), _REMOTE_HASH_BATCH_SIZE):
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        chunk = safe_paths[start : start + _REMOTE_HASH_BATCH_SIZE]
        payload = ("\0".join(chunk) + "\0").encode("utf-8")
        result = runner("LC_ALL=C xargs -0 sha256sum", payload)
        if result is None:
            return hashes
        _exit_code, stdout, _stderr = result
        for line in stdout.splitlines():
            match = _SHA256_LINE_RE.match(line)
            if not match:
                continue
            digest, path = match.group(1), match.group(2)
            if path in expected:
                hashes[path] = digest
    return hashes


class SftpScanner:
    def __init__(
        self,
        exclude_patterns: tuple[str, ...] = (),
        skip_patterns: tuple[str, ...] = (),
        ssh_exec: Callable[[str, bytes], tuple[int, str, str] | None] | None = None,
    ) -> None:
        self._excludes = PathRules(exclude_patterns)
        self._skips = PathRules(skip_patterns)
        self._ssh_exec = ssh_exec

    def scan(
        self,
        sftp: RemoteFileSystem,
        root: str,
        *,
        progress_callback: Callable[[int, int | None, str], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> RemoteScanResult:
        normalized_root = normalize_remote_root(root)
        files: list[FileRecord] = []
        issues: list[ScanIssue] = []
        excluded_count = 0
        skipped_protected_paths: list[str] = []
        pending: list[tuple[str, str]] = [(normalized_root, "")]
        candidates: list[tuple[str, str, Any]] = []
        scanned_directories = 0
        report = progress_callback or (lambda _current, _total, _detail: None)
        report(0, None, "")

        while pending:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            absolute_directory, relative_directory = pending.pop()
            scanned_directories += 1
            report(scanned_directories, None, relative_directory or ".")
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
                    candidates.append((absolute, relative, entry))
                else:
                    issues.append(ScanIssue(relative, "unsupported_type", "Unsupported remote entry type"))

        total = len(candidates)
        if total == 0:
            report(0, 0, "")
        for chunk_start in range(0, total, _REMOTE_HASH_BATCH_SIZE):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            chunk = candidates[chunk_start : chunk_start + _REMOTE_HASH_BATCH_SIZE]
            batch_hashes = batch_hash_remote_files(
                sftp,
                [absolute for absolute, _relative, _entry in chunk],
                cancellation_token=cancellation_token,
                ssh_exec=self._ssh_exec,
            )
            for index, (absolute, relative, entry) in enumerate(chunk, start=chunk_start + 1):
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()
                report(index - 1, total, relative)
                try:
                    digest = batch_hashes.get(absolute)
                    if digest is None:
                        with sftp.open(absolute, "rb") as stream:
                            digest = _hash_stream(
                                stream,
                                cancellation_token=cancellation_token,
                            )
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
                report(index, total, relative)

        return RemoteScanResult(
            root=normalized_root,
            files=tuple(sorted(files, key=lambda item: item.relative_path)),
            issues=tuple(issues),
            excluded_count=excluded_count,
            skipped_protected_paths=tuple(sorted(skipped_protected_paths)),
        )

