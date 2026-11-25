from __future__ import annotations

import json
import os
import plistlib
import stat
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
    verify_mcp_launch_spec,
)
from dxsync.models import Project, SiteType, SyncMapping
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore, get_default_state_directory


def test_detect_mcp_launch_spec_packaged_macos_bundle(monkeypatch, tmp_path: Path) -> None:
    app_bundle = tmp_path / "Docker XServer Sync.app"
    macos_dir = app_bundle / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    (app_bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleExecutable": "Docker XServer Sync"})
    )
    python_lib = macos_dir / "Python"
    python_lib.write_text("", encoding="utf-8")
    app_exe = macos_dir / "Docker XServer Sync"
    app_exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    app_exe.chmod(app_exe.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr("dxsync.mcp_setup.is_packaged_executable", lambda: True)
    monkeypatch.setattr("dxsync.mcp_setup._find_executable", lambda *_a, **_kw: None)
    monkeypatch.setattr(sys, "executable", str(python_lib))
    monkeypatch.setattr(sys, "argv", [str(python_lib)])
    spec = detect_mcp_launch_spec()
    assert spec.source == "packaged"
    assert spec.command == str(app_exe)
    assert spec.args == ("mcp",)


def test_detect_mcp_launch_spec_prefers_dxsync_cli_even_when_packaged(monkeypatch) -> None:
    monkeypatch.setattr("dxsync.mcp_setup.is_packaged_executable", lambda: True)
    monkeypatch.setattr(
        "dxsync.mcp_setup._find_executable",
        lambda name, extra=(): "/Users/test/.local/bin/dxsync" if name == "dxsync" else None,
    )
    spec = detect_mcp_launch_spec()
    assert spec.source == "dxsync-cli"
    assert spec.command == "/Users/test/.local/bin/dxsync"
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


def test_verify_mcp_launch_spec_checks_permissions_and_response(tmp_path: Path) -> None:
    missing = McpLaunchSpec(command=str(tmp_path / "missing"), args=("mcp",), source="packaged")
    ok, msg = verify_mcp_launch_spec(missing)
    assert ok is False
    assert "見つかりません" in msg

    if os.name != "nt":
        non_exec = tmp_path / "Python"
        non_exec.write_text("not executable", encoding="utf-8")
        non_exec.chmod(0o644)
        ok, msg = verify_mcp_launch_spec(
            McpLaunchSpec(command=str(non_exec), args=("mcp",), source="packaged")
        )
        assert ok is False
        assert "実行権限がありません" in msg

    spec = McpLaunchSpec(command=sys.executable, args=("-m", "dxsync.cli", "mcp"), source="python-module")
    ok, msg = verify_mcp_launch_spec(spec)
    assert ok is True
    assert "確認しました" in msg


def test_get_default_state_directory_migrates_legacy_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DXSYNC_STATE_DIR", raising=False)
    legacy_dir = tmp_path / "Docker XServer Sync"
    canonical_dir = legacy_dir / "Docker XServer Sync"
    legacy_dir.mkdir(parents=True)

    legacy_store = ProjectStore(legacy_dir / "state.db", secure_store=MemoryHostKeyStore())
    legacy_store.save(
        Project.create(
            name="stayhomelab.net",
            site_type=SiteType.STATIC_SITE,
            domain="stayhomelab.net",
            ssh_host="xs1.xsrv.jp",
            ssh_port=10022,
            ssh_username="xs1",
            host_key_fingerprint="SHA256:test",
            private_key_reference="/tmp/key",
            mappings=(
                SyncMapping.create(
                    name="stayhomelab.net",
                    local_root="/tmp/dist",
                    remote_root="/home/xs1/stayhomelab.net/public_html",
                ),
            ),
        )
    )

    monkeypatch.setattr("dxsync.storage._resolve_canonical_state_directory", lambda: canonical_dir)

    resolved = get_default_state_directory()
    assert resolved == canonical_dir
    assert (canonical_dir / "state.db").is_file()
    assert (legacy_dir / "state.db.migrated").is_file()

    migrated_store = ProjectStore(canonical_dir / "state.db", secure_store=MemoryHostKeyStore())
    assert len(migrated_store.list_projects()) == 1
    assert migrated_store.list_projects()[0].name == "stayhomelab.net"
