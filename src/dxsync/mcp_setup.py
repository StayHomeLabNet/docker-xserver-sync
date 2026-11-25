from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MCP_SERVER_NAME = "docker-xserver-sync"
ClientId = Literal["codex", "claude_code", "antigravity"]


@dataclass(frozen=True)
class McpLaunchSpec:
    command: str
    args: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class McpClientSpec:
    client_id: ClientId
    display_name: str
    cli_name: str
    add_prefix: tuple[str, ...]
    remove_prefix: tuple[str, ...]
    config_format: Literal["toml", "json"]


MCP_CLIENTS: dict[ClientId, McpClientSpec] = {
    "codex": McpClientSpec(
        client_id="codex",
        display_name="Codex",
        cli_name="codex",
        add_prefix=("mcp", "add", MCP_SERVER_NAME, "--"),
        remove_prefix=("mcp", "remove", MCP_SERVER_NAME),
        config_format="toml",
    ),
    "claude_code": McpClientSpec(
        client_id="claude_code",
        display_name="Claude Code",
        cli_name="claude",
        add_prefix=("mcp", "add", "-s", "user", MCP_SERVER_NAME, "--"),
        remove_prefix=("mcp", "remove", "-s", "user", MCP_SERVER_NAME),
        config_format="json",
    ),
    "antigravity": McpClientSpec(
        client_id="antigravity",
        display_name="Antigravity",
        cli_name="agy",
        add_prefix=("mcp", "add", MCP_SERVER_NAME, "--"),
        remove_prefix=("mcp", "remove", MCP_SERVER_NAME),
        config_format="json",
    ),
}


def is_packaged_executable() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _find_repo_root(start_path: Path | None = None) -> Path | None:
    current = (start_path or Path(__file__).resolve()).parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "dxsync" / "mcp_server.py"
        ).is_file():
            return candidate
    return None


def _is_executable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        return True
    return os.access(path, os.X_OK)


def _find_executable(name: str, extra_candidates: tuple[Path, ...] = ()) -> str | None:
    found = shutil.which(name)
    if found and _is_executable_file(Path(found)):
        return found
    for candidate in extra_candidates:
        if _is_executable_file(candidate):
            return str(candidate)
    return None


def _common_bin_candidates(name: str) -> tuple[Path, ...]:
    return (
        Path.home() / ".local" / "bin" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    )


def _resolve_macos_bundle_executable(start_path: Path) -> str | None:
    for parent in (start_path, *start_path.parents):
        if parent.name.endswith(".app"):
            plist_path = parent / "Contents" / "Info.plist"
            if plist_path.is_file():
                try:
                    info = plistlib.loads(plist_path.read_bytes())
                    bundle_exec = info.get("CFBundleExecutable")
                    if isinstance(bundle_exec, str) and bundle_exec:
                        candidate = parent / "Contents" / "MacOS" / bundle_exec
                        if _is_executable_file(candidate):
                            return str(candidate)
                except Exception:
                    pass
            fallback = parent / "Contents" / "MacOS" / parent.stem
            if _is_executable_file(fallback):
                return str(fallback)
    return None


def _resolve_packaged_executable() -> str:
    python_names = {"python", "python3", "python.exe"}
    candidates: list[Path] = []
    if sys.argv and sys.argv[0]:
        candidates.append(Path(sys.argv[0]).resolve())
    candidates.append(Path(sys.executable))

    for candidate in candidates:
        bundle_exe = _resolve_macos_bundle_executable(candidate)
        if bundle_exe:
            return bundle_exe

    for candidate in candidates:
        if candidate.name.lower() not in python_names and _is_executable_file(candidate):
            return str(candidate)

    return str(Path(sys.executable))


def detect_mcp_launch_spec() -> McpLaunchSpec:
    dxsync_exe = _find_executable("dxsync", _common_bin_candidates("dxsync"))
    if dxsync_exe:
        return McpLaunchSpec(
            command=dxsync_exe,
            args=("mcp",),
            source="dxsync-cli",
        )

    if is_packaged_executable():
        return McpLaunchSpec(
            command=_resolve_packaged_executable(),
            args=("mcp",),
            source="packaged",
        )

    repo_root = _find_repo_root()
    uv_exe = _find_executable("uv", _common_bin_candidates("uv"))
    if repo_root and uv_exe:
        return McpLaunchSpec(
            command=uv_exe,
            args=("--directory", str(repo_root), "run", "dxsync", "mcp"),
            source="uv-project",
        )

    return McpLaunchSpec(
        command=sys.executable,
        args=("-m", "dxsync.cli", "mcp"),
        source="python-module",
    )


