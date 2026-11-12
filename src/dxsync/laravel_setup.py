from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

from .cancellation import CancellationToken, OperationCancelled
from .presets import COMMON_EXCLUDES
from .validation import normalize_remote_root
from .xserver_api import XServerApiClient


class LaravelSetupError(RuntimeError):
    pass


LARAVEL_SETUP_TOTAL_STEPS = 10


@dataclass(frozen=True)
class LaravelSetupProgress:
    step: int
    total_steps: int
    message: str
    current: int | None = None
    total: int | None = None
    unit: str = ""
    detail: str = ""


@dataclass(frozen=True)
class LaravelSetupRequest:
    local_root: Path
    remote_app_root: str
    remote_public_root: str
    domain: str
    app_url: str
    app_name: str
    db_host: str
    database_mode: str  # "api" or "existing"
    db_name_suffix: str
    db_user_suffix: str
    db_password: str
    existing_db_name: str = ""
    existing_db_user: str = ""
    php_cli_version: str = "8.3"
    migration_mode: str = "migrate"  # "migrate" or "sql_dump"
    sql_dump: Path | None = None
    run_seed: bool = False
    resume_existing: bool = False


@dataclass(frozen=True)
class LaravelSetupResult:
    db_name: str
    db_user: str
    transferred_app_files: int
    transferred_public_files: int
    transferred_bytes: int
    migration_output: str
    public_url: str
    https_confirmed: bool


_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_FULL_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _validate_sql_dump(path: Path) -> None:
    dump = path.expanduser().resolve()
    if not dump.is_file() or dump.stat().st_size == 0:
        raise LaravelSetupError("A non-empty SQL dump file is required")
    dangerous_sql = re.compile(rb"^\s*(?:CREATE|DROP)\s+DATABASE\b|^\s*USE\s+", re.I)
    with dump.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            if dangerous_sql.search(line):
                raise LaravelSetupError(
                    "SQL dump contains a forbidden database-level statement "
                    f"at line {line_number}"
                )


def detect_laravel_required_php(local_root: Path) -> str | None:
    """Detects the minimum required PHP version from vendor/composer/platform_check.php or composer.json."""
    root = local_root.expanduser().resolve()
    platform_check = root / "vendor" / "composer" / "platform_check.php"
    if platform_check.is_file():
        try:
            content = platform_check.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r"PHP_VERSION_ID\s*>=\s*(\d+)", content)
            if match:
                val = int(match.group(1))
                major = val // 10000
                minor = (val % 10000) // 100
                return f"{major}.{minor}"
        except OSError:
            pass

    composer_json = root / "composer.json"
    if composer_json.is_file():
        try:
            data = json.loads(composer_json.read_text(encoding="utf-8"))
            php_req = data.get("require", {}).get("php", "")
            match = re.search(r"[~^>=]*\s*(\d+\.\d+)", php_req)
            if match:
                return match.group(1)
        except Exception:
            pass

    return None


