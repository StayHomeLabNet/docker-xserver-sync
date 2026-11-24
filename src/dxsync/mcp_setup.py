from __future__ import annotations

import json
import os
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


def _find_executable(name: str, extra_candidates: tuple[Path, ...] = ()) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in extra_candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _common_bin_candidates(name: str) -> tuple[Path, ...]:
    return (
        Path.home() / ".local" / "bin" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    )


def detect_mcp_launch_spec() -> McpLaunchSpec:
    if is_packaged_executable():
        return McpLaunchSpec(
            command=sys.executable,
            args=("mcp",),
            source="packaged",
        )

    dxsync_exe = _find_executable("dxsync", _common_bin_candidates("dxsync"))
    if dxsync_exe:
        return McpLaunchSpec(
            command=dxsync_exe,
            args=("mcp",),
            source="dxsync-cli",
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
