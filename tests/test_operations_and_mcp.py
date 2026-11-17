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


class _InMemorySftpWriter:
    def __init__(self, sftp: "_InMemorySftp", path: str) -> None:
        self._sftp = sftp
        self._path = path
        self._chunks: list[bytes] = []
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def write(self, data: bytes) -> int:
        self._chunks.append(data)
        return len(data)

    def close(self) -> None:
        if not self._closed:
            self._sftp.files[self._path] = b"".join(self._chunks)
            self._sftp.modes.setdefault(self._path, 0o644)
            self._closed = True


class _InMemorySftp:
    def __init__(self, initial_files: dict[str, bytes] | None = None) -> None:
        import posixpath

        self.posixpath = posixpath
        self.dirs: set[str] = {"/", "/home", "/home/xuser", "/home/xuser/example.com", "/home/xuser/example.com/public_html"}
        self.files: dict[str, bytes] = {}
        self.modes: dict[str, int] = {}
        self.fail_remove_paths: set[str] = set()
        self.closed = False
        for path, content in (initial_files or {}).items():
            self._ensure_parents(path)
            self.files[path] = content
            self.modes[path] = 0o644

    def _ensure_parents(self, path: str) -> None:
        parent = self.posixpath.dirname(path)
        while parent and parent not in self.dirs:
            self.dirs.add(parent)
            if parent == "/":
                break
            parent = self.posixpath.dirname(parent)

    def stat(self, path: str):
        import stat as stat_mod
        from types import SimpleNamespace

        if path in self.files:
            return SimpleNamespace(
                st_mode=stat_mod.S_IFREG | self.modes.get(path, 0o644),
                st_size=len(self.files[path]),
                st_mtime=1,
            )
        if path in self.dirs:
            return SimpleNamespace(
                st_mode=stat_mod.S_IFDIR | self.modes.get(path, 0o755),
                st_size=0,
                st_mtime=1,
            )
        raise FileNotFoundError(path)

    def listdir_attr(self, path: str):
        import stat as stat_mod
        from types import SimpleNamespace

        if path not in self.dirs:
            raise FileNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        entries = []
        seen_names: set[str] = set()
        for d in sorted(self.dirs):
            if d != path and d.startswith(prefix):
                rest = d[len(prefix):]
                if "/" not in rest and rest and rest not in seen_names:
                    seen_names.add(rest)
                    entries.append(
                        SimpleNamespace(
                            filename=rest,
                            st_mode=stat_mod.S_IFDIR | self.modes.get(d, 0o755),
                            st_size=0,
                            st_mtime=1,
                        )
                    )
        for f, content in sorted(self.files.items()):
            if f.startswith(prefix):
                rest = f[len(prefix):]
                if "/" not in rest and rest and rest not in seen_names:
                    seen_names.add(rest)
                    entries.append(
                        SimpleNamespace(
                            filename=rest,
                            st_mode=stat_mod.S_IFREG | self.modes.get(f, 0o644),
                            st_size=len(content),
                            st_mtime=1,
                        )
                    )
        return entries

    def open(self, path: str, mode: str = "r"):
        import io

        if "w" in mode:
            self._ensure_parents(path)
            return _InMemorySftpWriter(self, path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return io.BytesIO(self.files[path])

    def mkdir(self, path: str, mode: int = 0o755) -> None:
        self.dirs.add(path)
        self.modes[path] = mode

    def remove(self, path: str) -> None:
        if path in self.fail_remove_paths:
            raise OSError("permission denied")
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]
        self.modes.pop(path, None)

    def rename(self, oldpath: str, newpath: str) -> None:
        self._ensure_parents(newpath)
        self.files[newpath] = self.files.pop(oldpath)
        if oldpath in self.modes:
            self.modes[newpath] = self.modes.pop(oldpath)

    def rmdir(self, path: str) -> None:
        self.dirs.discard(path)

    def chmod(self, path: str, mode: int) -> None:
        self.modes[path] = mode

    def close(self) -> None:
        self.closed = True


