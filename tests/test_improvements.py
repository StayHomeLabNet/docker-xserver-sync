from __future__ import annotations

import io
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from dxsync.auto_sync_policy import normalize_sftp_max_workers
from dxsync.deployment import _ensure_parents
from dxsync.maintenance import (
    disable_wordpress_maintenance,
    enable_wordpress_maintenance,
    enter_maintenance_mode,
    exit_maintenance_mode,
    laravel_app_root,
)
from dxsync.mcp_server import McpServer
from dxsync.mcp_setup import (
    get_client_skill_path,
    get_client_skill_status,
    install_client_skill,
    remove_client_skill,
)
from dxsync.models import Project, SiteType, SyncMapping
from dxsync.operations import execute_auto_sync_all, execute_dry_run_all
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore
from dxsync.updater import select_recommended_asset


def test_mcp_server_emits_progress_notifications(monkeypatch, tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "state.db", secure_store=MemoryHostKeyStore())
    project = Project.create(
        name="example.com",
        site_type=SiteType.STATIC_SITE,
        domain="example.com",
        ssh_host="xs1.xsrv.jp",
        ssh_port=10022,
        ssh_username="xs1",
        host_key_fingerprint="SHA256:abc",
        private_key_reference="/tmp/key",
        mappings=(
            SyncMapping.create(
                name="main",
                local_root="/tmp/dist",
                remote_root="/home/xs1/example.com/public_html",
            ),
        ),
    )
    store.initialize()
    store.save(project)

    def fake_auto_sync(*_args, **kwargs):
        cb = kwargs.get("progress_callback")
        assert cb is not None
        cb(1, 2, "バックアップ中 (1/2): index.html")
        cb(2, 2, "転送中 (2/2): app.js")
        return {"status": "success", "summary": "ok"}

    monkeypatch.setattr("dxsync.mcp_server.execute_auto_sync", fake_auto_sync)

    notifications: list[dict] = []
    server = McpServer(store=store, notify_callback=notifications.append)
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "dxsync_auto_sync",
                "arguments": {"project": "example.com"},
                "_meta": {"progressToken": "tok-1"},
            },
        }
    )
    assert resp is not None
    assert resp["result"]["isError"] is False
    assert len(notifications) == 2
    assert notifications[0]["method"] == "notifications/progress"
    assert notifications[0]["params"]["progressToken"] == "tok-1"
    assert notifications[0]["params"]["progress"] == 1
    assert notifications[1]["params"]["total"] == 2


def test_install_and_remove_client_skill(monkeypatch, tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    assert get_client_skill_status("codex") is False

    ok, _ = install_client_skill("codex")
    assert ok is True
    assert get_client_skill_status("codex") is True
    skill_path = get_client_skill_path("codex")
    assert "docker-xserver-sync" in skill_path.read_text(encoding="utf-8")

    ok, _ = remove_client_skill("codex")
    assert ok is True
    assert get_client_skill_status("codex") is False


def test_ensure_parents_uses_verified_dirs_cache() -> None:
    stat_calls: list[str] = []

    class FakeSftp:
        def stat(self, path: str):
            stat_calls.append(path)
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)

        def mkdir(self, path: str, mode: int = 0o755) -> None:
            raise AssertionError("Should not mkdir existing dir")

    sftp = FakeSftp()
    created: list[str] = []
    verified: set[str] = set()
    _ensure_parents(sftp, "/home/u/site", "/home/u/site/assets/js", created, 0o755, verified_dirs=verified)
    first_count = len(stat_calls)
    _ensure_parents(sftp, "/home/u/site", "/home/u/site/assets/js", created, 0o755, verified_dirs=verified)
    assert len(stat_calls) == first_count


def test_normalize_sftp_max_workers() -> None:
    assert normalize_sftp_max_workers(0) == 1
    assert normalize_sftp_max_workers(8) == 8
    assert normalize_sftp_max_workers(99) == 16
    assert normalize_sftp_max_workers("bad") == 4


