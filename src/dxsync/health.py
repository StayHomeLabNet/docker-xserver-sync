from __future__ import annotations

import http.client
from dataclasses import dataclass
from typing import Callable, Protocol


class HttpResponse(Protocol):
    status: int

    def read(self, amount: int | None = None) -> bytes: ...


class HttpsConnection(Protocol):
    def request(self, method: str, url: str, headers: dict[str, str]) -> None: ...
    def getresponse(self) -> HttpResponse: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class HealthCheckResult:
    url: str
    ok: bool
    status_code: int | None = None
    error: str | None = None


def normalize_health_check_path(value: str) -> str:
    path = value.strip()
    if not path:
        raise ValueError("確認パスを入力してください。")
    if any(character in path for character in ("\x00", "\r", "\n")):
        raise ValueError("確認パスに制御文字は使えません。")
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("確認パスは / から始めてください。")
    if any(character.isspace() for character in path):
        raise ValueError("確認パスに空白は使えません。")
    return path


def perform_https_health_check(
    *,
    domain: str,
    path: str,
    timeout: float = 10.0,
    connection_factory: Callable[..., HttpsConnection] = http.client.HTTPSConnection,
) -> HealthCheckResult:
    normalized_path = normalize_health_check_path(path)
    url = f"https://{domain}{normalized_path}"
    connection: HttpsConnection | None = None
    try:
        connection = connection_factory(domain, timeout=timeout)
        connection.request(
            "GET",
            normalized_path,
            headers={"User-Agent": "Docker-XServer-Sync/0.1 health-check"},
        )
        response = connection.getresponse()
        response.read(1024)
        status = int(response.status)
        return HealthCheckResult(
            url=url,
            ok=200 <= status < 400,
            status_code=status,
            error=None if 200 <= status < 400 else f"HTTP {status}",
        )
    except Exception as exc:
        return HealthCheckResult(url=url, ok=False, error=str(exc))
    finally:
        if connection is not None:
            connection.close()