def _make_local_project(tmp_path: Path, *, site_type: SiteType = SiteType.WORDPRESS, runtime_paths_confirmed: bool = True) -> tuple[ProjectStore, Project, Path]:
    local_root = tmp_path / "local_site"
    local_root.mkdir(parents=True, exist_ok=True)
    (local_root / "index.php").write_bytes(b"<?php echo 'new';")
    mapping = SyncMapping(
        mapping_id="map-1",
        name="site",
        local_root=str(local_root),
        remote_root="/home/xuser/example.com/public_html",
        runtime_paths_confirmed=runtime_paths_confirmed,
    )
    project = Project.create(
        name="Example Project",
        domain="example.com",
        site_type=site_type,
        ssh_host="sv12345.xserver.jp",
        ssh_port=10022,
        ssh_username="xuser",
        host_key_fingerprint="SHA256:fakefingerprint",
        private_key_reference="/path/to/key",
        mappings=(mapping,),
    )
    store = ProjectStore(tmp_path / "state.db", MemoryHostKeyStore())
    store.initialize()
    store.save(project)
    return store, project, local_root


def test_execute_dry_run_saves_config_hash_and_enforces_preflight_guards(tmp_path: Path) -> None:
    from dxsync.integrity import project_config_sha256
    from dxsync.operations import execute_dry_run
    from dxsync.revalidation import PlanRevalidator

    store, project, _ = _make_local_project(tmp_path)
    sftp = _InMemorySftp({"/home/xuser/example.com/public_html/index.php": b"<?php echo 'old';"})
    client = MagicMock()

    with patch("dxsync.operations.connect_sftp", return_value=(client, sftp)):
        res = execute_dry_run(store, project, staging_parent=tmp_path / "staging")

    assert res["status"] == "success"
    stored = store.get_plan(res["plan_id"])
    assert stored is not None
    assert stored.plan.config_sha256 == project_config_sha256(project, project.mappings[0])

    reval = PlanRevalidator().revalidate(
        stored=stored,
        project=project,
        mapping=project.mappings[0],
        sftp=sftp,
    )
    assert reval.valid, reval.reasons

    # Custom PHP without runtime_paths_confirmed must be rejected before SFTP connect
    store2, unconfirmed, _ = _make_local_project(
        tmp_path / "unconfirmed",
        site_type=SiteType.CUSTOM_PHP,
        runtime_paths_confirmed=False,
    )
    with pytest.raises(OperationError, match="本番で書き込まれるディレクトリ"):
        execute_dry_run(store2, unconfirmed)

    # Insecure host-key store status must be rejected before SFTP connect
    from dxsync.storage import ProjectSecurityStatus

    with patch.object(
        store,
        "security_status",
        return_value=ProjectSecurityStatus(
            project_id=project.project_id,
            state="legacy",
            backend="sqlite",
            message="not protected",
        ),
    ):
        with pytest.raises(OperationError, match="保護されていないため実行できません"):
            execute_dry_run(store, project)