def validate_laravel_setup_request(request: LaravelSetupRequest) -> None:
    root = request.local_root.expanduser().resolve()
    if not root.is_dir():
        raise LaravelSetupError(f"Laravel root is not a directory: {root}")
    for required in ("artisan", "composer.json", "app", "public", "bootstrap"):
        if not (root / required).exists():
            raise LaravelSetupError(
                f"Missing required Laravel entry '{required}' in {root}"
            )

    vendor_dir = root / "vendor"
    if not vendor_dir.is_dir() or not (vendor_dir / "autoload.php").is_file():
        raise LaravelSetupError(
            "Local vendor/ directory not found or autoload.php is missing. "
            "Please run 'composer install' in your Docker/local environment before setup."
        )

    required_php = detect_laravel_required_php(root)
    if required_php:
        def _to_tuple(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)
        if _to_tuple(request.php_cli_version) < _to_tuple(required_php):
            raise LaravelSetupError(
                f"ローカルのComposer環境（platform_check.php）は PHP {required_php} 以上を要求しています。"
                f"指定されたCLI用PHPバージョン（PHP {request.php_cli_version}）では動作しません。PHP {required_php} 以上を指定してください。"
            )

    if not request.remote_app_root.strip():
        raise LaravelSetupError("Remote application root is required")
    if not request.remote_public_root.strip():
        raise LaravelSetupError("Remote public root is required")
    if request.remote_app_root.strip() == request.remote_public_root.strip():
        raise LaravelSetupError(
            "Remote application root and public root must not be the same directory"
        )

    if not request.domain.strip():
        raise LaravelSetupError("Target domain is required")

    parsed_url = urlparse(request.app_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise LaravelSetupError(f"Invalid public URL: {request.app_url}")

    if request.database_mode == "api":
        if not _SUFFIX_RE.match(request.db_name_suffix):
            raise LaravelSetupError("Invalid database name suffix")
        if not _SUFFIX_RE.match(request.db_user_suffix):
            raise LaravelSetupError("Invalid database user suffix")
        if not request.db_password:
            raise LaravelSetupError("Database password is required")
    elif request.database_mode == "existing":
        if not _FULL_DB_NAME_RE.match(request.existing_db_name):
            raise LaravelSetupError("Invalid existing database name")
        if not _FULL_DB_NAME_RE.match(request.existing_db_user):
            raise LaravelSetupError("Invalid existing database user")
        if not request.db_password:
            raise LaravelSetupError("Database password is required")
    else:
        raise LaravelSetupError(f"Unsupported database mode: {request.database_mode}")

    if request.migration_mode == "sql_dump":
        if request.sql_dump is None:
            raise LaravelSetupError("SQL dump file is required for sql_dump mode")
        _validate_sql_dump(request.sql_dump)


def build_laravel_env(
    template_content: str,
    *,
    app_name: str,
    app_url: str,
    db_host: str,
    db_name: str,
    db_user: str,
    db_password: str,
) -> str:
    """Updates or adds standard Laravel environment variables for production."""
    replacements = {
        "APP_NAME": f'"{app_name}"' if " " in app_name else app_name,
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "APP_URL": app_url,
        "DB_CONNECTION": "mysql",
        "DB_HOST": db_host or "localhost",
        "DB_PORT": "3306",
        "DB_DATABASE": db_name,
        "DB_USERNAME": db_user,
        "DB_PASSWORD": f'"{db_password}"' if re.search(r'[\s#"\']', db_password) else db_password,
    }

    lines = template_content.splitlines()
    found_keys = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in line:
            key, _ = line.split("=", 1)
            key = key.strip()
            if key in replacements:
                new_lines.append(f"{key}={replacements[key]}")
                found_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys not found in the template
    for key, val in replacements.items():
        if key not in found_keys:
            new_lines.append(f"{key}={val}")

    return "\n".join(new_lines) + "\n"


def patch_laravel_index_php(
    content: str,
    *,
    remote_app_root: str,
    remote_public_root: str,
) -> str:
    """Adjusts the paths in public/index.php to point to remote_app_root."""
    rel_path = posixpath.relpath(remote_app_root, remote_public_root)
    # Ensure forward slashes
    rel_path = rel_path.replace("\\", "/")

    # Matches __DIR__.'/../ or __DIR__ . '/../ or __DIR__."/../" etc.
    # Replaces __DIR__ . '/../ with __DIR__ . '/{rel_path}/
    pattern = r"__DIR__\s*\.\s*['\"]\/..\/"
    replacement = f"__DIR__ . '/{rel_path}/"

    patched = re.sub(pattern, replacement, content)
    return patched


def _run_remote(ssh_client: Any, command: str) -> str:
    _, stdout, stderr = ssh_client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if exit_status != 0:
        raise LaravelSetupError(
            f"Remote command failed (code {exit_status}): {command}\n{err or out}"
        )
    return out


def _scan_remote_files(ssh_client: Any, php_bin: str, remote_root: str) -> dict[str, int]:
    """Scans the remote directory using a fast remote PHP script to return {rel_path: size}.

    If the remote directory does not exist or scanning fails, returns an empty dictionary.
    """
    php_code = (
        "$dir = $argv[1];"
        "if (!is_dir($dir)) exit(0);"
        "$iter = new RecursiveIteratorIterator("
        "new RecursiveDirectoryIterator($dir, FilesystemIterator::SKIP_DOTS),"
        "RecursiveIteratorIterator::LEAVES_ONLY);"
        "$len = strlen($dir);"
        "foreach ($iter as $file) {"
        "if ($file->isFile()) {"
        "$rel = ltrim(substr($file->getPathname(), $len), '/');"
        "echo $rel . \"\\t\" . $file->getSize() . \"\\n\";"
        "}"
        "}"
    )
    cmd = f"{shlex.quote(php_bin)} -r {shlex.quote(php_code)} -- {shlex.quote(remote_root)}"
    try:
        _, stdout, stderr = ssh_client.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            return {}
        result: dict[str, int] = {}
        for line in stdout.read().decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                try:
                    result[parts[0]] = int(parts[1])
                except ValueError:
                    pass
        return result
    except Exception:
        return {}


def _upload_file(sftp: Any, local_path: Path, remote_path: str) -> int:
    remote_dir = posixpath.dirname(remote_path)
    _ensure_remote_dir(sftp, remote_dir)
    tmp_path = f"{remote_path}.dxsync_tmp_{os.urandom(4).hex()}"
    try:
        sftp.put(str(local_path), tmp_path)
        posix_rename = getattr(sftp, "posix_rename", None)
        if callable(posix_rename):
            try:
                posix_rename(tmp_path, remote_path)
                return local_path.stat().st_size
            except OSError:
                pass
        # Fallback for servers without posix-rename or if it failed
        try:
            sftp.remove(remote_path)
        except OSError:
            pass
        sftp.rename(tmp_path, remote_path)
        return local_path.stat().st_size
    except Exception as exc:
        raise LaravelSetupError(
            f"ファイル転送に失敗しました ({local_path.name} -> {remote_path}): {exc}"
        ) from exc
    finally:
        try:
            sftp.remove(tmp_path)
        except OSError:
            pass


def _ensure_remote_dir(sftp: Any, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    current = "/" if remote_dir.startswith("/") else ""
    for part in parts:
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except Exception:
            try:
                sftp.mkdir(current)
            except Exception:
                pass


class LaravelSetupEngine:
    def setup(
        self,
        *,
        request: LaravelSetupRequest,
        api: XServerApiClient | None,
        ssh_client: Any,
        sftp: Any,
        ssh_username: str,
        progress_callback: Callable[[LaravelSetupProgress], None] | None = None,
        checkpoint_callback: Callable[[dict], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> LaravelSetupResult:
        validate_laravel_setup_request(request)
        progress = progress_callback or (lambda _p: None)
        checkpoint = checkpoint_callback or (lambda _c: None)
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
                LaravelSetupProgress(
                    step=step,
                    total_steps=LARAVEL_SETUP_TOTAL_STEPS,
                    message=message,
                    current=current,
                    total=total,
                    unit=unit,
                    detail=detail,
                )
            )

        report(1, "設定と環境のバリデーションを実行中")
        uses_api = request.database_mode == "api"
        if uses_api:
            if api is None:
                raise LaravelSetupError("APIでDBを新規作成するにはXServer APIキーが必要です")
            server_id = api.servername.split(".", 1)[0]
            expected_db = f"{server_id}_{request.db_name_suffix}"
            expected_user = f"{server_id}_{request.db_user_suffix}"
            report(2, "XServer APIと作成先を確認中")
            api.key_info()
            if expected_db in api.list_databases():
                raise LaravelSetupError(f"Database already exists: {expected_db}")
            if expected_user in api.list_database_users():
                raise LaravelSetupError(f"Database user already exists: {expected_user}")
        else:
            expected_db = request.existing_db_name
            expected_user = request.existing_db_user
            report(2, "作成済みMySQLデータベースの接続情報を確認中")

        remote_app_root = normalize_remote_root(request.remote_app_root)
        remote_public_root = normalize_remote_root(request.remote_public_root)

        # Step 3: Remote environment inspection
        cancel.raise_if_cancelled()
        report(3, "XServerのMySQLコマンドとPHPを確認中")
        mysql_bin = _run_remote(
            ssh_client, "command -v mysql || command -v mariadb"
        ).splitlines()[0]

        php_bin = f"/usr/bin/php{request.php_cli_version}"
        php_version = _run_remote(
            ssh_client,
            f"test -x {shlex.quote(php_bin)} && {shlex.quote(php_bin)} -r 'echo PHP_VERSION;'",
        )
        if not php_version.startswith(request.php_cli_version + "."):
            raise LaravelSetupError(
                f"PHP CLIバージョンの確認に失敗しました: 指定 {request.php_cli_version}, 検出 {php_version or '不明'}"
            )

        # Step 2 execution: Create DB via API if requested
        if uses_api:
            assert api is not None
            report(2, "MySQLデータベースを作成中")
            api.create_database(request.db_name_suffix, memo="Docker XServer Sync - Laravel")
            report(2, "MySQLユーザーを作成中")
            api.create_database_user(
                request.db_user_suffix,
                request.db_password,
                memo="Docker XServer Sync - Laravel",
            )
            report(2, "データベースアクセス権を付与中")
            api.add_database_user_access(expected_db, expected_user)

        # Test database connectivity
        report(2, "MySQL接続確認中")
        db_check_cmd = (
            f"{shlex.quote(mysql_bin)} -h {shlex.quote(request.db_host or 'localhost')} "
            f"-u {shlex.quote(expected_user)} -p{shlex.quote(request.db_password)} "
            f"-D {shlex.quote(expected_db)} -e 'SELECT 1;'"
        )
        _run_remote(ssh_client, db_check_cmd)

        cancel.raise_if_cancelled()

        # Step 4: Transfer Application Files (excluding public/)
        report(4, "Laravel本体ファイルの配置状況を確認中（public/を除く）")
        local_root = request.local_root.expanduser().resolve()
        app_files_transferred = 0
        total_bytes = 0

        # Scan remote application directory to check for already transferred files
        remote_app_files = _scan_remote_files(ssh_client, php_bin, remote_app_root)

        # Collect files for application root
        app_candidates: list[Path] = []
        app_excludes = set(COMMON_EXCLUDES) | {"public", "/public", "public/"}

        for path in local_root.rglob("*"):
            if path.is_file():
                rel = path.relative_to(local_root)
                rel_posix = rel.as_posix()
                # Check exclusion: startswith public/ or in app_excludes
                if rel_posix.startswith("public/") or rel_posix == "public":
                    continue
                # Simple check against common excludes
                should_exclude = False
                for exc in COMMON_EXCLUDES:
                    exc_clean = exc.strip("/").strip("*")
                    if exc_clean and (rel_posix == exc_clean or rel_posix.startswith(f"{exc_clean}/") or f"/{exc_clean}/" in f"/{rel_posix}/"):
                        should_exclude = True
                        break
                if not should_exclude:
                    app_candidates.append(path)

        total_app_files = len(app_candidates)
        report(4, f"Laravel本体ファイルを転送中（全{total_app_files}ファイル）")
        for i, local_file in enumerate(app_candidates, start=1):
            cancel.raise_if_cancelled()
            rel = local_file.relative_to(local_root).as_posix()
            remote_target = posixpath.join(remote_app_root, rel)
            local_size = local_file.stat().st_size
            if rel in remote_app_files and remote_app_files[rel] == local_size:
                # Already present on remote with identical size
                if i % 25 == 0 or i == total_app_files:
                    report(
                        4,
                        "Laravel本体ファイルを検証・転送中",
                        current=i,
                        total=total_app_files,
                        unit="files",
                        detail=f"{rel}（配置済み）",
                    )
                continue

            bytes_sent = _upload_file(sftp, local_file, remote_target)
            app_files_transferred += 1
            total_bytes += bytes_sent
            if i % 25 == 0 or i == total_app_files:
                report(
                    4,
                    "Laravel本体ファイルを転送中",
                    current=i,
                    total=total_app_files,
                    unit="files",
                    detail=rel,
                )

        cancel.raise_if_cancelled()

        # Step 5: Transfer Public Files
        report(5, "公開ディレクトリ（public/）の配置状況を確認中")
        public_files_transferred = 0
        remote_pub_files = _scan_remote_files(ssh_client, php_bin, remote_public_root)
        local_public = local_root / "public"
        public_candidates: list[Path] = []
        for path in local_public.rglob("*"):
            if path.is_file():
                rel = path.relative_to(local_public).as_posix()
                should_exclude = False
                for exc in COMMON_EXCLUDES:
                    exc_clean = exc.strip("/").strip("*")
                    if exc_clean and (rel == exc_clean or rel.startswith(f"{exc_clean}/") or f"/{exc_clean}/" in f"/{rel}/"):
                        should_exclude = True
                        break
                if not should_exclude:
                    public_candidates.append(path)

        total_pub_files = len(public_candidates)
        report(5, f"公開ディレクトリ（public/）のファイルを転送中（全{total_pub_files}ファイル）")
        for i, local_file in enumerate(public_candidates, start=1):
            cancel.raise_if_cancelled()
            rel = local_file.relative_to(local_public).as_posix()
            remote_target = posixpath.join(remote_public_root, rel)
            local_size = local_file.stat().st_size
            if rel in remote_pub_files and remote_pub_files[rel] == local_size:
                if i % 10 == 0 or i == total_pub_files:
                    report(
                        5,
                        "公開ディレクトリのファイルを検証・転送中",
                        current=i,
                        total=total_pub_files,
                        unit="files",
                        detail=f"{rel}（配置済み）",
                    )
                continue

            bytes_sent = _upload_file(sftp, local_file, remote_target)
            public_files_transferred += 1
            total_bytes += bytes_sent
            if i % 10 == 0 or i == total_pub_files:
                report(
                    5,
                    "公開ディレクトリのファイルを転送中",
                    current=i,
                    total=total_pub_files,
                    unit="files",
                    detail=rel,
                )

        cancel.raise_if_cancelled()

        # Step 6: Patch public_html/index.php
        report(6, "public_html/index.php のパスを調整中")
        remote_index_php = posixpath.join(remote_public_root, "index.php")
        try:
            with sftp.open(remote_index_php, "r") as f:
                raw_index = f.read().decode("utf-8", errors="replace")

            patched_index = patch_laravel_index_php(
                raw_index,
                remote_app_root=remote_app_root,
                remote_public_root=remote_public_root,
            )
            with sftp.open(remote_index_php, "w") as f:
                f.write(patched_index.encode("utf-8"))
        except Exception as exc:
            raise LaravelSetupError(
                f"public/index.php の調整に失敗しました ({remote_index_php}): {exc}"
            ) from exc

        cancel.raise_if_cancelled()

        # Step 7: Generate and upload .env
        report(7, "本番用 .env を生成・配置中")
        try:
            local_env_example = local_root / ".env.example"
            local_env = local_root / ".env"
            template_str = ""
            if local_env_example.is_file():
                template_str = local_env_example.read_text(encoding="utf-8")
            elif local_env.is_file():
                template_str = local_env.read_text(encoding="utf-8")

            new_env_content = build_laravel_env(
                template_str,
                app_name=request.app_name,
                app_url=request.app_url,
                db_host=request.db_host,
                db_name=expected_db,
                db_user=expected_user,
                db_password=request.db_password,
            )
            remote_env_path = posixpath.join(remote_app_root, ".env")
            with sftp.open(remote_env_path, "w") as f:
                f.write(new_env_content.encode("utf-8"))
            sftp.chmod(remote_env_path, 0o600)
        except Exception as exc:
            raise LaravelSetupError(
                f".env の生成・配置に失敗しました ({remote_env_path}): {exc}"
            ) from exc

        cancel.raise_if_cancelled()

        # Step 8: Permissions & Key Generation
        report(8, "ストレージ権限の設定とAPP_KEYの生成中")
        artisan_path = posixpath.join(remote_app_root, "artisan")
        # Ensure storage & bootstrap/cache exist and have 0775 permissions
        _run_remote(
            ssh_client,
            f"mkdir -p {shlex.quote(remote_app_root)}/storage/framework/cache "
            f"{shlex.quote(remote_app_root)}/storage/framework/sessions "
            f"{shlex.quote(remote_app_root)}/storage/framework/views "
            f"{shlex.quote(remote_app_root)}/storage/logs "
            f"{shlex.quote(remote_app_root)}/bootstrap/cache && "
            f"chmod -R 775 {shlex.quote(remote_app_root)}/storage "
            f"{shlex.quote(remote_app_root)}/bootstrap/cache",
        )

        # Generate APP_KEY
        _run_remote(
            ssh_client,
            f"{shlex.quote(php_bin)} {shlex.quote(artisan_path)} key:generate --force",
        )

        cancel.raise_if_cancelled()

        # Step 9: Database Migration / Import, Links & Cache
        migration_out = ""
        if request.migration_mode == "sql_dump" and request.sql_dump is not None:
            report(9, "SQLダンプをデータベースへインポート中")
            remote_sql_tmp = f"{remote_app_root}/.dxsync_dump_{os.urandom(4).hex()}.sql"
            sftp.put(str(request.sql_dump), remote_sql_tmp)
            try:
                import_cmd = (
                    f"{shlex.quote(mysql_bin)} -h {shlex.quote(request.db_host or 'localhost')} "
                    f"-u {shlex.quote(expected_user)} -p{shlex.quote(request.db_password)} "
                    f"-D {shlex.quote(expected_db)} < {shlex.quote(remote_sql_tmp)}"
                )
                migration_out = _run_remote(ssh_client, import_cmd)
            finally:
                try:
                    sftp.remove(remote_sql_tmp)
                except OSError:
                    pass
        else:
            report(9, "データベースマイグレーションを実行中")
            seed_flag = " --seed" if request.run_seed else ""
            migrate_cmd = (
                f"{shlex.quote(php_bin)} {shlex.quote(artisan_path)} migrate --force{seed_flag}"
            )
            migration_out = _run_remote(ssh_client, migrate_cmd)

        report(9, "ストレージリンクと最適化キャッシュを生成中")
        # storage:link
        _run_remote(
            ssh_client,
            f"{shlex.quote(php_bin)} {shlex.quote(artisan_path)} storage:link || true",
        )
        # config/route/view cache (graceful)
        _run_remote(
            ssh_client,
            f"{shlex.quote(php_bin)} {shlex.quote(artisan_path)} config:cache || true",
        )
        _run_remote(
            ssh_client,
            f"{shlex.quote(php_bin)} {shlex.quote(artisan_path)} route:cache || true",
        )
        _run_remote(
            ssh_client,
            f"{shlex.quote(php_bin)} {shlex.quote(artisan_path)} view:cache || true",
        )

        cancel.raise_if_cancelled()

        # Step 10: Complete
        report(10, "Laravelセットアップが完了しました")
        return LaravelSetupResult(
            db_name=expected_db,
            db_user=expected_user,
            transferred_app_files=app_files_transferred,
            transferred_public_files=public_files_transferred,
            transferred_bytes=total_bytes,
            migration_output=migration_out,
            public_url=request.app_url,
            https_confirmed=False,
        )
