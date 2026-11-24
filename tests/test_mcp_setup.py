from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

from dxsync import gui
from dxsync.mcp_setup import (
    MCP_SERVER_NAME,
    McpLaunchSpec,
    detect_mcp_launch_spec,
    format_client_cli_command,
    format_codex_cli_command,
    format_codex_toml,
    format_mcp_json,
    get_client_mcp_status,
    get_codex_mcp_status,
    register_client_mcp,
    register_codex_mcp,
    unregister_client_mcp,
    unregister_codex_mcp,
)


def test_detect_mcp_launch_spec_packaged(monkeypatch, tmp_path: Path) -> None:
    app_exe = tmp_path / "Docker XServer Sync"
    app_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr("dxsync.mcp_setup.is_packaged_executable", lambda: True)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(sys, "argv", [str(app_exe)])
    spec = detect_mcp_launch_spec()
    assert spec.source == "packaged"
    assert spec.command == str(app_exe.resolve())
    assert spec.args == ("mcp",)


def test_detect_mcp_launch_spec_dxsync_cli(monkeypatch) -> None:
    monkeypatch.setattr("dxsync.mcp_setup.is_packaged_executable", lambda: False)
    monkeypatch.setattr(
        "dxsync.mcp_setup._find_executable",
        lambda name, extra=(): "/Users/test/.local/bin/dxsync" if name == "dxsync" else None,
    )
    spec = detect_mcp_launch_spec()
    assert spec.source == "dxsync-cli"
    assert spec.command == "/Users/test/.local/bin/dxsync"
    assert spec.args == ("mcp",)


