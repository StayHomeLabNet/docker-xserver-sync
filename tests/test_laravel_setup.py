from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dxsync.laravel_setup import (
    LaravelSetupEngine,
    LaravelSetupError,
    LaravelSetupProgress,
    LaravelSetupRequest,
    build_laravel_env,
    detect_laravel_required_php,
    patch_laravel_index_php,
    validate_laravel_setup_request,
)


def make_laravel(root: Path) -> None:
    for d in ("app", "public", "bootstrap"):
        (root / d).mkdir(parents=True)
    (root / "artisan").write_text("#!/usr/bin/env php\n<?php", encoding="utf-8")
    (root / "composer.json").write_text("{}", encoding="utf-8")
    vendor_dir = root / "vendor"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "autoload.php").write_text("<?php", encoding="utf-8")
    (root / "public" / "index.php").write_text(
        "<?php\nrequire __DIR__.'/../vendor/autoload.php';\n"
        "$app = require_once __DIR__.'/../bootstrap/app.php';\n",
        encoding="utf-8",
    )


def test_build_laravel_env() -> None:
    template = """
APP_NAME=OldApp
APP_ENV=local
APP_KEY=base64:existingkey
APP_DEBUG=true
APP_URL=http://localhost

DB_CONNECTION=sqlite
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=old_db
DB_USERNAME=root
DB_PASSWORD=
"""
    result = build_laravel_env(
        template,
        app_name="Demo Site",
        app_url="https://demo.stayhomelab.net",
        db_host="localhost",
        db_name="xsrv_laravel",
        db_user="xsrv_lar_user",
        db_password="Secret#Password!123",
    )

    assert 'APP_NAME="Demo Site"' in result
    assert "APP_ENV=production" in result
    assert "APP_DEBUG=false" in result
    assert "APP_URL=https://demo.stayhomelab.net" in result
    assert "DB_CONNECTION=mysql" in result
    assert "DB_HOST=localhost" in result
    assert "DB_DATABASE=xsrv_laravel" in result
    assert "DB_USERNAME=xsrv_lar_user" in result
    assert 'DB_PASSWORD="Secret#Password!123"' in result
    assert "APP_KEY=base64:existingkey" in result  # Preserves unmanaged keys


def test_patch_laravel_index_php() -> None:
    original = """<?php
use Illuminate\\Http\\Request;
define('LARAVEL_START', microtime(true));

if (file_exists($maintenance = __DIR__.'/../storage/framework/maintenance.php')) {
    require $maintenance;
}

require __DIR__.'/../vendor/autoload.php';

(require_once __DIR__.'/../bootstrap/app.php')
    ->handleRequest(Request::capture());
"""
    app_root = "/home/xsrv123/demo.stayhomelab.net_app"
    public_root = "/home/xsrv123/demo.stayhomelab.net/public_html"

    patched = patch_laravel_index_php(
        original,
        remote_app_root=app_root,
        remote_public_root=public_root,
    )

    # Relative path from public_html to _app is ../../demo.stayhomelab.net_app
    assert "__DIR__ . '/../../demo.stayhomelab.net_app/vendor/autoload.php'" in patched
    assert "__DIR__ . '/../../demo.stayhomelab.net_app/bootstrap/app.php'" in patched
    assert "__DIR__ . '/../../demo.stayhomelab.net_app/storage/framework/maintenance.php'" in patched


def test_validate_laravel_setup_request(tmp_path: Path) -> None:
    laravel_root = tmp_path / "laravel"
    make_laravel(laravel_root)

    req = LaravelSetupRequest(
        local_root=laravel_root,
        remote_app_root="/home/user/app",
        remote_public_root="/home/user/public_html",
        domain="demo.stayhomelab.net",
        app_url="https://demo.stayhomelab.net",
        app_name="Demo",
        db_host="localhost",
        database_mode="api",
        db_name_suffix="laravel",
        db_user_suffix="lar_user",
        db_password="password123",
    )
    validate_laravel_setup_request(req)

    # Test missing vendor
    (laravel_root / "vendor" / "autoload.php").unlink()
    with pytest.raises(LaravelSetupError, match="vendor"):
        validate_laravel_setup_request(req)

    # Restore vendor
    (laravel_root / "vendor" / "autoload.php").write_text("<?php", encoding="utf-8")

    # Test same roots
    same_roots_req = LaravelSetupRequest(
        local_root=laravel_root,
        remote_app_root="/home/user/app",
        remote_public_root="/home/user/app",
        domain="demo.stayhomelab.net",
        app_url="https://demo.stayhomelab.net",
        app_name="Demo",
        db_host="localhost",
        database_mode="api",
        db_name_suffix="laravel",
        db_user_suffix="lar_user",
        db_password="password123",
    )
    with pytest.raises(LaravelSetupError, match="must not be the same"):
        validate_laravel_setup_request(same_roots_req)

    # Test invalid SQL dump
    bad_sql = tmp_path / "bad.sql"
    bad_sql.write_text("CREATE DATABASE test_db;\n", encoding="utf-8")
    sql_req = LaravelSetupRequest(
        local_root=laravel_root,
        remote_app_root="/home/user/app",
        remote_public_root="/home/user/public_html",
        domain="demo.stayhomelab.net",
        app_url="https://demo.stayhomelab.net",
        app_name="Demo",
        db_host="localhost",
        database_mode="api",
        db_name_suffix="laravel",
        db_user_suffix="lar_user",
        db_password="password123",
        migration_mode="sql_dump",
        sql_dump=bad_sql,
    )
    with pytest.raises(LaravelSetupError, match="forbidden database-level"):
        validate_laravel_setup_request(sql_req)