def verify_mcp_launch_spec(spec: McpLaunchSpec, timeout: float = 10.0) -> tuple[bool, str]:
    cmd_path = Path(spec.command)
    if not cmd_path.is_file():
        resolved = shutil.which(spec.command)
        if not resolved:
            return False, f"起動コマンドが見つかりません: {spec.command}"
        cmd_path = Path(resolved)

    if not _is_executable_file(cmd_path):
        return (
            False,
            f"実行権限がありません: {cmd_path}\n"
            "実行権限を付与するか、dxsync CLI を導入してください。",
        )

    init_request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            }
        )
        + "\n"
    )
    try:
        proc = subprocess.run(
            [str(cmd_path), *spec.args],
            input=init_request,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"MCP サーバーの起動テストに失敗しました: {exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return (
            False,
            f"MCP サーバーがエラー終了しました (exit {proc.returncode}): "
            f"{detail or '詳細不明'}",
        )

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id") == 1 and "result" in payload:
            server_info = payload["result"].get("serverInfo", {})
            version = server_info.get("version", "")
            suffix = f" (v{version})" if version else ""
            return True, f"MCP サーバーの起動と応答を確認しました{suffix}。"

    return False, "MCP サーバーから有効な initialize 応答が返りませんでした。"


def _join_shell_command(parts: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def format_client_cli_command(client_id: ClientId, spec: McpLaunchSpec) -> str:
    client = MCP_CLIENTS[client_id]
    return _join_shell_command(
        [client.cli_name, *client.add_prefix, spec.command, *spec.args]
    )


def format_codex_cli_command(spec: McpLaunchSpec) -> str:
    return format_client_cli_command("codex", spec)


def format_codex_toml(spec: McpLaunchSpec) -> str:
    cmd_literal = json.dumps(spec.command, ensure_ascii=False)
    args_literal = json.dumps(list(spec.args), ensure_ascii=False)
    return (
        f"[mcp_servers.{MCP_SERVER_NAME}]\n"
        f"command = {cmd_literal}\n"
        f"args = {args_literal}\n"
    )


def format_mcp_json(spec: McpLaunchSpec) -> str:
    payload = {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": spec.command,
                "args": list(spec.args),
            }
        }
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def find_client_executable(client_id: ClientId) -> str | None:
    cli_name = MCP_CLIENTS[client_id].cli_name
    return _find_executable(cli_name, _common_bin_candidates(cli_name))


def find_codex_executable() -> str | None:
    return find_client_executable("codex")


def get_codex_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home) if codex_home else (Path.home() / ".codex")
    return base / "config.toml"


def get_client_config_path(client_id: ClientId) -> Path:
    if client_id == "codex":
        return get_codex_config_path()
    if client_id == "claude_code":
        override = os.environ.get("CLAUDE_CONFIG_PATH")
        return Path(override) if override else (Path.home() / ".claude.json")
    override = os.environ.get("ANTIGRAVITY_MCP_CONFIG_PATH")
    return (
        Path(override)
        if override
        else (Path.home() / ".gemini" / "antigravity" / "mcp_config.json")
    )


