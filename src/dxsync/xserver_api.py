from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class XServerApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatedDatabase:
    db_name: str


@dataclass(frozen=True)
class CreatedDatabaseUser:
    db_user: str


class XServerApiClient:
    """Small, dependency-free client for the XServer server API."""

    def __init__(
        self,
        *,
        api_key: str,
        servername: str,
        base_url: str = "https://api.xserver.ne.jp",
        opener: Callable[..., Any] = urlopen,
        timeout: float = 30.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("XServer API key is required")
        if not servername.strip() or "/" in servername:
            raise ValueError("Invalid XServer server name")
        self._api_key = api_key.strip()
        self.servername = servername.strip()
        self._base_url = base_url.rstrip("/")
        self._opener = opener
        self._timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self._base_url + path,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = ""
            try:
                parsed = json.loads(exc.read().decode("utf-8"))
                error = parsed.get("error", {})
                detail = error.get("message") or error.get("code") or ""
            except Exception:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise XServerApiError(f"XServer API returned HTTP {exc.code}{suffix}") from exc
        except URLError as exc:
            raise XServerApiError(f"Cannot connect to XServer API: {exc.reason}") from exc
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise XServerApiError("XServer API returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise XServerApiError("XServer API returned an unexpected response")
        return value

    @property
    def _server_path(self) -> str:
        return "/v1/server/" + quote(self.servername, safe=".-")

    def key_info(self) -> dict[str, Any]:
        return self._request("GET", "/v1/me")

    def list_databases(self) -> tuple[str, ...]:
        value = self._request("GET", self._server_path + "/db")
        return tuple(item["db_name"] for item in value.get("databases", ()))

    def list_database_users(self) -> tuple[str, ...]:
        value = self._request("GET", self._server_path + "/db/user")
        return tuple(item["db_user"] for item in value.get("users", ()))

    def create_database(self, name_suffix: str, *, memo: str = "") -> CreatedDatabase:
        value = self._request(
            "POST",
            self._server_path + "/db",
            {"name_suffix": name_suffix, "character_set": "utf8mb4", "memo": memo},
        )
        db_name = value.get("db_name")
        if not isinstance(db_name, str) or not db_name:
            raise XServerApiError("XServer API did not return the created database name")
        return CreatedDatabase(db_name)

    def create_database_user(
        self,
        name_suffix: str,
        password: str,
        *,
        memo: str = "",
    ) -> CreatedDatabaseUser:
        value = self._request(
            "POST",
            self._server_path + "/db/user",
            {"name_suffix": name_suffix, "password": password, "memo": memo},
        )
        db_user = value.get("db_user")
        if not isinstance(db_user, str) or not db_user:
            raise XServerApiError("XServer API did not return the created database user name")
        return CreatedDatabaseUser(db_user)

    def grant_database(self, db_user: str, db_name: str) -> None:
        user = quote(db_user, safe="_-.@")
        self._request(
            "POST",
            self._server_path + f"/db/user/{user}/grant",
            {"db_name": db_name},
        )
