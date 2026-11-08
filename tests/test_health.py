from __future__ import annotations

from dataclasses import dataclass

import pytest

from dxsync.health import normalize_health_check_path, perform_https_health_check


@dataclass
class FakeResponse:
    status: int
    body: bytes = b"ok"
    location: str | None = None

    def read(self, amount: int | None = None) -> bytes:
        return self.body if amount is None else self.body[:amount]

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.location if name.lower() == "location" else default


class FakeConnection:
    def __init__(
        self,
        domain: str,
        *,
        timeout: float,
        status: int = 200,
        location: str | None = None,
    ) -> None:
        self.domain = domain
        self.timeout = timeout
        self.status = status
        self.location = location
        self.requested: tuple[str, str, dict[str, str]] | None = None
        self.closed = False

    def request(self, method: str, url: str, headers: dict[str, str]) -> None:
        self.requested = (method, url, headers)

    def getresponse(self) -> FakeResponse:
        return FakeResponse(self.status, location=self.location)

    def close(self) -> None:
        self.closed = True


def test_https_health_check_accepts_success_and_redirect_status() -> None:
    connections: list[FakeConnection] = []

    def factory(domain: str, *, timeout: float) -> FakeConnection:
        connection = FakeConnection(domain, timeout=timeout, status=302)
        connections.append(connection)
        return connection

    result = perform_https_health_check(
        domain="example.com",
        path="/health.php",
        connection_factory=factory,
    )

    assert result.ok
    assert result.status_code == 302
    assert result.url == "https://example.com/health.php"
    assert connections[0].requested[0:2] == ("GET", "/health.php")
    assert connections[0].closed


def test_https_health_check_reports_http_failure_without_raising() -> None:
    result = perform_https_health_check(
        domain="example.com",
        path="/",
        connection_factory=lambda domain, timeout: FakeConnection(
            domain, timeout=timeout, status=500
        ),
    )

    assert not result.ok
    assert result.status_code == 500
    assert result.error == "HTTP 500"


def test_https_health_check_rejects_redirect_to_unexpected_port() -> None:
    result = perform_https_health_check(
        domain="demo.example.com",
        path="/",
        connection_factory=lambda domain, timeout: FakeConnection(
            domain,
            timeout=timeout,
            status=301,
            location="https://demo.example.com:9999/",
        ),
    )

    assert not result.ok
    assert result.status_code == 301
    assert result.error == (
        "想定外のリダイレクト: https://demo.example.com:9999/"
    )


@pytest.mark.parametrize("value", ["", "health", "//other.example/path", "/has space"])
def test_unsafe_health_check_path_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_health_check_path(value)
