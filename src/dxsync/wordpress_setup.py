from __future__ import annotations

import errno
import re
import secrets
import shlex
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

from .force_overwrite import ForceOverwriteEngine
from .presets import COMMON_EXCLUDES
from .validation import normalize_remote_root
from .xserver_api import XServerApiClient


class WordPressSetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class WordPressSetupRequest:
    local_root: Path
    sql_dump: Path
    remote_root: str
    domain: str
    old_url: str
    new_url: str
    db_host: str
    db_name_suffix: str
    db_user_suffix: str
    db_password: str
    table_prefix: str = "wp_"
    include_paths: tuple[str, ...] | None = None


@dataclass(frozen=True)
class WordPressSetupResult:
    db_name: str
    db_user: str
    transferred_files: int
    transferred_bytes: int
    table_count: int
    url_replaced: bool


_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_TABLE_PREFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")


def validate_setup_request(request: WordPressSetupRequest) -> None:
    root = request.local_root.expanduser().resolve()
    if not root.is_dir():
        raise WordPressSetupError(f"WordPress root is not a directory: {root}")
    for required in ("wp-admin", "wp-includes", "wp-content", "wp-load.php"):
        if not (root / required).exists():
            raise WordPressSetupError(f"WordPress file or directory is missing: {required}")
    dump = request.sql_dump.expanduser().resolve()
    if not dump.is_file() or dump.stat().st_size == 0:
        raise WordPressSetupError("A non-empty SQL dump file is required")
    dangerous_sql = re.compile(rb"^\s*(?:CREATE|DROP)\s+DATABASE\b|^\s*USE\s+", re.I)
    with dump.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            if dangerous_sql.search(line):
                raise WordPressSetupError(
                    f"SQL dump contains a forbidden database-level statement at line {line_number}"
                )
    normalize_remote_root(request.remote_root)
    if not request.domain.strip() or "/" in request.domain:
        raise WordPressSetupError("Invalid target domain")
    for label, value in (
        ("database suffix", request.db_name_suffix),
        ("database user suffix", request.db_user_suffix),
    ):
        if not _SUFFIX_RE.fullmatch(value):
            raise WordPressSetupError(f"Invalid {label}; use letters, numbers, and underscores")
    if len(request.db_password) < 8:
        raise WordPressSetupError("Database password must contain at least 8 characters")
    if "\n" in request.db_password or "\r" in request.db_password:
        raise WordPressSetupError("Database password contains a forbidden newline")
    if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", request.db_host):
        raise WordPressSetupError("Invalid database host")
    if not _TABLE_PREFIX_RE.fullmatch(request.table_prefix):
        raise WordPressSetupError("Invalid WordPress table prefix")
    if request.include_paths is not None and not request.include_paths:
        raise WordPressSetupError("At least one WordPress file must be selected")
    for label, value in (("old URL", request.old_url), ("new URL", request.new_url)):
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WordPressSetupError(f"Invalid {label}")


