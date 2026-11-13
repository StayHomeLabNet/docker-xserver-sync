from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dxsync.cli import main
from dxsync.mcp_server import McpServer
from dxsync.models import Project, SiteType, SyncMapping
from dxsync.operations import (
    OperationError,
    find_project,
    get_project_detail,
    list_backups_summary,
    list_projects_summary,
    select_mapping,
)
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore


@pytest.fixture
def sample_project() -> Project:
    mapping = SyncMapping(
        mapping_id="map-1",
        name="site",
        local_root="/tmp/local/site",
        remote_root="/home/user/example.com/public_html",
    )
    return Project.create(
        name="Example Project",
        domain="example.com",
        site_type=SiteType.WORDPRESS,
        ssh_host="sv12345.xserver.jp",
        ssh_port=10022,
        ssh_username="xuser",
        host_key_fingerprint="SHA256:fakefingerprint",
        private_key_reference="/path/to/key",
        mappings=(mapping,),
    )


def test_find_project_and_select_mapping(tmp_path: Path, sample_project: Project) -> None:
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.initialize()
    store.save(sample_project)

    # By exact project_id
    assert find_project(store, sample_project.project_id) is not None
    # By exact name
    assert find_project(store, "Example Project") is not None
    # By case-insensitive name
    assert find_project(store, "example project") is not None
    # By domain
    assert find_project(store, "example.com") is not None
    # Non-existent
    assert find_project(store, "does-not-exist") is None

    # Default mapping
    primary = select_mapping(sample_project)
    assert primary.mapping_id == "map-1"

    # Specific mapping by name
    m = select_mapping(sample_project, "site")
    assert m.mapping_id == "map-1"

    # Specific mapping case-insensitive
    m2 = select_mapping(sample_project, "SITE")
    assert m2.mapping_id == "map-1"

    # Mapping error
    with pytest.raises(OperationError):
        select_mapping(sample_project, "non-existent")


def test_list_projects_and_details(tmp_path: Path, sample_project: Project) -> None:
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.initialize()
    store.save(sample_project)

    summary = list_projects_summary(store)
    assert len(summary) == 1
    assert summary[0]["project_id"] == sample_project.project_id
    assert summary[0]["name"] == "Example Project"
    assert summary[0]["site_type"] == "wordpress"

    detail = get_project_detail(store, sample_project)
    assert detail["project_id"] == sample_project.project_id
    assert len(detail["mappings"]) == 1
    assert detail["mappings"][0]["name"] == "site"

    backups = list_backups_summary(store, sample_project)
    assert backups == []


def test_mcp_server_lifecycle_and_tools(tmp_path: Path, sample_project: Project) -> None:
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.initialize()
    store.save(sample_project)

    server = McpServer(store=store)

    # 1. Initialize
    init_resp = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert init_resp["id"] == 1
    assert "capabilities" in init_resp["result"]
    assert init_resp["result"]["serverInfo"]["name"] == "docker-xserver-sync"

    # 2. Ping
    ping_resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping_resp["id"] == 2
    assert ping_resp["result"] == {}

    # 3. tools/list
    tools_resp = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = tools_resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "dxsync_list_projects" in tool_names
    assert "dxsync_get_project" in tool_names
    assert "dxsync_auto_sync" in tool_names
    assert "dxsync_dry_run" in tool_names

    # 4. tools/call: dxsync_list_projects
    call_list = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "dxsync_list_projects", "arguments": {}},
        }
    )
    assert not call_list.get("isError")
    content_text = call_list["result"]["content"][0]["text"]
    projects_data = json.loads(content_text)
    assert len(projects_data) == 1
    assert projects_data[0]["name"] == "Example Project"

    # 5. tools/call: dxsync_get_project
    call_get = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "dxsync_get_project",
                "arguments": {"project": "Example Project"},
            },
        }
    )
    assert not call_get.get("isError")
    proj_data = json.loads(call_get["result"]["content"][0]["text"])
    assert proj_data["domain"] == "example.com"

    # 6. tools/call: error when project not found
    call_not_found = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "dxsync_get_project",
                "arguments": {"project": "unknown"},
            },
        }
    )
    assert call_not_found["result"]["isError"] is True

    # 7. Unknown tool returns call error
    unknown_tool = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "unknown_tool", "arguments": {}},
        }
    )
    assert unknown_tool["result"]["isError"] is True
    assert "Unknown tool" in unknown_tool["result"]["content"][0]["text"]

    # 8. Unknown JSON-RPC method returns RPC error
    unknown_method = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "unknown_method",
            "params": {},
        }
    )
    assert "error" in unknown_method
    assert unknown_method["error"]["code"] == -32601


def test_cli_project_list_and_show(tmp_path: Path, sample_project: Project, capsys) -> None:
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.initialize()
    store.save(sample_project)

    # Patch get_default_store to point to tmp_path
    with patch("dxsync.cli.get_default_store", return_value=store):
        # 1. project list --json
        res = main(["project", "list", "--json"])
        assert res == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["name"] == "Example Project"

        # 2. project show (found) --json
        res = main(["project", "show", "example.com", "--json"])
        assert res == 0
        captured = capsys.readouterr()
        show_data = json.loads(captured.out)
        assert show_data["project_id"] == sample_project.project_id

        # 3. project show (not found)
        res = main(["project", "show", "nonexistent"])
        assert res == 2
        captured = capsys.readouterr()
        assert "not found" in captured.err or "見つかりません" in captured.err

        # 4. backup list
        res = main(["backup", "list", "example.com", "--json"])
        assert res == 0
        captured = capsys.readouterr()
        backup_data = json.loads(captured.out)
        assert backup_data == []