def test_detect_laravel_required_php(tmp_path: Path) -> None:
    laravel_root = tmp_path / "laravel"
    make_laravel(laravel_root)
    assert detect_laravel_required_php(laravel_root) is None

    # Detect from platform_check.php
    platform_dir = laravel_root / "vendor" / "composer"
    platform_dir.mkdir(parents=True, exist_ok=True)
    (platform_dir / "platform_check.php").write_text(
        '<?php if (!(PHP_VERSION_ID >= 80401)) { throw new RuntimeException("..."); }',
        encoding="utf-8",
    )
    assert detect_laravel_required_php(laravel_root) == "8.4"

    # Incompatible php_cli_version should fail validation
    req = LaravelSetupRequest(
        local_root=laravel_root,
        remote_app_root="/home/user/app",
        remote_public_root="/home/user/public_html",
        domain="demo.stayhomelab.net",
        app_url="https://demo.stayhomelab.net",
        app_name="Demo",
        db_host="localhost",
        database_mode="api",
        db_name_suffix="laravel",
        db_user_suffix="lar_user",
        db_password="password123",
        php_cli_version="8.3",
    )
    with pytest.raises(LaravelSetupError, match="PHP 8.4 以上を要求しています"):
        validate_laravel_setup_request(req)

    # Compatible php_cli_version succeeds
    req_84 = replace(req, php_cli_version="8.4")
    validate_laravel_setup_request(req_84)


def test_laravel_setup_engine(tmp_path: Path) -> None:
    laravel_root = tmp_path / "laravel"
    make_laravel(laravel_root)
    (laravel_root / ".env.example").write_text("APP_NAME=Test\n", encoding="utf-8")

    req = LaravelSetupRequest(
        local_root=laravel_root,
        remote_app_root="/home/user/app",
        remote_public_root="/home/user/public_html",
        domain="demo.stayhomelab.net",
        app_url="https://demo.stayhomelab.net",
        app_name="Demo",
        db_host="localhost",
        database_mode="existing",
        db_name_suffix="",
        db_user_suffix="",
        db_password="password123",
        existing_db_name="user_laravel",
        existing_db_user="user_lar_user",
    )

    ssh_client = MagicMock()
    stdout_mock = MagicMock()
    stdout_mock.channel.recv_exit_status.return_value = 0
    stdout_mock.read.return_value = b"8.3.1\n"
    stderr_mock = MagicMock()
    stderr_mock.read.return_value = b""
    ssh_client.exec_command.return_value = (MagicMock(), stdout_mock, stderr_mock)

    sftp_mock = MagicMock()
    # Mock sftp.open for reading and writing files
    file_buffers: dict[str, io.BytesIO] = {
        "/home/user/public_html/index.php": io.BytesIO(b"<?php\nrequire __DIR__.'/../vendor/autoload.php';\n"),
    }

    def fake_open(path: str, mode: str = "r"):
        if "w" in mode:
            buf = io.BytesIO()
            file_buffers[path] = buf
            return buf
        return file_buffers.get(path, io.BytesIO(b""))

    sftp_mock.open.side_effect = fake_open

    progress_events: list[LaravelSetupProgress] = []

    engine = LaravelSetupEngine()
    result = engine.setup(
        request=req,
        api=None,
        ssh_client=ssh_client,
        sftp=sftp_mock,
        ssh_username="user",
        progress_callback=progress_events.append,
    )

    assert result.db_name == "user_laravel"
    assert result.db_user == "user_lar_user"
    assert result.transferred_app_files > 0
    assert result.transferred_public_files > 0
    assert result.public_url == "https://demo.stayhomelab.net"
    assert len(progress_events) > 0


def test_upload_file_overwrites_with_posix_rename(tmp_path: Path) -> None:
    from dxsync.laravel_setup import _upload_file

    local = tmp_path / "test.txt"
    local.write_text("hello", encoding="utf-8")

    sftp = MagicMock()
    sftp.posix_rename = MagicMock()

    size = _upload_file(sftp, local, "/remote/path/test.txt")
    assert size == 5
    assert sftp.put.called
    assert sftp.posix_rename.called


def test_upload_file_fallback_rename_when_posix_rename_fails(tmp_path: Path) -> None:
    from dxsync.laravel_setup import _upload_file

    local = tmp_path / "test.txt"
    local.write_text("hello", encoding="utf-8")

    sftp = MagicMock()
    sftp.posix_rename = MagicMock(side_effect=OSError("posix_rename unsupported"))

    size = _upload_file(sftp, local, "/remote/path/test.txt")
    assert size == 5
    assert sftp.remove.called
    assert sftp.rename.called


def test_upload_file_raises_descriptive_error_on_sftp_failure(tmp_path: Path) -> None:
    import pytest
    from dxsync.laravel_setup import _upload_file, LaravelSetupError

    local = tmp_path / "test.txt"
    local.write_text("hello", encoding="utf-8")

    sftp = MagicMock()
    del sftp.posix_rename
    sftp.rename.side_effect = IOError("Failure")

    with pytest.raises(LaravelSetupError) as exc_info:
        _upload_file(sftp, local, "/remote/path/test.txt")
    assert "ファイル転送に失敗しました" in str(exc_info.value)
    assert "Failure" in str(exc_info.value)


def test_scan_remote_files() -> None:
    from dxsync.laravel_setup import _scan_remote_files

    ssh = MagicMock()
    stdout = MagicMock()
    stdout.channel.recv_exit_status.return_value = 0
    stdout.read.return_value = b"app/Http/Kernel.php\t1024\npublic/index.php\t512\n"
    stderr = MagicMock()
    ssh.exec_command.return_value = (MagicMock(), stdout, stderr)

    res = _scan_remote_files(ssh, "php", "/home/user/app")
    assert res == {
        "app/Http/Kernel.php": 1024,
        "public/index.php": 512,
    }