def test_detect_mcp_launch_spec_uv_project(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("dxsync.mcp_setup.is_packaged_executable", lambda: False)
    monkeypatch.setattr("dxsync.mcp_setup._find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "dxsync.mcp_setup._find_executable",
        lambda name, extra=(): "/Users/test/.local/bin/uv" if name == "uv" else None,
    )
    spec = detect_mcp_launch_spec()
    assert spec.source == "uv-project"
    assert spec.command == "/Users/test/.local/bin/uv"
    assert spec.args == ("--directory", str(tmp_path), "run", "dxsync", "mcp")


def test_format_helpers() -> None:
    spec = McpLaunchSpec(
        command="/usr/local/bin/dxsync",
        args=("mcp",),
        source="dxsync-cli",
    )
    assert format_codex_cli_command(spec) == f"codex mcp add {MCP_SERVER_NAME} -- /usr/local/bin/dxsync mcp"
    assert (
        format_client_cli_command("claude_code", spec)
        == f"claude mcp add -s user {MCP_SERVER_NAME} -- /usr/local/bin/dxsync mcp"
    )
    assert (
        format_client_cli_command("antigravity", spec)
        == f"agy mcp add {MCP_SERVER_NAME} -- /usr/local/bin/dxsync mcp"
    )

    toml_text = format_codex_toml(spec)
    parsed_toml = tomllib.loads(toml_text)
    assert parsed_toml["mcp_servers"][MCP_SERVER_NAME] == {
        "command": "/usr/local/bin/dxsync",
        "args": ["mcp"],
    }

    json_text = format_mcp_json(spec)
    parsed_json = json.loads(json_text)
    assert parsed_json["mcpServers"][MCP_SERVER_NAME] == {
        "command": "/usr/local/bin/dxsync",
        "args": ["mcp"],
    }


def test_register_and_unregister_codex_toml_fallback(monkeypatch, tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_PATH", str(tmp_path / ".claude.json"))
    monkeypatch.setenv("ANTIGRAVITY_MCP_CONFIG_PATH", str(tmp_path / "mcp_config.json"))
    monkeypatch.setattr("dxsync.mcp_setup.find_client_executable", lambda _cid: None)

    registered, _ = get_codex_mcp_status()
    assert registered is False

    spec = McpLaunchSpec(
        command="/opt/dxsync/Docker XServer Sync",
        args=("mcp",),
        source="packaged",
    )
    ok, _ = register_codex_mcp(spec)
    assert ok is True

    registered, detail = get_codex_mcp_status()
    assert registered is True
    assert "/opt/dxsync/Docker XServer Sync" in detail

    config_file = codex_home / "config.toml"
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert data["mcp_servers"][MCP_SERVER_NAME]["args"] == ["mcp"]

    ok, _ = unregister_codex_mcp()
    assert ok is True
    registered_after, _ = get_codex_mcp_status()
    assert registered_after is False


def test_register_and_unregister_claude_and_antigravity_json_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    claude_cfg = tmp_path / ".claude.json"
    agy_cfg = tmp_path / "mcp_config.json"
    claude_cfg.write_text('{"otherSetting": true, "mcpServers": {"keep": {"command": "x"}}}', encoding="utf-8")

    monkeypatch.setenv("CLAUDE_CONFIG_PATH", str(claude_cfg))
    monkeypatch.setenv("ANTIGRAVITY_MCP_CONFIG_PATH", str(agy_cfg))
    monkeypatch.setattr("dxsync.mcp_setup.find_client_executable", lambda _cid: None)

    spec = McpLaunchSpec(command="dxsync", args=("mcp",), source="dxsync-cli")

    for client_id, cfg_path in (("claude_code", claude_cfg), ("antigravity", agy_cfg)):
        registered, _ = get_client_mcp_status(client_id)
        assert registered is False

        ok, _ = register_client_mcp(client_id, spec)
        assert ok is True
        registered, detail = get_client_mcp_status(client_id)
        assert registered is True
        assert "dxsync" in detail

        parsed = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert parsed["mcpServers"][MCP_SERVER_NAME] == {"command": "dxsync", "args": ["mcp"]}

        ok, _ = unregister_client_mcp(client_id)
        assert ok is True
        registered_after, _ = get_client_mcp_status(client_id)
        assert registered_after is False

    # Existing unrelated keys in .claude.json must be preserved
    claude_after = json.loads(claude_cfg.read_text(encoding="utf-8"))
    assert claude_after["otherSetting"] is True
    assert "keep" in claude_after["mcpServers"]


def test_register_clients_via_cli(monkeypatch, tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        f'[mcp_servers.{MCP_SERVER_NAME}]\ncommand = "old"\nargs = ["mcp"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_PATH", str(tmp_path / ".claude.json"))
    monkeypatch.setenv("ANTIGRAVITY_MCP_CONFIG_PATH", str(tmp_path / "mcp_config.json"))
    monkeypatch.setattr(
        "dxsync.mcp_setup.find_client_executable",
        lambda cid: f"/usr/local/bin/{'agy' if cid == 'antigravity' else ('claude' if cid == 'claude_code' else 'codex')}",
    )

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        return proc

    monkeypatch.setattr("dxsync.mcp_setup.subprocess.run", fake_run)

    spec = McpLaunchSpec(command="dxsync", args=("mcp",), source="dxsync-cli")
    assert register_codex_mcp(spec)[0] is True
    assert register_client_mcp("claude_code", spec)[0] is True
    assert register_client_mcp("antigravity", spec)[0] is True

    assert calls == [
        ["/usr/local/bin/codex", "mcp", "remove", MCP_SERVER_NAME],
        ["/usr/local/bin/codex", "mcp", "add", MCP_SERVER_NAME, "--", "dxsync", "mcp"],
        ["/usr/local/bin/claude", "mcp", "add", "-s", "user", MCP_SERVER_NAME, "--", "dxsync", "mcp"],
        ["/usr/local/bin/agy", "mcp", "add", MCP_SERVER_NAME, "--", "dxsync", "mcp"],
    ]


def test_gui_main_dispatches_mcp(monkeypatch) -> None:
    called = False

    def fake_run_stdio_server() -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("dxsync.mcp_server.run_stdio_server", fake_run_stdio_server)
    exit_code = gui.main(["dxsync-gui", "mcp"])
    assert exit_code == 0
    assert called is True