def get_client_mcp_status(client_id: ClientId) -> tuple[bool, str]:
    client = MCP_CLIENTS[client_id]
    config_path = get_client_config_path(client_id)
    if not config_path.is_file():
        return False, f"未登録（{config_path} が未作成）"
    try:
        raw = config_path.read_text(encoding="utf-8")
        if client.config_format == "toml":
            data = tomllib.loads(raw)
            servers = data.get("mcp_servers")
        else:
            data = json.loads(raw) if raw.strip() else {}
            servers = data.get("mcpServers") if isinstance(data, dict) else None
    except (OSError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        return False, f"設定ファイル読み込みエラー: {exc}"

    if isinstance(servers, dict) and MCP_SERVER_NAME in servers:
        entry = servers[MCP_SERVER_NAME]
        if isinstance(entry, dict):
            cmd = entry.get("command", "")
            return True, f"登録済み（{cmd}）"
        return True, "登録済み"
    return False, "未登録"


def get_codex_mcp_status() -> tuple[bool, str]:
    return get_client_mcp_status("codex")


def _remove_toml_section(text: str, section_name: str) -> str:
    target_header = f"[{section_name}]"
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if stripped == target_header:
                skipping = True
                continue
            if skipping:
                skipping = False
        if not skipping:
            result.append(line)
    return "".join(result)


def _upsert_codex_toml(spec: McpLaunchSpec) -> Path:
    config_path = get_codex_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    cleaned = _remove_toml_section(existing, f"mcp_servers.{MCP_SERVER_NAME}").rstrip()
    section = format_codex_toml(spec)
    new_text = f"{cleaned}\n\n{section}" if cleaned else section
    config_path.write_text(new_text, encoding="utf-8")
    return config_path


def _upsert_json_mcp_config(config_path: Path, spec: McpLaunchSpec) -> Path:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if config_path.is_file():
        raw = config_path.read_text(encoding="utf-8").strip()
        if raw:
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise ValueError(f"{config_path} のルートは JSON オブジェクトである必要があります。")
            data = loaded
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    servers[MCP_SERVER_NAME] = {
        "command": spec.command,
        "args": list(spec.args),
    }
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_path


def _remove_json_mcp_config(config_path: Path) -> bool:
    if not config_path.is_file():
        return False
    raw = config_path.read_text(encoding="utf-8").strip()
    if not raw:
        return False
    data = json.loads(raw)
    if not isinstance(data, dict):
        return False
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or MCP_SERVER_NAME not in servers:
        return False
    del servers[MCP_SERVER_NAME]
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def register_client_mcp(client_id: ClientId, spec: McpLaunchSpec) -> tuple[bool, str]:
    client = MCP_CLIENTS[client_id]
    cli_exe = find_client_executable(client_id)
    if cli_exe:
        registered, _ = get_client_mcp_status(client_id)
        if registered:
            subprocess.run(
                [cli_exe, *client.remove_prefix],
                capture_output=True,
                text=True,
                check=False,
            )
        proc = subprocess.run(
            [cli_exe, *client.add_prefix, spec.command, *spec.args],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return True, f"{client.cli_name} mcp add で {client.display_name} に登録しました。"
        err = (proc.stderr or proc.stdout or "").strip()
        return (
            False,
            f"{client.cli_name} mcp add に失敗しました: {err or f'exit {proc.returncode}'}",
        )

    config_path = get_client_config_path(client_id)
    try:
        if client.config_format == "toml":
            _upsert_codex_toml(spec)
        else:
            _upsert_json_mcp_config(config_path, spec)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return False, f"{config_path} の更新に失敗しました: {exc}"
    return True, f"{config_path} に {client.display_name} 用 MCP 設定を書き込みました。"


def unregister_client_mcp(client_id: ClientId) -> tuple[bool, str]:
    client = MCP_CLIENTS[client_id]
    cli_exe = find_client_executable(client_id)
    if cli_exe:
        proc = subprocess.run(
            [cli_exe, *client.remove_prefix],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return True, f"{client.display_name} から登録を解除しました。"
        err = (proc.stderr or proc.stdout or "").strip()
        return False, f"登録解除に失敗しました: {err or f'exit {proc.returncode}'}"

    config_path = get_client_config_path(client_id)
    if not config_path.is_file():
        return True, "すでに未登録です。"
    try:
        if client.config_format == "toml":
            existing = config_path.read_text(encoding="utf-8")
            cleaned = _remove_toml_section(
                existing, f"mcp_servers.{MCP_SERVER_NAME}"
            ).rstrip()
            config_path.write_text((cleaned + "\n") if cleaned else "", encoding="utf-8")
        else:
            _remove_json_mcp_config(config_path)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"{config_path} の更新に失敗しました: {exc}"
    return True, f"{config_path} から登録を解除しました。"


def register_codex_mcp(spec: McpLaunchSpec) -> tuple[bool, str]:
    return register_client_mcp("codex", spec)


def unregister_codex_mcp() -> tuple[bool, str]:
    return unregister_client_mcp("codex")


_FALLBACK_SKILL_MD = """---
name: docker-xserver-sync
description: >-
  Docker開発環境からXServer本番・ステージング環境への安全な同期・デプロイ・ドライラン・ロールバックを操作するスキル。
---

# Docker XServer Sync AI 操作スキル

- 同期・変更前にまず `dxsync_list_projects` と `dxsync_dry_run` で対象プロジェクトと差分を確認する。
- 安全同期を実行する場合は `dxsync_auto_sync` を使用する（ドライラン→計画再検証→バックアップ→アトミック転送→HTTPSヘルスチェック）。
- `REMOTE_ONLY` ファイルの削除は明示的な指定がある場合のみ `remote_only_policy="ask"` と `delete_paths` を使う。
"""


def get_client_skill_path(client_id: ClientId) -> Path:
    if client_id == "codex":
        codex_home = os.environ.get("CODEX_HOME")
        base = Path(codex_home) if codex_home else (Path.home() / ".codex")
        return base / "skills" / MCP_SERVER_NAME / "SKILL.md"
    if client_id == "claude_code":
        return Path.home() / ".claude" / "skills" / MCP_SERVER_NAME / "SKILL.md"
    return Path.home() / ".gemini" / "antigravity" / "skills" / MCP_SERVER_NAME / "SKILL.md"


def get_client_skill_status(client_id: ClientId) -> bool:
    return get_client_skill_path(client_id).is_file()


def _load_skill_markdown() -> str:
    repo_root = _find_repo_root()
    if repo_root is not None:
        source_skill = repo_root / "skills" / MCP_SERVER_NAME / "SKILL.md"
        if source_skill.is_file():
            return source_skill.read_text(encoding="utf-8")
    return _FALLBACK_SKILL_MD


def install_client_skill(client_id: ClientId) -> tuple[bool, str]:
    target_path = get_client_skill_path(client_id)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(_load_skill_markdown(), encoding="utf-8")
    except OSError as exc:
        return False, f"スキルファイルの配置に失敗しました ({target_path}): {exc}"
    return True, f"スキルを {target_path} に配置しました。"


def remove_client_skill(client_id: ClientId) -> tuple[bool, str]:
    target_path = get_client_skill_path(client_id)
    if not target_path.is_file():
        return True, "スキルは未配置です。"
    try:
        target_path.unlink()
        if target_path.parent.is_dir() and not any(target_path.parent.iterdir()):
            target_path.parent.rmdir()
    except OSError as exc:
        return False, f"スキルの削除に失敗しました ({target_path}): {exc}"
    return True, f"スキルを {target_path} から削除しました。"