def test_select_recommended_asset() -> None:
    assets = [
        {"name": "Docker.XServer.Sync-macOS-arm64.zip", "browser_download_url": "https://example.com/mac.zip"},
        {"name": "Docker-XServer-Sync-Setup-v1.1.6.exe", "browser_download_url": "https://example.com/win.exe"},
        {"name": "Docker.XServer.Sync-Linux-x64.tar.gz", "browser_download_url": "https://example.com/linux.tar.gz"},
    ]
    assert select_recommended_asset(assets, platform="darwin") == (
        "Docker.XServer.Sync-macOS-arm64.zip",
        "https://example.com/mac.zip",
    )
    assert select_recommended_asset(assets, platform="win32") == (
        "Docker-XServer-Sync-Setup-v1.1.6.exe",
        "https://example.com/win.exe",
    )
    assert select_recommended_asset(assets, platform="linux") == (
        "Docker.XServer.Sync-Linux-x64.tar.gz",
        "https://example.com/linux.tar.gz",
    )


def test_execute_dry_run_all_and_auto_sync_all(monkeypatch, tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "state.db", secure_store=MemoryHostKeyStore())
    project = Project.create(
        name="laravel.example.com",
        site_type=SiteType.LARAVEL,
        domain="laravel.example.com",
        ssh_host="xs1.xsrv.jp",
        ssh_port=10022,
        ssh_username="xs1",
        host_key_fingerprint="SHA256:abc",
        private_key_reference="/tmp/key",
        mappings=(
            SyncMapping.create(name="app", local_root="/tmp/app", remote_root="/home/xs1/app"),
            SyncMapping.create(name="public", local_root="/tmp/pub", remote_root="/home/xs1/app/public"),
        ),
    )

    monkeypatch.setattr(
        "dxsync.operations.execute_dry_run",
        lambda _s, _p, mapping_name=None, **_kw: {
            "status": "success",
            "mapping": mapping_name,
            "plan_id": f"plan-{mapping_name}",
            "has_blocking_issues": False,
        },
    )
    dry_all = execute_dry_run_all(store, project)
    assert dry_all["mappings_count"] == 2
    assert dry_all["status"] == "success"

    monkeypatch.setattr(
        "dxsync.operations.execute_auto_sync",
        lambda _s, _p, mapping_name=None, **_kw: {
            "status": "success",
            "mapping": mapping_name,
            "transferred_files": 3,
            "total_bytes": 100,
        },
    )
    sync_all = execute_auto_sync_all(store, project)
    assert sync_all["mappings_count"] == 2
    assert sync_all["transferred_files"] == 6
    assert sync_all["total_bytes"] == 200


def test_wordpress_and_laravel_maintenance_mode() -> None:
    files: dict[str, bytes] = {}

    class FakeSftp:
        def open(self, path: str, mode: str = "r"):
            buf = io.BytesIO()
            original_close = buf.close

            def on_close():
                files[path] = buf.getvalue()
                original_close()

            buf.close = on_close  # type: ignore[method-assign]
            return buf

        def remove(self, path: str) -> None:
            if path not in files:
                raise FileNotFoundError(path)
            del files[path]

    sftp = FakeSftp()
    path = enable_wordpress_maintenance(sftp, "/home/u/wp/public_html")
    assert path == "/home/u/wp/public_html/.maintenance"
    assert b"$upgrading" in files[path]
    assert disable_wordpress_maintenance(sftp, "/home/u/wp/public_html") is True
    assert disable_wordpress_maintenance(sftp, "/home/u/wp/public_html") is False

    # Laravel maintenance via SSH
    ran_cmds: list[str] = []

    class FakeSsh:
        def exec_command(self, cmd: str):
            ran_cmds.append(cmd)
            stdout = MagicMock()
            stdout.channel.recv_exit_status.return_value = 0
            stderr = MagicMock()
            stderr.read.return_value = b""
            return None, stdout, stderr

    pub_mapping = SyncMapping.create(
        name="public",
        local_root="/tmp/pub",
        remote_root="/home/u/laravel/public",
    )
    proj = Project.create(
        name="laravel.test",
        site_type=SiteType.LARAVEL,
        domain="laravel.test",
        ssh_host="xs1.xsrv.jp",
        ssh_port=10022,
        ssh_username="xs1",
        host_key_fingerprint="SHA256:abc",
        private_key_reference="/tmp/key",
        mappings=(pub_mapping,),
    )
    assert laravel_app_root(proj, pub_mapping) == "/home/u/laravel"
    ssh = FakeSsh()
    assert enter_maintenance_mode(ssh, sftp, proj, pub_mapping) is not None
    assert exit_maintenance_mode(ssh, sftp, proj, pub_mapping) is not None
    assert ran_cmds == [
        "cd /home/u/laravel && php artisan down",
        "cd /home/u/laravel && php artisan up",
    ]
