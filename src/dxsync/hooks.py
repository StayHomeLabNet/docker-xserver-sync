from __future__ import annotations

import json
import shlex
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import certifi


@dataclass(frozen=True)
class HookResult:
    ok: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""


def normalize_allowed_delete_folders(value: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items = value.replace(",", "\n").splitlines()
    else:
        raw_items = list(value)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        cleaned = item.replace("\\", "/").strip().strip("/")
        if not cleaned:
            continue
        parts = cleaned.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            continue
        folder = cleaned + "/"
        if folder not in seen:
            seen.add(folder)
            normalized.append(folder)
    return tuple(normalized)


def filter_allowed_delete_paths(
    remote_only_paths: Sequence[str],
    allowed_folders: Sequence[str],
) -> tuple[str, ...]:
    normalized_folders = normalize_allowed_delete_folders(allowed_folders)
    if not normalized_folders:
        return ()
    return tuple(
        path
        for path in remote_only_paths
        if any(path.startswith(folder) for folder in normalized_folders)
    )


def run_pre_sync_command(
    command: str,
    cwd: Path,
    *,
    timeout: float = 300.0,
) -> HookResult:
    stripped = command.strip()
    if not stripped:
        return HookResult(ok=True, returncode=0)
    try:
        completed = subprocess.run(
            stripped,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return HookResult(
            ok=False,
            error=f"事前コマンドがタイムアウトしました（{timeout:.0f}秒）: {exc}",
        )
    except OSError as exc:
        return HookResult(ok=False, error=f"事前コマンドを実行できません: {exc}")

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return HookResult(
            ok=False,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            error=(
                f"事前コマンドが終了コード {completed.returncode} で失敗しました"
                + (f": {detail}" if detail else "")
            ),
        )
    return HookResult(
        ok=True,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_post_sync_remote_command(
    ssh_client: Any,
    command: str,
    remote_root: str,
    *,
    timeout: float = 120.0,
) -> HookResult:
    stripped = command.strip()
    if not stripped:
        return HookResult(ok=True, returncode=0)
    full_command = f"cd {shlex.quote(remote_root)} && {stripped}"
    try:
        _stdin, stdout_stream, stderr_stream = ssh_client.exec_command(
            full_command,
            timeout=timeout,
        )
        stdout_text = stdout_stream.read().decode("utf-8", errors="replace")
        stderr_text = stderr_stream.read().decode("utf-8", errors="replace")
        exit_status = stdout_stream.channel.recv_exit_status()
    except Exception as exc:
        return HookResult(
            ok=False,
            error=f"同期後リモートコマンドの実行に失敗しました: {exc}",
        )
    if exit_status != 0:
        detail = (stderr_text or stdout_text or "").strip()
        return HookResult(
            ok=False,
            returncode=exit_status,
            stdout=stdout_text,
            stderr=stderr_text,
            error=(
                f"同期後リモートコマンドが終了コード {exit_status} で失敗しました"
                + (f": {detail}" if detail else "")
            ),
        )
    return HookResult(
        ok=True,
        returncode=exit_status,
        stdout=stdout_text,
        stderr=stderr_text,
    )


def validate_webhook_url(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        return ""
    parsed = urlparse(stripped)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("Webhook URL は https:// から始まる有効なURLを指定してください。")
    return stripped


def send_post_sync_webhook(
    webhook_url: str,
    *,
    project_name: str,
    domain: str,
    status: str,
    summary: str,
    timeout: float = 10.0,
    opener: Callable[..., Any] | None = None,
) -> HookResult:
    stripped = webhook_url.strip()
    if not stripped:
        return HookResult(ok=True)
    try:
        validated_url = validate_webhook_url(stripped)
    except ValueError as exc:
        return HookResult(ok=False, error=str(exc))

    message = f"[Docker XServer Sync] {project_name} ({domain}) [{status}]\n{summary}"
    payload = json.dumps({"text": message, "content": message}, ensure_ascii=False).encode(
        "utf-8"
    )
    request = Request(
        validated_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Docker-XServer-Sync-Webhook",
        },
    )
    try:
        if opener is None:
            context = ssl.create_default_context(cafile=certifi.where())
            response_ctx = urlopen(request, timeout=timeout, context=context)
        else:
            response_ctx = opener(request, timeout=timeout)
        with response_ctx as response:
            status_code = getattr(response, "status", 200)
            return HookResult(ok=200 <= int(status_code) < 300, returncode=int(status_code))
    except HTTPError as exc:
        return HookResult(
            ok=False,
            returncode=exc.code,
            error=f"Webhook 通知に失敗しました (HTTP {exc.code})",
        )
    except (URLError, OSError) as exc:
        return HookResult(ok=False, error=f"Webhook 通知に失敗しました: {exc}")