def generate_wp_config(
    *,
    db_name: str,
    db_user: str,
    db_password: str,
    db_host: str,
    table_prefix: str,
) -> bytes:
    def php_string(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    salts = [secrets.token_urlsafe(48) for _ in range(8)]
    keys = (
        "AUTH_KEY",
        "SECURE_AUTH_KEY",
        "LOGGED_IN_KEY",
        "NONCE_KEY",
        "AUTH_SALT",
        "SECURE_AUTH_SALT",
        "LOGGED_IN_SALT",
        "NONCE_SALT",
    )
    lines = [
        "<?php",
        "define('DB_NAME', '" + php_string(db_name) + "');",
        "define('DB_USER', '" + php_string(db_user) + "');",
        "define('DB_PASSWORD', '" + php_string(db_password) + "');",
        "define('DB_HOST', '" + php_string(db_host) + "');",
        "define('DB_CHARSET', 'utf8mb4');",
        "define('DB_COLLATE', '');",
    ]
    lines.extend(
        "define('" + key + "', '" + php_string(value) + "');"
        for key, value in zip(keys, salts, strict=True)
    )
    lines.extend(
        (
            "$table_prefix = '" + php_string(table_prefix) + "';",
            "define('WP_DEBUG', false);",
            "if (!defined('ABSPATH')) define('ABSPATH', __DIR__ . '/');",
            "require_once ABSPATH . 'wp-settings.php';",
            "",
        )
    )
    return "\n".join(lines).encode("utf-8")


def _run_remote(client: Any, command: str) -> str:
    _stdin, stdout, stderr = client.exec_command(command)
    status = stdout.channel.recv_exit_status()
    output = stdout.read().decode("utf-8", errors="replace").strip()
    error = stderr.read().decode("utf-8", errors="replace").strip()
    if status != 0:
        detail = error or output or f"exit status {status}"
        raise WordPressSetupError(f"Remote command failed: {detail}")
    return output


def _mkdir_private(sftp: Any, path: str, *, base: str) -> None:
    base_path = PurePosixPath(base)
    path_value = PurePosixPath(path)
    try:
        parts = path_value.relative_to(base_path).parts
    except ValueError as exc:
        raise WordPressSetupError("Private work path escaped the SSH account home") from exc
    current = base_path
    for part in parts:
        current /= part
        text = str(current)
        try:
            mode = sftp.stat(text).st_mode
            if not stat.S_ISDIR(mode):
                raise WordPressSetupError(f"Remote path is not a directory: {text}")
        except OSError as exc:
            if getattr(exc, "errno", None) not in {None, errno.ENOENT}:
                raise
            sftp.mkdir(text, mode=0o700)
        sftp.chmod(text, 0o700)


def _upload_bytes(sftp: Any, path: str, value: bytes, mode: int = 0o600) -> None:
    with sftp.open(path, "wb") as stream:
        stream.write(value)
    sftp.chmod(path, mode)


def _mysql_option_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class WordPressSetupEngine:
    def setup(
        self,
        *,
        request: WordPressSetupRequest,
        api: XServerApiClient,
        ssh_client: Any,
        sftp: Any,
        ssh_username: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> WordPressSetupResult:
        validate_setup_request(request)
        progress = progress_callback or (lambda _message: None)
        server_id = api.servername.split(".", 1)[0]
        expected_db = f"{server_id}_{request.db_name_suffix}"
        expected_user = f"{server_id}_{request.db_user_suffix}"

        progress("XServer APIと作成先を確認中")
        api.key_info()
        if expected_db in api.list_databases():
            raise WordPressSetupError(f"Database already exists: {expected_db}")
        if expected_user in api.list_database_users():
            raise WordPressSetupError(f"Database user already exists: {expected_user}")

        remote_root = normalize_remote_root(request.remote_root)
        try:
            entries = tuple(item.filename for item in sftp.listdir_attr(remote_root))
        except OSError as exc:
            raise WordPressSetupError(f"Cannot read the remote root: {exc}") from exc
        unexpected = sorted(set(entries) - {"index.html"})
        if unexpected:
            raise WordPressSetupError(
                "Remote root is not empty; setup stopped: " + ", ".join(unexpected[:10])
            )

        progress("XServerのMySQLコマンドを確認中")
        mysql_bin = _run_remote(
            ssh_client, "command -v mysql || command -v mariadb"
        ).splitlines()[0]
        wp_bin = ""
        if request.old_url.rstrip("/") != request.new_url.rstrip("/"):
            progress("XServerのWP-CLIを確認中")
            wp_bin = _run_remote(ssh_client, "command -v wp").splitlines()[0]

        progress("MySQLデータベースを作成中")
        database = api.create_database(request.db_name_suffix, memo="Docker XServer Sync")
        try:
            progress("MySQLユーザーを作成中")
            user = api.create_database_user(
                request.db_user_suffix,
                request.db_password,
                memo="Docker XServer Sync",
            )
        except Exception as exc:
            raise WordPressSetupError(
                f"{exc}\n\nCreated database was not deleted automatically: {database.db_name}"
            ) from exc
        try:
            api.grant_database(user.db_user, database.db_name)
        except Exception as exc:
            raise WordPressSetupError(
                f"{exc}\n\nCreated resources were not deleted automatically: "
                f"database {database.db_name}, user {user.db_user}"
            ) from exc

        token = uuid.uuid4().hex
        work_dir = f"/home/{ssh_username}/.dxsync/setup/{token}"
        sql_remote = f"{work_dir}/database.sql"
        cnf_remote = f"{work_dir}/mysql.cnf"
        try:
            _mkdir_private(sftp, work_dir, base=f"/home/{ssh_username}")
            progress("SQLダンプを暗号化通信で転送中")
            with request.sql_dump.expanduser().resolve().open("rb") as reader, sftp.open(
                sql_remote, "wb"
            ) as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
            sftp.chmod(sql_remote, 0o600)
            cnf = (
                "[client]\n"
                f"host={_mysql_option_value(request.db_host)}\n"
                f"user={_mysql_option_value(user.db_user)}\n"
                f"password={_mysql_option_value(request.db_password)}\n"
                "default-character-set=utf8mb4\n"
            ).encode("utf-8")
            _upload_bytes(sftp, cnf_remote, cnf)

            import_command = (
                f"{shlex.quote(mysql_bin)} --defaults-extra-file={shlex.quote(cnf_remote)} "
                f"{shlex.quote(database.db_name)} < {shlex.quote(sql_remote)}"
            )
            progress("SQLを新規データベースへインポート中")
            _run_remote(ssh_client, import_command)

            count_command = (
                f"{shlex.quote(mysql_bin)} --defaults-extra-file={shlex.quote(cnf_remote)} "
                f"-Nse 'SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = "
                f"DATABASE()' {shlex.quote(database.db_name)}"
            )
            count_output = _run_remote(ssh_client, count_command)
            table_count = int(count_output.strip() or "0")
            if table_count < 1:
                raise WordPressSetupError("SQL import completed but no tables were found")

            progress("WordPressファイルを転送中")
            transfer = ForceOverwriteEngine().overwrite(
                sftp=sftp,
                local_root=request.local_root,
                remote_root=remote_root,
                exclude_patterns=COMMON_EXCLUDES
                + ("wp-config.php", "*.sql", "*.sql.gz"),
                protect_patterns=(),
                include_paths=request.include_paths,
            )
            config = generate_wp_config(
                db_name=database.db_name,
                db_user=user.db_user,
                db_password=request.db_password,
                db_host=request.db_host,
                table_prefix=request.table_prefix,
            )
            config_temp = f"{remote_root}/wp-config.php.dxsync-{token}.tmp"
            _upload_bytes(sftp, config_temp, config, 0o600)
            destination = f"{remote_root}/wp-config.php"
            rename = getattr(sftp, "posix_rename", None)
            if callable(rename):
                rename(config_temp, destination)
            else:
                sftp.rename(config_temp, destination)

            url_replaced = request.old_url.rstrip("/") != request.new_url.rstrip("/")
            if url_replaced:
                progress("WordPressのURLをシリアライズ対応で置換中")
                search_command = " ".join(
                    (
                        shlex.quote(wp_bin),
                        "search-replace",
                        shlex.quote(request.old_url.rstrip("/")),
                        shlex.quote(request.new_url.rstrip("/")),
                        "--all-tables-with-prefix",
                        "--precise",
                        "--skip-columns=guid",
                        "--path=" + shlex.quote(remote_root),
                    )
                )
                _run_remote(ssh_client, search_command)

            if "index.html" in entries:
                sftp.remove(f"{remote_root}/index.html")
            progress("初回セットアップが完了しました")
            return WordPressSetupResult(
                db_name=database.db_name,
                db_user=user.db_user,
                transferred_files=len(transfer.files) + 1,
                transferred_bytes=transfer.total_bytes + len(config),
                table_count=table_count,
                url_replaced=url_replaced,
            )
        except Exception as exc:
            if isinstance(exc, WordPressSetupError):
                detail = str(exc)
            else:
                detail = str(exc)
            raise WordPressSetupError(
                f"{detail}\n\nCreated resources were not deleted automatically: "
                f"database {database.db_name}, user {user.db_user}. "
                "Files may already exist in the remote root; inspect them before retrying."
            ) from exc
        finally:
            for path in (cnf_remote, sql_remote):
                try:
                    sftp.remove(path)
                except OSError:
                    pass
            try:
                sftp.rmdir(work_dir)
            except OSError:
                pass
