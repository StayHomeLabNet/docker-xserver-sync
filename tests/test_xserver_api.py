from __future__ import annotations

import io
import json

from dxsync.xserver_api import XServerApiClient


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_xserver_api_creates_database_user_and_grant() -> None:
    calls = []
    responses = iter(
        (
            {"db_name": "sample_wp"},
            {"db_user": "sample_wp_user"},
            {"message": "ok"},
        )
    )

    def opener(request, *, timeout):
        calls.append(
            (
                request.method,
                request.full_url,
                request.headers["Authorization"],
                json.loads(request.data) if request.data else None,
                timeout,
            )
        )
        return Response(json.dumps(next(responses)).encode())

    client = XServerApiClient(
        api_key="xs_secret",
        servername="sample.xsrv.jp",
        opener=opener,
    )
    database = client.create_database("wp")
    user = client.create_database_user("wp_user", "Password123!")
    client.grant_database(user.db_user, database.db_name)

    assert database.db_name == "sample_wp"
    assert user.db_user == "sample_wp_user"
    assert calls[0][0:3] == (
        "POST",
        "https://api.xserver.ne.jp/v1/server/sample.xsrv.jp/db",
        "Bearer xs_secret",
    )
    assert calls[2][3] == {"db_name": "sample_wp"}
    assert "xs_secret" not in repr(calls[0][3])


def test_xserver_api_lists_existing_resources() -> None:
    responses = iter(
        (
            {"databases": [{"db_name": "sample_existing"}]},
            {"users": [{"db_user": "sample_user"}]},
        )
    )

    def opener(_request, *, timeout):
        assert timeout == 30.0
        return Response(json.dumps(next(responses)).encode())

    client = XServerApiClient(
        api_key="xs_secret",
        servername="sample.xsrv.jp",
        opener=opener,
    )
    assert client.list_databases() == ("sample_existing",)
    assert client.list_database_users() == ("sample_user",)


def test_default_urlopen_uses_certifi_bundle(monkeypatch) -> None:
    import certifi

    from dxsync import xserver_api

    captured: dict[str, object] = {}

    def fake_create_default_context(*, cafile=None):
        captured["cafile"] = cafile
        return "ssl-context"

    def fake_urlopen(request, *, timeout, context=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["context"] = context
        return Response(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(
        xserver_api.ssl, "create_default_context", fake_create_default_context
    )
    monkeypatch.setattr(xserver_api, "urlopen", fake_urlopen)

    client = xserver_api.XServerApiClient(
        api_key="xs_secret",
        servername="sample.xsrv.jp",
    )
    assert client.key_info() == {"ok": True}
    assert captured["cafile"] == certifi.where()
    assert captured["context"] == "ssl-context"
    assert captured["timeout"] == 30.0
