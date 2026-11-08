from __future__ import annotations

from pathlib import Path
import io
import stat
from dataclasses import replace

import pytest

from dxsync.wordpress_setup import (
    WordPressSetupError,
    WordPressSetupProgress,
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


def test_resume_validation_does_not_require_sql_dump(tmp_path: Path) -> None:
    make_wordpress(tmp_path)
    value = replace(
        request(tmp_path, tmp_path / "missing.sql"),
        resume_existing=True,
        database_mode="existing",
        existing_db_name="sample_wp",
        existing_db_user="sample_wp_user",
    )

    validate_setup_request(value)


def test_resume_validation_rejects_api_database_mode(tmp_path: Path) -> None:
    make_wordpress(tmp_path)
    value = replace(
        request(tmp_path, tmp_path / "missing.sql"),
        resume_existing=True,
    )

    with pytest.raises(WordPressSetupError, match="作成済みDB"):
        validate_setup_request(value)


def test_resume_validation_accepts_remote_wp_config_without_db_credentials(
    tmp_path: Path,
) -> None:
    make_wordpress(tmp_path)
    value = replace(
        request(tmp_path, tmp_path / "missing.sql"),
        resume_existing=True,
        use_remote_wp_config=True,
        database_mode="existing",
        existing_db_name="",
        existing_db_user="",
        db_password="",
        db_host="",
    )

    validate_setup_request(value)


def test_validate_setup_request_rejects_unsupported_php_cli_version(
    tmp_path: Path,
) -> None:
    make_wordpress(tmp_path)
    sql = tmp_path / "site.sql"
    sql.write_text("CREATE TABLE wp_options (id int);", encoding="utf-8")

    with pytest.raises(WordPressSetupError, match="WP-CLI用PHP"):
        validate_setup_request(
            replace(request(tmp_path, sql), php_cli_version="5.4")
        )


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
        table_counts = iter((0, 12))

        def exec_command(self, command):
            self.commands.append(command)
            if "command -v mysql" in command:
                output = b"/usr/bin/mysql\n"
            elif "test -x /usr/bin/php8.3" in command:
                output = b"8.3.29"
            elif "command -v wp" in command:
                output = b"/usr/local/bin/wp\n"
            elif "information_schema.tables" in command:
                output = f"{next(self.table_counts)}\n".encode()
            else:
                output = b""
            return io.BytesIO(), Output(output), io.BytesIO()

    transfer_arguments = {}
    progress: list[WordPressSetupProgress] = []

    def fake_overwrite(*_args, **kwargs):
        transfer_arguments.update(kwargs)
        kwargs["file_started_callback"](0, 2, "index.php")
        kwargs["progress_callback"](1, 2, "index.php")
        kwargs["file_started_callback"](
            1, 2, "wp-content/plugins/example/plugin.php"
        )
        kwargs["progress_callback"](
            2, 2, "wp-content/plugins/example/plugin.php"
        )
        return ForceOverwriteResult(
            "/home/sample/example.com/public_html",
            ("index.php", "wp-content/plugins/example/plugin.php"),
            100,
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
        progress_callback=progress.append,
    )

    assert result.db_name == "sample_wp"
    assert result.table_count == 12
    assert result.url_replaced
    assert ("grant", "sample_wp_user", "sample_wp") in api.calls
    assert "/home/sample/example.com/public_html/wp-config.php" in sftp.files
    assert any(
        command.startswith("/usr/bin/php8.3 /usr/local/bin/wp search-replace")
        for command in client.commands
    )
    assert any(
        command == "/usr/bin/php8.3 /usr/local/bin/wp --info"
        for command in client.commands
    )
    assert not any(path.endswith("database.sql") for path in sftp.files)
    assert transfer_arguments["include_paths"] == setup_request.include_paths
    assert all(item.total_steps == 10 for item in progress)
    assert any(
        item.step == 6
        and item.unit == "bytes"
        and item.current == item.total == sql.stat().st_size
        for item in progress
    )
    assert any(
        item.step == 8
        and item.unit == "files"
        and item.current == 1
        and item.total == 2
        and item.detail == "index.php"
        for item in progress
    )
    assert any(item.step == 8 and item.current == item.total == 2 for item in progress)


def test_setup_engine_uses_existing_empty_database_without_api(
    tmp_path: Path, monkeypatch
) -> None:
    make_wordpress(tmp_path)
    sql = tmp_path / "site.sql"
    sql.write_text("CREATE TABLE wp_options (id int);", encoding="utf-8")
    commands: list[str] = []
    table_counts = iter((0, 3))

    def fake_run_remote(_client, command: str) -> str:
        commands.append(command)
        if "command -v mysql" in command:
            return "/usr/bin/mysql"
        if "information_schema.tables" in command:
            return str(next(table_counts))
        return ""

    class Sftp:
        def listdir_attr(self, _path):
            return []

        def open(self, _path, _mode="r"):
            return io.BytesIO()

        def chmod(self, _path, _mode):
            pass

        def remove(self, _path):
            pass

        def rmdir(self, _path):
            pass

        def posix_rename(self, _old, _new):
            pass

    monkeypatch.setattr("dxsync.wordpress_setup._run_remote", fake_run_remote)
    monkeypatch.setattr(
        "dxsync.wordpress_setup._mkdir_private", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "dxsync.wordpress_setup._upload_bytes", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "dxsync.wordpress_setup.ForceOverwriteEngine.overwrite",
        lambda *_args, **_kwargs: ForceOverwriteResult(
            "/home/sample/example.com/public_html", ("index.php",), 100
        ),
    )
    setup_request = replace(
        request(tmp_path, sql),
        old_url="https://example.com",
        database_mode="existing",
        existing_db_name="sample_existing",
        existing_db_user="sample_user",
    )

    result = WordPressSetupEngine().setup(
        request=setup_request,
        api=None,
        ssh_client=object(),
        sftp=Sftp(),
        ssh_username="sample",
    )

    assert result.db_name == "sample_existing"
    assert result.db_user == "sample_user"
    assert result.table_count == 3
    assert any("sample_existing" in command for command in commands)


def test_resume_uses_remote_wp_config_without_exporting_password(
    tmp_path: Path, monkeypatch
) -> None:
    make_wordpress(tmp_path)
    commands: list[str] = []

    def fake_run_remote(_client, command: str) -> str:
        commands.append(command)
        if "test -x /usr/bin/php8.3" in command:
            return "8.3.29"
        if "command -v wp" in command:
            return "/usr/local/bin/wp"
        if "config get DB_NAME" in command:
            return "sample_existing"
        if "config get DB_USER" in command:
            return "sample_user"
        if "db query" in command and "information_schema.tables" in command:
            return "12"
        return ""

    class Item:
        filename = "wp-config.php"

    class Sftp:
        def listdir_attr(self, _path):
            return [Item()]

        def stat(self, _path):
            return object()

        def remove(self, _path):
            pass

        def rmdir(self, _path):
            pass

    monkeypatch.setattr("dxsync.wordpress_setup._run_remote", fake_run_remote)
    monkeypatch.setattr(
        "dxsync.wordpress_setup._mkdir_private", lambda *_args, **_kwargs: None
    )
    setup_request = replace(
        request(tmp_path, tmp_path / "missing.sql"),
        resume_existing=True,
        use_remote_wp_config=True,
        database_mode="existing",
        existing_db_name="",
        existing_db_user="",
        db_password="",
        db_host="",
    )

    result = WordPressSetupEngine().setup(
        request=setup_request,
        api=None,
        ssh_client=object(),
        sftp=Sftp(),
        ssh_username="sample",
    )

    assert result.db_name == "sample_existing"
    assert result.db_user == "sample_user"
    assert result.table_count == 12
    assert not any("command -v mysql" in command for command in commands)
    assert any("config get DB_NAME" in command for command in commands)
    assert any("db query" in command for command in commands)


def test_resume_partial_transfer_preserves_remote_wp_config(
    tmp_path: Path, monkeypatch
) -> None:
    make_wordpress(tmp_path)
    commands: list[str] = []
    messages: list[str] = []

    def fake_run_remote(_client, command: str) -> str:
        commands.append(command)
        if "test -x /usr/bin/php8.3" in command:
            return "8.3.29"
        if "command -v wp" in command:
            return "/usr/local/bin/wp"
        if "config get DB_NAME" in command:
            return "sample_existing"
        if "config get DB_USER" in command:
            return "sample_user"
        if "db query" in command:
            return "12"
        return ""

    class Item:
        filename = "wp-config.php"

    class Sftp:
        def listdir_attr(self, _path):
            return [Item()]

        def stat(self, path):
            if path.endswith("/wp-load.php"):
                raise FileNotFoundError(path)
            return object()

        def remove(self, _path):
            pass

        def rmdir(self, _path):
            pass

    monkeypatch.setattr("dxsync.wordpress_setup._run_remote", fake_run_remote)
    monkeypatch.setattr(
        "dxsync.wordpress_setup._mkdir_private", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "dxsync.wordpress_setup._upload_bytes",
        lambda *_args, **_kwargs: pytest.fail("wp-config.php must not be replaced"),
    )
    monkeypatch.setattr(
        "dxsync.wordpress_setup.ForceOverwriteEngine.overwrite",
        lambda *_args, **_kwargs: ForceOverwriteResult(
            remote_root="/home/sample/example.com/public_html",
            files=("wp-load.php",),
            total_bytes=5,
        ),
    )
    setup_request = replace(
        request(tmp_path, tmp_path / "missing.sql"),
        resume_existing=True,
        use_remote_wp_config=True,
        database_mode="existing",
        existing_db_name="",
        existing_db_user="",
        db_password="",
        db_host="",
    )

    result = WordPressSetupEngine().setup(
        request=setup_request,
        api=None,
        ssh_client=object(),
        sftp=Sftp(),
        ssh_username="sample",
        progress_callback=lambda value: messages.append(value.message),
    )

    assert result.transferred_files == 1
    assert result.transferred_bytes == 5
    assert "配置済みwp-config.phpを保持中" in messages


def test_setup_engine_rejects_existing_database_with_tables(
    tmp_path: Path, monkeypatch
) -> None:
    make_wordpress(tmp_path)
    sql = tmp_path / "site.sql"
    sql.write_text("CREATE TABLE wp_options (id int);", encoding="utf-8")
    commands: list[str] = []

    def fake_run_remote(_client, command: str) -> str:
        commands.append(command)
        if "command -v mysql" in command:
            return "/usr/bin/mysql"
        if "information_schema.tables" in command:
            return "2"
        return ""

    class Sftp:
        def listdir_attr(self, _path):
            return []

        def open(self, _path, _mode="r"):
            return io.BytesIO()

        def chmod(self, _path, _mode):
            pass

        def remove(self, _path):
            pass

        def rmdir(self, _path):
            pass

    monkeypatch.setattr("dxsync.wordpress_setup._run_remote", fake_run_remote)
    monkeypatch.setattr(
        "dxsync.wordpress_setup._mkdir_private", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "dxsync.wordpress_setup._upload_bytes", lambda *_args, **_kwargs: None
    )
    setup_request = replace(
        request(tmp_path, sql),
        old_url="https://example.com",
        database_mode="existing",
        existing_db_name="sample_existing",
        existing_db_user="sample_user",
    )

    with pytest.raises(WordPressSetupError, match="空ではありません") as error:
        WordPressSetupEngine().setup(
            request=setup_request,
            api=None,
            ssh_client=object(),
            sftp=Sftp(),
            ssh_username="sample",
        )

    assert not any(" < " in command for command in commands)
    assert "WordPressファイル配置は開始していません" in str(error.value)
