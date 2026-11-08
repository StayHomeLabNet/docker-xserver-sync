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
from .cancellation import CancellationToken, OperationCancelled
from .presets import COMMON_EXCLUDES
from .validation import normalize_remote_root
from .xserver_api import XServerApiClient


class WordPressSetupError(RuntimeError):
    pass


WORDPRESS_SETUP_TOTAL_STEPS = 10


@dataclass(frozen=True)
class WordPressSetupProgress:
    step: int
    total_steps: int
    message: str
    current: int | None = None
    total: int | None = None
    unit: str = ""
    detail: str = ""


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
    database_mode: str = "api"
    existing_db_name: str = ""
    existing_db_user: str = ""
    php_cli_version: str = "8.3"
    resume_existing: bool = False
    use_remote_wp_config: bool = False
    import_sql_if_database_empty: bool = False


@dataclass(frozen=True)
class WordPressSetupResult:
    db_name: str
    db_user: str
    transferred_files: int
    transferred_bytes: int
    table_count: int
    url_replaced: bool
    public_url: str
    detected_home_url: str
    detected_site_url: str
    replaced_urls: tuple[str, ...]


_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_FULL_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_TABLE_PREFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_sql_dump(path: Path) -> None:
    dump = path.expanduser().resolve()
    if not dump.is_file() or dump.stat().st_size == 0:
        raise WordPressSetupError("A non-empty SQL dump file is required")
    dangerous_sql = re.compile(rb"^\s*(?:CREATE|DROP)\s+DATABASE\b|^\s*USE\s+", re.I)
    with dump.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            if dangerous_sql.search(line):
                raise WordPressSetupError(
                    "SQL dump contains a forbidden database-level statement "
                    f"at line {line_number}"
                )