def test_execute_auto_sync_and_restore_end_to_end(tmp_path: Path) -> None:
    from dxsync.health import HealthCheckResult
    from dxsync.operations import execute_auto_sync, execute_restore

    store, project, _ = _make_local_project(tmp_path)
    remote_file = "/home/xuser/example.com/public_html/index.php"
    sftp = _InMemorySftp({remote_file: b"<?php echo 'old';"})
    client = MagicMock()

    hc_ok = HealthCheckResult(url="https://example.com/", ok=True, status_code=200)
    with (
        patch("dxsync.operations.connect_sftp", return_value=(client, sftp)),
        patch("dxsync.operations.perform_https_health_check", return_value=hc_ok) as hc_mock,
    ):
        sync_res = execute_auto_sync(store, project, staging_parent=tmp_path / "staging")

    hc_mock.assert_called_once_with(domain="example.com", path="/")
    assert sync_res["status"] == "success"
    assert sync_res["transferred_files"] == 1
    assert sync_res["health_check"]["ok"] is True
    assert sftp.files[remote_file] == b"<?php echo 'new';"
    assert store.get_plan(sync_res["plan_id"]).status == "used"

    # Restore from the created backup verifies expected_current_hashes and marks backup restored
    backup_id = sync_res["backup_id"]
    with patch("dxsync.operations.connect_sftp", return_value=(client, sftp)):
        restore_res = execute_restore(store, project, backup_id=backup_id)

    assert restore_res["status"] == "success"
    assert restore_res["restored_files_count"] == 1
    assert sftp.files[remote_file] == b"<?php echo 'old';"
    assert store.get_backup(backup_id).status == "restored"

    # Restoring an already-restored backup must fail cleanly
    with pytest.raises(OperationError, match="not available"):
        execute_restore(store, project, backup_id=backup_id)


def test_execute_auto_sync_handles_failed_health_check_and_invalid_revalidation(tmp_path: Path) -> None:
    from dxsync.health import HealthCheckResult
    from dxsync.operations import execute_auto_sync

    store, project, _ = _make_local_project(tmp_path)
    remote_file = "/home/xuser/example.com/public_html/index.php"
    sftp = _InMemorySftp({remote_file: b"<?php echo 'old';"})
    client = MagicMock()

    hc_fail = HealthCheckResult(
        url="https://example.com/",
        ok=False,
        status_code=503,
        error="HTTP 503",
    )
    with (
        patch("dxsync.operations.connect_sftp", return_value=(client, sftp)),
        patch("dxsync.operations.perform_https_health_check", return_value=hc_fail),
    ):
        sync_res = execute_auto_sync(store, project, staging_parent=tmp_path / "staging")

    assert sync_res["status"] == "warning"
    assert sync_res["health_check"]["ok"] is False
    assert sync_res["health_check"]["status_code"] == 503
    ops = store.list_operations(project.project_id, limit=1)
    assert ops[0].status == "succeeded"
    assert "503" in (ops[0].error or "")


def test_execute_verify_cleanup_records_issues_without_tuple_mutation_error(tmp_path: Path) -> None:
    from dxsync.operations import execute_verify

    store, project, _ = _make_local_project(tmp_path)
    ok_tmp = "/home/xuser/example.com/public_html/a.php.dxsync-1.new"
    fail_tmp = "/home/xuser/example.com/public_html/b.php.dxsync-2.new"
    sftp = _InMemorySftp({ok_tmp: b"1", fail_tmp: b"2"})
    sftp.fail_remove_paths.add(fail_tmp)
    client = MagicMock()

    with patch("dxsync.operations.connect_sftp", return_value=(client, sftp)):
        res = execute_verify(store, project, cleanup=True)

    assert res["temporary_files_count"] == 2
    assert res["cleaned_up_count"] == 1
    assert res["cleaned_up"] == ["a.php.dxsync-1.new"]
    assert any("Failed to remove b.php.dxsync-2.new" in msg for msg in res["issues"])


def test_cli_write_commands_require_yes_in_non_interactive_mode(
    tmp_path: Path, capsys
) -> None:
    store, project, _ = _make_local_project(tmp_path)

    with (
        patch("dxsync.cli.get_default_store", return_value=store),
        patch("dxsync.cli.sys.stdin.isatty", return_value=False),
    ):
        # auto-sync without --yes must fail before SFTP connect
        rc = main(["run", "auto-sync", project.name, "--json"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "--yes" in captured.err

        # backup restore without --yes must fail before SFTP connect
        rc2 = main(["backup", "restore", project.name, "--backup-id", "b-1", "--json"])
        assert rc2 == 2
        captured2 = capsys.readouterr()
        assert "--yes" in captured2.err

