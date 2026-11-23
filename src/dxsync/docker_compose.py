from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

COMPOSE_FILENAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)


class DockerComposeError(RuntimeError):
    pass


@dataclass(frozen=True)
class DockerComposeDbService:
    service_name: str
    image: str = ""
    database: str = ""
    user: str = ""
    password: str = ""
    root_password: str = ""


def find_compose_file(start_dir: Path) -> Path | None:
    current = start_dir.expanduser()
    for _ in range(3):
        for name in COMPOSE_FILENAMES:
            candidate = current / name
            if candidate.is_file():
                return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def _strip_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _parse_compose_services(text: str) -> dict[str, dict[str, object]]:
    services: dict[str, dict[str, object]] = {}
    in_services = False
    services_indent = -1
    current_service: str | None = None
    service_indent = -1
    in_env = False
    env_indent = -1

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))

        if not in_services:
            if stripped == "services:":
                in_services = True
                services_indent = indent
            continue

        if indent <= services_indent:
            break

        if service_indent == -1 or indent == service_indent:
            match = re.match(r"^([A-Za-z0-9_.-]+):\s*$", stripped)
            if match:
                service_indent = indent
                current_service = match.group(1)
                services[current_service] = {"image": "", "env": {}}
                in_env = False
                continue

        if current_service is None:
            continue

        if indent <= service_indent:
            in_env = False
            continue

        if in_env and indent <= env_indent:
            in_env = False

        if stripped == "environment:":
            in_env = True
            env_indent = indent
            continue

        if in_env:
            env = services[current_service]["env"]
            assert isinstance(env, dict)
            item = stripped[1:].strip() if stripped.startswith("-") else stripped
            if "=" in item:
                key, val = item.split("=", 1)
                env[key.strip()] = _strip_quotes(val)
            elif ":" in item:
                key, val = item.split(":", 1)
                env[key.strip()] = _strip_quotes(val)
            continue

        if stripped.startswith("image:"):
            services[current_service]["image"] = _strip_quotes(
                stripped.split(":", 1)[1]
            )
    return services


def detect_db_services_from_compose(
    compose_path: Path,
) -> tuple[DockerComposeDbService, ...]:
    try:
        text = compose_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DockerComposeError(f"Compose ファイルを読み取れません: {exc}") from exc

    parsed = _parse_compose_services(text)
    candidates: list[DockerComposeDbService] = []
    for name, info in parsed.items():
        image = str(info.get("image", ""))
        env = info.get("env", {})
        assert isinstance(env, dict)
        lower_image = image.lower()
        lower_name = name.lower()
        is_mysql_like = (
            "mysql" in lower_image
            or "mariadb" in lower_image
            or lower_name in {"db", "mysql", "mariadb", "database"}
            or any(
                key in env
                for key in (
                    "MYSQL_DATABASE",
                    "MARIADB_DATABASE",
                    "MYSQL_ROOT_PASSWORD",
                    "MARIADB_ROOT_PASSWORD",
                )
            )
        )
        if not is_mysql_like:
            continue
        database = str(
            env.get("MYSQL_DATABASE")
            or env.get("MARIADB_DATABASE")
            or env.get("WORDPRESS_DB_NAME")
            or env.get("DB_DATABASE")
            or ""
        )
        user = str(env.get("MYSQL_USER") or env.get("MARIADB_USER") or "")
        password = str(
            env.get("MYSQL_PASSWORD") or env.get("MARIADB_PASSWORD") or ""
        )
        root_password = str(
            env.get("MYSQL_ROOT_PASSWORD")
            or env.get("MARIADB_ROOT_PASSWORD")
            or ""
        )
        candidates.append(
            DockerComposeDbService(
                service_name=name,
                image=image,
                database=database,
                user=user,
                password=password,
                root_password=root_password,
            )
        )
    return tuple(candidates)


def export_sql_dump_from_compose(
    compose_path: Path,
    service: DockerComposeDbService,
    output_path: Path | None = None,
    *,
    timeout: float = 120.0,
) -> Path:
    db_name = service.database.strip()
    if not db_name:
        raise DockerComposeError(
            f"サービス '{service.service_name}' からデータベース名を検出できませんでした。"
        )
    username = "root" if service.root_password else (service.user or "root")
    password = service.root_password or service.password
    quoted_user = shlex.quote(username)
    quoted_db = shlex.quote(db_name)
    dump_cmd = (
        "DUMP_BIN=$(command -v mariadb-dump || command -v mysqldump); "
        f"[ -n \"$DUMP_BIN\" ] && exec \"$DUMP_BIN\" -u {quoted_user} "
        f"--single-transaction --quick {quoted_db}"
    )
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_path),
        "exec",
        "-T",
    ]
    if password:
        command.extend(["-e", f"MYSQL_PWD={password}"])
    command.extend([service.service_name, "sh", "-lc", dump_cmd])

    try:
        completed = subprocess.run(
            command,
            cwd=str(compose_path.parent),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerComposeError(
            f"SQLダンプ生成がタイムアウトしました（{timeout:.0f}秒）"
        ) from exc
    except OSError as exc:
        raise DockerComposeError(f"docker compose コマンドを実行できません: {exc}") from exc

    if completed.returncode != 0 or not completed.stdout.strip():
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise DockerComposeError(
            f"SQLダンプの生成に失敗しました: {detail or '出力が空です'}"
        )

    if output_path is None:
        handle = tempfile.NamedTemporaryFile(
            prefix="dxsync-dump-",
            suffix=".sql",
            delete=False,
        )
        target = Path(handle.name)
        handle.close()
    else:
        target = output_path
    target.write_bytes(completed.stdout)
    return target