def validate_setup_request(request: WordPressSetupRequest) -> None:
    root = request.local_root.expanduser().resolve()
    if not root.is_dir():
        raise WordPressSetupError(f"WordPress root is not a directory: {root}")
    for required in ("wp-admin", "wp-includes", "wp-content", "wp-load.php"):
        if not (root / required).exists():
            raise WordPressSetupError(f"WordPress file or directory is missing: {required}")
    if not request.resume_existing or request.import_sql_if_database_empty:
        _validate_sql_dump(request.sql_dump)
    normalize_remote_root(request.remote_root)
    if not request.domain.strip() or "/" in request.domain:
        raise WordPressSetupError("Invalid target domain")
    if request.database_mode not in {"api", "existing"}:
        raise WordPressSetupError("DBの準備方法が正しくありません")
    if request.resume_existing and request.database_mode != "existing":
        raise WordPressSetupError("再開時は作成済みDBの完全な名前を指定してください")
    if request.use_remote_wp_config and not request.resume_existing:
        raise WordPressSetupError("wp-config.phpのDB接続情報は再開時だけ使用できます")
    if request.import_sql_if_database_empty and not request.resume_existing:
        raise WordPressSetupError("DBが空の場合のSQL復旧は再開時だけ使用できます")
    if request.database_mode == "api":
        for label, value in (
            ("database suffix", request.db_name_suffix),
            ("database user suffix", request.db_user_suffix),
        ):
            if not _SUFFIX_RE.fullmatch(value):
                raise WordPressSetupError(
                    f"Invalid {label}; use letters, numbers, and underscores"
                )
    elif not request.use_remote_wp_config:
        for label, value in (
            ("作成済みDB名", request.existing_db_name),
            ("作成済みDBユーザー", request.existing_db_user),
        ):
            if not _FULL_DB_NAME_RE.fullmatch(value):
                raise WordPressSetupError(
                    f"{label}が正しくありません。"
                    "サーバーパネルに表示された完全な名前を入力してください"
                )
    if not request.use_remote_wp_config and len(request.db_password) < 8:
        raise WordPressSetupError("Database password must contain at least 8 characters")
    if "\n" in request.db_password or "\r" in request.db_password:
        raise WordPressSetupError("Database password contains a forbidden newline")
    if (
        not request.use_remote_wp_config
        and not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", request.db_host)
    ):
        raise WordPressSetupError("Invalid database host")
    if not _TABLE_PREFIX_RE.fullmatch(request.table_prefix):
        raise WordPressSetupError("Invalid WordPress table prefix")
    if not re.fullmatch(r"8\.[0-5]", request.php_cli_version):
        raise WordPressSetupError("WP-CLI用PHPバージョンが正しくありません")
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
        api: XServerApiClient | None,
        ssh_client: Any,
        sftp: Any,
        ssh_username: str,
        progress_callback: Callable[[WordPressSetupProgress], None] | None = None,
        checkpoint_callback: Callable[[dict], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> WordPressSetupResult:
        validate_setup_request(request)
        progress = progress_callback or (lambda _progress: None)
        checkpoint = checkpoint_callback or (lambda _checkpoint: None)
        cancel = cancellation_token or CancellationToken()
        cancel.raise_if_cancelled()

        def report(
            step: int,
            message: str,
            *,
            current: int | None = None,
            total: int | None = None,
            unit: str = "",
            detail: str = "",
        ) -> None:
            progress(
                WordPressSetupProgress(
                    step=step,
                    total_steps=WORDPRESS_SETUP_TOTAL_STEPS,
                    message=message,
                    current=current,
                    total=total,
                    unit=unit,
                    detail=detail,
                )
            )

        uses_api = request.database_mode == "api"
        if uses_api:
            if api is None:
                raise WordPressSetupError(
                    "APIでDBを新規作成するにはXServer APIキーが必要です"
                )
            server_id = api.servername.split(".", 1)[0]
            expected_db = f"{server_id}_{request.db_name_suffix}"
            expected_user = f"{server_id}_{request.db_user_suffix}"

            report(2, "XServer APIと作成先を確認中")
            api.key_info()
            if expected_db in api.list_databases():
                raise WordPressSetupError(f"Database already exists: {expected_db}")
            if expected_user in api.list_database_users():
                raise WordPressSetupError(
                    f"Database user already exists: {expected_user}"
                )
        else:
            expected_db = request.existing_db_name
            expected_user = request.existing_db_user
            report(2, "作成済みMySQLデータベースの接続情報を確認中")

        remote_root = normalize_remote_root(request.remote_root)
        try:
            entries = tuple(item.filename for item in sftp.listdir_attr(remote_root))
        except OSError as exc:
            raise WordPressSetupError(f"Cannot read the remote root: {exc}") from exc
        unexpected = sorted(set(entries) - {"index.html"})
        if unexpected and not request.resume_existing:
            raise WordPressSetupError(
                "Remote root is not empty; setup stopped: " + ", ".join(unexpected[:10])
            )

        uses_remote_config = request.resume_existing and request.use_remote_wp_config
        if uses_remote_config and "wp-config.php" not in entries:
            raise WordPressSetupError(
                "公開先にwp-config.phpがありません。"
                "「配置済みwp-config.phpのDB接続情報を使用」を解除し、"
                "XServerのDB接続情報を入力してください"
            )

        cancel.raise_if_cancelled()
        mysql_bin = ""
        if not uses_remote_config:
            report(3, "XServerのMySQLコマンドを確認中")
            mysql_bin = _run_remote(
                ssh_client, "command -v mysql || command -v mariadb"
            ).splitlines()[0]
        wp_bin = ""
        php_bin = f"/usr/bin/php{request.php_cli_version}"
        report(3, f"XServerのPHP {request.php_cli_version}とWP-CLIを確認中")
        php_version = _run_remote(
            ssh_client,
            f"test -x {shlex.quote(php_bin)} && "
            f"{shlex.quote(php_bin)} -r 'echo PHP_VERSION;'",
        )
        if not php_version.startswith(request.php_cli_version + "."):
            raise WordPressSetupError(
                f"WP-CLI用PHPの確認に失敗しました: "
                f"指定 {request.php_cli_version}, 検出 {php_version or '不明'}"
            )
        wp_bin = _run_remote(ssh_client, "command -v wp").splitlines()[0]
        _run_remote(
            ssh_client,
            f"{shlex.quote(php_bin)} {shlex.quote(wp_bin)} --info",
        )

        if uses_api:
            assert api is not None
            report(4, "MySQLデータベースを作成中")
            database = api.create_database(
                request.db_name_suffix, memo="Docker XServer Sync"
            )
            try:
                report(4, "MySQLユーザーを作成中")
                user = api.create_database_user(
                    request.db_user_suffix,
                    request.db_password,
                    memo="Docker XServer Sync",
                )
            except Exception as exc:
                raise WordPressSetupError(
                    f"{exc}\n\nCreated database was not deleted automatically: "
                    f"{database.db_name}"
                ) from exc
            try:
                api.grant_database(user.db_user, database.db_name)
            except Exception as exc:
                raise WordPressSetupError(
                    f"{exc}\n\nCreated resources were not deleted automatically: "
                    f"database {database.db_name}, user {user.db_user}"
                ) from exc
            db_name = database.db_name
            db_user = user.db_user
        elif uses_remote_config:
            report(4, "配置済みwp-config.phpのDB接続情報を確認中")
            config_command = (
                f"{shlex.quote(php_bin)} {shlex.quote(wp_bin)} config get {{name}} "
                f"--type=constant --path={shlex.quote(remote_root)}"
            )
            db_name = _run_remote(
                ssh_client, config_command.format(name="DB_NAME")
            ).strip()
            db_user = _run_remote(
                ssh_client, config_command.format(name="DB_USER")
            ).strip()
            if not _FULL_DB_NAME_RE.fullmatch(db_name) or not _FULL_DB_NAME_RE.fullmatch(
                db_user
            ):
                raise WordPressSetupError(
                    "wp-config.phpからDB名またはDBユーザーを確認できません"
                )
        else:
            db_name = expected_db
            db_user = expected_user
            report(4, "作成済みDBとMySQLユーザーを使用します")
        checkpoint(
            {
                "stage": "database_ready",
                "database": db_name,
                "database_user": db_user,
                "resume_existing": request.resume_existing,
            }
        )

        token = uuid.uuid4().hex
        work_dir = f"/home/{ssh_username}/.dxsync/setup/{token}"
        sql_remote = f"{work_dir}/database.sql"
        cnf_remote = f"{work_dir}/mysql.cnf"
        import_started = False
        import_completed = False
        files_transfer_started = False
        files_transfer_completed = False
        config_size = 0
        config_file_count = 0
        try:
            _mkdir_private(sftp, work_dir, base=f"/home/{ssh_username}")

            def transfer_sql_dump() -> None:
                sql_path = request.sql_dump.expanduser().resolve()
                sql_size = sql_path.stat().st_size
                sql_transferred = 0
                report(
                    6,
                    "SQLダンプを暗号化通信で転送中",
                    current=0,
                    total=sql_size,
                    unit="bytes",
                )
                with sql_path.open("rb") as reader, sftp.open(
                    sql_remote, "wb"
                ) as writer:
                    while chunk := reader.read(1024 * 1024):
                        cancel.raise_if_cancelled()
                        writer.write(chunk)
                        sql_transferred += len(chunk)
                        report(
                            6,
                            "SQLダンプを暗号化通信で転送中",
                            current=sql_transferred,
                            total=sql_size,
                            unit="bytes",
                        )
                sftp.chmod(sql_remote, 0o600)

            if uses_remote_config:
                report(5, "wp-config.phpを使って対象DBを確認中")
                preflight_command = " ".join(
                    (
                        shlex.quote(php_bin),
                        shlex.quote(wp_bin),
                        "db query",
                        shlex.quote(
                            "SELECT COUNT(*) FROM information_schema.tables "
                            "WHERE table_schema = DATABASE()"
                        ),
                        "--skip-column-names",
                        "--path=" + shlex.quote(remote_root),
                    )
                )
            else:
                cnf = (
                    "[client]\n"
                    f"host={_mysql_option_value(request.db_host)}\n"
                    f"user={_mysql_option_value(db_user)}\n"
                    f"password={_mysql_option_value(request.db_password)}\n"
                    "default-character-set=utf8mb4\n"
                ).encode("utf-8")
                _upload_bytes(sftp, cnf_remote, cnf)
                preflight_command = (
                    f"{shlex.quote(mysql_bin)} --defaults-extra-file={shlex.quote(cnf_remote)} "
                    f"-Nse 'SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = "
                    f"DATABASE()' {shlex.quote(db_name)}"
                )
                report(5, "対象データベースが空であることを確認中")
            existing_table_count = int(
                _run_remote(ssh_client, preflight_command) or "0"
            )
            resume_needs_transfer = False
            if request.resume_existing:
                required_remote = (
                    f"{remote_root}/wp-load.php",
                    f"{remote_root}/wp-includes",
                    f"{remote_root}/wp-config.php",
                )
                missing_remote: list[str] = []
                for path in required_remote:
                    try:
                        sftp.stat(path)
                    except OSError:
                        missing_remote.append(PurePosixPath(path).name)
                if existing_table_count < 1:
                    if not request.import_sql_if_database_empty:
                        raise WordPressSetupError(
                            "配置先のDBは空です（DBテーブル: 0）。"
                            "再開ウィザードで元のSQLダンプを選択すると、"
                            "DBが空であることを再確認してから復旧します。"
                            f" WordPressファイル不足: "
                            f"{', '.join(missing_remote) or 'なし'}"
                        )
                    cancel.raise_if_cancelled()
                    transfer_sql_dump()
                    if uses_remote_config:
                        import_command = " ".join(
                            (
                                shlex.quote(php_bin),
                                shlex.quote(wp_bin),
                                "db import",
                                shlex.quote(sql_remote),
                                "--path=" + shlex.quote(remote_root),
                            )
                        )
                    else:
                        import_command = (
                            f"{shlex.quote(mysql_bin)} "
                            f"--defaults-extra-file={shlex.quote(cnf_remote)} "
                            f"{shlex.quote(db_name)} < {shlex.quote(sql_remote)}"
                        )
                    report(7, "空のDBへSQLを復旧中")
                    import_started = True
                    _run_remote(ssh_client, import_command)
                    existing_table_count = int(
                        _run_remote(ssh_client, preflight_command) or "0"
                    )
                    if existing_table_count < 1:
                        raise WordPressSetupError(
                            "SQL復旧後もDBテーブルを確認できません"
                        )
                    import_completed = True
                    checkpoint(
                        {
                            "stage": "sql_imported",
                            "database": db_name,
                            "database_user": db_user,
                            "table_count": existing_table_count,
                            "resume_existing": True,
                        }
                    )
                table_count = existing_table_count
                import_completed = True
                transfer = None
                resume_needs_transfer = bool(missing_remote)
                if not resume_needs_transfer:
                    files_transfer_completed = True
                    checkpoint(
                        {
                            "stage": "files_configured",
                            "database": db_name,
                            "database_user": db_user,
                            "table_count": table_count,
                            "resume_existing": True,
                        }
                    )
            elif existing_table_count:
                raise WordPressSetupError(
                    f"指定したDBは空ではありません（{existing_table_count}テーブル）。"
                    "SQLをインポートせずに停止しました"
                )

            if not request.resume_existing:
                cancel.raise_if_cancelled()
                transfer_sql_dump()

                import_command = (
                    f"{shlex.quote(mysql_bin)} --defaults-extra-file={shlex.quote(cnf_remote)} "
                    f"{shlex.quote(db_name)} < {shlex.quote(sql_remote)}"
                )
                report(7, "SQLを新規データベースへインポート中")
                import_started = True
                _run_remote(ssh_client, import_command)

                count_command = (
                    f"{shlex.quote(mysql_bin)} --defaults-extra-file={shlex.quote(cnf_remote)} "
                    f"-Nse 'SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = "
                    f"DATABASE()' {shlex.quote(db_name)}"
                )
                count_output = _run_remote(ssh_client, count_command)
                table_count = int(count_output.strip() or "0")
                if table_count < 1:
                    raise WordPressSetupError("SQL import completed but no tables were found")
                import_completed = True
                checkpoint(
                    {
                        "stage": "sql_imported",
                        "database": db_name,
                        "database_user": db_user,
                        "table_count": table_count,
                    }
                )

                report(8, "WordPressファイルを転送する準備中")
                files_transfer_started = True

                def file_started(current: int, total: int, path: str) -> None:
                    report(
                        8,
                        "WordPressファイルを転送中",
                        current=current,
                        total=total,
                        unit="files",
                        detail=path,
                    )

                def file_completed(current: int, total: int, path: str) -> None:
                    report(
                        8,
                        "WordPressファイルを転送中",
                        current=current,
                        total=total,
                        unit="files",
                        detail=path,
                    )

                transfer = ForceOverwriteEngine().overwrite(
                    sftp=sftp,
                    local_root=request.local_root,
                    remote_root=remote_root,
                    exclude_patterns=COMMON_EXCLUDES
                    + ("wp-config.php", "*.sql", "*.sql.gz"),
                    protect_patterns=(),
                    include_paths=request.include_paths,
                    progress_callback=file_completed,
                    file_started_callback=file_started,
                )
                report(9, "wp-config.phpを生成・配置中")
                config = generate_wp_config(
                    db_name=db_name,
                    db_user=db_user,
                    db_password=request.db_password,
                    db_host=request.db_host,
                    table_prefix=request.table_prefix,
                )
                config_size = len(config)
                config_file_count = 1
                config_temp = f"{remote_root}/wp-config.php.dxsync-{token}.tmp"
                _upload_bytes(sftp, config_temp, config, 0o600)
                destination = f"{remote_root}/wp-config.php"
                rename = getattr(sftp, "posix_rename", None)
                if callable(rename):
                    rename(config_temp, destination)
                else:
                    sftp.rename(config_temp, destination)
                files_transfer_completed = True
                checkpoint(
                    {
                        "stage": "files_configured",
                        "database": db_name,
                        "database_user": db_user,
                        "table_count": table_count,
                        "file_count": len(transfer.files) + 1,
                    }
                )
                cancel.raise_if_cancelled()

            if request.resume_existing and resume_needs_transfer:
                report(8, "中断したWordPressファイル転送を再開中")
                files_transfer_started = True

                def resumed_file_started(current: int, total: int, path: str) -> None:
                    report(
                        8,
                        "WordPressファイルを再転送中",
                        current=current,
                        total=total,
                        unit="files",
                        detail=path,
                    )

                transfer = ForceOverwriteEngine().overwrite(
                    sftp=sftp,
                    local_root=request.local_root,
                    remote_root=remote_root,
                    exclude_patterns=COMMON_EXCLUDES
                    + ("wp-config.php", "*.sql", "*.sql.gz"),
                    protect_patterns=(),
                    include_paths=request.include_paths,
                    progress_callback=resumed_file_started,
                    file_started_callback=resumed_file_started,
                )
                if uses_remote_config:
                    report(9, "配置済みwp-config.phpを保持中")
                else:
                    report(9, "wp-config.phpを生成・配置中")
                    config = generate_wp_config(
                        db_name=db_name,
                        db_user=db_user,
                        db_password=request.db_password,
                        db_host=request.db_host,
                        table_prefix=request.table_prefix,
                    )
                    config_size = len(config)
                    config_file_count = 1
                    config_temp = f"{remote_root}/wp-config.php.dxsync-{token}.tmp"
                    _upload_bytes(sftp, config_temp, config, 0o600)
                    destination = f"{remote_root}/wp-config.php"
                    rename = getattr(sftp, "posix_rename", None)
                    if callable(rename):
                        rename(config_temp, destination)
                    else:
                        sftp.rename(config_temp, destination)
                files_transfer_completed = True
                checkpoint(
                    {
                        "stage": "files_configured",
                        "database": db_name,
                        "database_user": db_user,
                        "table_count": table_count,
                        "file_count": len(transfer.files) + 1,
                        "resume_existing": True,
                    }
                )
                cancel.raise_if_cancelled()

            target_url = request.new_url.rstrip("/")

            def wordpress_option(name: str) -> str:
                return _run_remote(
                    ssh_client,
                    " ".join(
                        (
                            shlex.quote(php_bin),
                            shlex.quote(wp_bin),
                            "option get",
                            shlex.quote(name),
                            "--skip-plugins",
                            "--skip-themes",
                            "--path=" + shlex.quote(remote_root),
                        )
                    ),
                ).strip().rstrip("/")

            report(9, "DB内のWordPress URLを確認中")
            detected_home_url = wordpress_option("home")
            detected_site_url = wordpress_option("siteurl")
            for label, value in (
                ("home", detected_home_url),
                ("siteurl", detected_site_url),
            ):
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise WordPressSetupError(
                        f"WordPressの{label} URLをDBから確認できません: "
                        f"{value or '空'}"
                    )

            replaced_urls = tuple(
                sorted(
                    {
                        value
                        for value in (detected_home_url, detected_site_url)
                        if value != target_url
                    },
                    key=len,
                    reverse=True,
                )
            )
            for source_url in replaced_urls:
                cancel.raise_if_cancelled()
                report(
                    9,
                    "WordPressのURLをシリアライズ対応で置換中",
                    detail=f"{source_url} → {target_url}",
                )
                search_command = " ".join(
                    (
                        shlex.quote(php_bin),
                        shlex.quote(wp_bin),
                        "search-replace",
                        shlex.quote(source_url),
                        shlex.quote(target_url),
                        "--all-tables-with-prefix",
                        "--precise",
                        "--skip-columns=guid",
                        "--path=" + shlex.quote(remote_root),
                    )
                )
                _run_remote(ssh_client, search_command)
            for option_name in ("home", "siteurl"):
                cancel.raise_if_cancelled()
                _run_remote(
                    ssh_client,
                    " ".join(
                        (
                            shlex.quote(php_bin),
                            shlex.quote(wp_bin),
                            "option update",
                            option_name,
                            shlex.quote(target_url),
                            "--skip-plugins",
                            "--skip-themes",
                            "--path=" + shlex.quote(remote_root),
                        )
                    ),
                )
            verified_home_url = wordpress_option("home")
            verified_site_url = wordpress_option("siteurl")
            if (verified_home_url, verified_site_url) != (target_url, target_url):
                raise WordPressSetupError(
                    "WordPress URLの更新を検証できません: "
                    f"home={verified_home_url}, siteurl={verified_site_url}"
                )
            url_replaced = bool(replaced_urls)
            checkpoint(
                {
                    "stage": "url_replaced",
                    "database": db_name,
                    "database_user": db_user,
                    "table_count": table_count,
                    "url_replaced": url_replaced,
                    "detected_home_url": detected_home_url,
                    "detected_site_url": detected_site_url,
                    "public_url": target_url,
                }
            )

            if "index.html" in entries:
                sftp.remove(f"{remote_root}/index.html")
            report(9, "WordPressファイルとDBの配置が完了しました")
            return WordPressSetupResult(
                db_name=db_name,
                db_user=db_user,
                transferred_files=(
                    0
                    if transfer is None
                    else len(transfer.files) + config_file_count
                ),
                transferred_bytes=(
                    0
                    if transfer is None
                    else transfer.total_bytes + config_size
                ),
                table_count=table_count,
                url_replaced=url_replaced,
                public_url=target_url,
                detected_home_url=detected_home_url,
                detected_site_url=detected_site_url,
                replaced_urls=replaced_urls,
            )
        except OperationCancelled:
            raise
        except Exception as exc:
            if isinstance(exc, WordPressSetupError):
                detail = str(exc)
            else:
                detail = str(exc)
            if uses_api:
                recovery = (
                    "Created resources were not deleted automatically: "
                    f"database {db_name}, user {db_user}. "
                )
            else:
                recovery = (
                    f"作成済みDB {db_name} は削除・初期化していません。"
                )
                if import_completed:
                    recovery += "SQLインポートは完了しています。"
                elif import_started:
                    recovery += "SQLが途中まで入っている可能性があります。"
                else:
                    recovery += "SQLインポートは開始していません。"
            if files_transfer_completed:
                recovery += (
                    "WordPressファイルとwp-config.phpの配置は"
                    "完了しています。"
                )
            elif files_transfer_started:
                recovery += (
                    "WordPressファイルが公開先へ一部配置された可能性があります。"
                    "再実行前に公開先を確認してください。"
                )
            else:
                recovery += "WordPressファイル配置は開始していません。"
            raise WordPressSetupError(f"{detail}\n\n{recovery}") from exc
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
