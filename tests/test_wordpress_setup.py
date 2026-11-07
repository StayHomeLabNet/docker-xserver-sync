from __future__ import annotations

from pathlib import Path
import io
import stat
from dataclasses import replace

import pytest

from dxsync.wordpress_setup import (
    WordPressSetupError,
    WordPressSetupRequest,
    WordPressSetupEngine,
    generate_wp_config,
    validate_setup_request,
    _mkdir_private,
    _mysql_option_value,
)
from dxsync.force_overwrite import ForceOverwriteResult
from dxsync.xserver_api import CreatedDatabase, CreatedDatabaseUser


def make_wordpress(root: Path) -> None:
    for directory in ("wp-admin", "wp-includes", "wp-content"):
        (root / directory).mkdir(parents=True)
    (root / "wp-load.php").write_text("<?php", encoding="utf-8")


def request(root: Path, sql: Path) -> WordPressSetupRequest:
    return WordPressSetupRequest(
        local_root=root,
        sql_dump=sql,
        remote_root="/home/sample/example.com/public_html",
        domain="example.com",
        old_url="http://localhost",
        new_url="https://example.com",
        db_host="mysql.example.xserver.jp",
        db_name_suffix="wp",
        db_user_suffix="wp_user",
        db_password="Password123!",
    )


def test_validate_setup_request_accepts_wordpress_and_safe_sql(tmp_path: Path) -> None:
    make_wordpress(tmp_path)
    sql = tmp_path / "site.sql"
    sql.write_text("CREATE TABLE wp_options (id int);", encoding="utf-8")
    validate_setup_request(request(tmp_path, sql))


@pytest.mark.parametrize(
    "statement",
    ("CREATE DATABASE other;", "DROP DATABASE other;", "USE other;"),
)
def test_validate_setup_request_rejects_database_level_sql(
    tmp_path: Path, statement: str
) -> None:
    make_wordpress(tmp_path)
    sql = tmp_path / "site.sql"
    sql.write_text(statement, encoding="utf-8")
    with pytest.raises(WordPressSetupError, match="forbidden database-level"):
        validate_setup_request(request(tmp_path, sql))


def test_generate_wp_config_escapes_secrets_and_uses_production_values() -> None:
    value = generate_wp_config(
        db_name="sample_wp",
        db_user="sample_user",
        db_password="pa'ss\\word",
        db_host="mysql.example.xserver.jp",
        table_prefix="site_",
    ).decode()

    assert "define('DB_NAME', 'sample_wp');" in value
    assert "define('DB_PASSWORD', 'pa\\'ss\\\\word');" in value
    assert "$table_prefix = 'site_';" in value
    assert "define('WP_DEBUG', false);" in value
    assert value.count("_SALT'") == 4
    assert value.count("_KEY'") == 4
    assert "Password123!" not in value


def test_private_remote_directory_does_not_chmod_home_directories() -> None:
    class Item:
        st_mode = stat.S_IFDIR | 0o755

    class Sftp:
        directories = {"/home/sample"}
        chmods = []

        def stat(self, path):
            if path not in self.directories:
                raise FileNotFoundError(path)
            return Item()

        def mkdir(self, path, mode=0o777):
            self.directories.add(path)

        def chmod(self, path, mode):
            self.chmods.append((path, mode))

    sftp = Sftp()
    _mkdir_private(
        sftp,
        "/home/sample/.dxsync/setup/token",
        base="/home/sample",
    )

    assert ("/home/sample", 0o700) not in sftp.chmods
    assert sftp.chmods == [
        ("/home/sample/.dxsync", 0o700),
        ("/home/sample/.dxsync/setup", 0o700),
        ("/home/sample/.dxsync/setup/token", 0o700),
    ]


def test_mysql_option_value_quotes_special_characters() -> None:
    assert _mysql_option_value('pa#ss"word\\x') == '"pa#ss\\"word\\\\x"'


def test_setup_engine_creates_empty_database_imports_and_transfers(
    tmp_path: Path, monkeypatch
) -> None:
    make_wordpress(tmp_path)
    sql = tmp_path / "site.sql"
    sql.write_text("CREATE TABLE wp_options (id int);", encoding="utf-8")

    class Api:
        servername = "sample.xsrv.jp"
        calls = []

        def key_info(self):
            self.calls.append("key")
            return {}

        def list_databases(self):
            return ()

        def list_database_users(self):
            return ()

        def create_database(self, suffix, *, memo=""):
            self.calls.append(("db", suffix))
            return CreatedDatabase("sample_wp")

        def create_database_user(self, suffix, password, *, memo=""):
            self.calls.append(("user", suffix, password))
            return CreatedDatabaseUser("sample_wp_user")

        def grant_database(self, user, database):
            self.calls.append(("grant", user, database))

    class RemoteFile(io.BytesIO):
        def __init__(self, sftp, path):
            super().__init__()
            self.sftp = sftp
            self.path = path

        def close(self):
            if not self.closed:
                self.sftp.files[self.path] = self.getvalue()
            super().close()

    class Sftp:
        def __init__(self):
            self.directories = {
                "/home/sample",
                "/home/sample/example.com/public_html",
            }
            self.files = {}

        def listdir_attr(self, path):
            assert path == "/home/sample/example.com/public_html"
            return []

        def stat(self, path):
            if path not in self.directories:
                raise FileNotFoundError(path)
            return type("Item", (), {"st_mode": stat.S_IFDIR | 0o755})()

        def mkdir(self, path, mode=0o777):
            self.directories.add(path)

        def chmod(self, path, mode):
            pass

        def open(self, path, mode="r"):
            assert "w" in mode
            return RemoteFile(self, path)

        def posix_rename(self, old, new):
            self.files[new] = self.files.pop(old)

        def remove(self, path):
            self.files.pop(path, None)

        def rmdir(self, path):
            self.directories.discard(path)

    class Channel:
        def recv_exit_status(self):
            return 0

    class Output(io.BytesIO):
        channel = Channel()

    class Client:
        commands = []

        def exec_command(self, command):
            self.commands.append(command)
            if "command -v mysql" in command:
                output = b"/usr/bin/mysql\n"
            elif "command -v wp" in command:
                output = b"/usr/local/bin/wp\n"
            elif "information_schema.tables" in command:
                output = b"12\n"
            else:
                output = b""
            return io.BytesIO(), Output(output), io.BytesIO()

    transfer_arguments = {}

    def fake_overwrite(*_args, **kwargs):
        transfer_arguments.update(kwargs)
        return ForceOverwriteResult(
            "/home/sample/example.com/public_html", ("index.php",), 100
        )

    monkeypatch.setattr(
        "dxsync.wordpress_setup.ForceOverwriteEngine.overwrite",
        fake_overwrite,
    )
    api = Api()
    sftp = Sftp()
    client = Client()

    setup_request = replace(
        request(tmp_path, sql),
        include_paths=("index.php", "wp-content/plugins/example/plugin.php"),
    )
    result = WordPressSetupEngine().setup(
        request=setup_request,
        api=api,
        ssh_client=client,
        sftp=sftp,
        ssh_username="sample",
    )

    assert result.db_name == "sample_wp"
    assert result.table_count == 12
    assert result.url_replaced
    assert ("grant", "sample_wp_user", "sample_wp") in api.calls
    assert "/home/sample/example.com/public_html/wp-config.php" in sftp.files
    assert any("search-replace" in command for command in client.commands)
    assert not any(path.endswith("database.sql") for path in sftp.files)
    assert transfer_arguments["include_paths"] == setup_request.include_paths
