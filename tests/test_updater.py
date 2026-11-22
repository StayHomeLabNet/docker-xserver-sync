import io
import json
import os
import urllib.error
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from dxsync.gui import MainWindow
from dxsync.secure_store import MemoryHostKeyStore
from dxsync.storage import ProjectStore
from dxsync.updater import (
    GITHUB_RELEASES_PAGE,
    UpdateCheckError,
    UpdateCheckResult,
    check_for_updates,
    is_newer_version,
    parse_semver,
)


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _DummyResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_parse_semver_and_compare() -> None:
    assert parse_semver("1.0.0") == ((1, 0, 0), 1, "")
    assert parse_semver("v1.2.3") == ((1, 2, 3), 1, "")
    assert is_newer_version("1.0.0", "v1.1.0")
    assert is_newer_version("1.1.0-rc1", "1.1.0")
    assert not is_newer_version("1.1.0", "1.1.0")
    assert not is_newer_version("1.2.0", "1.1.9")

    with pytest.raises(UpdateCheckError):
        parse_semver("invalid")


def test_check_for_updates_detects_new_release() -> None:
    body = json.dumps(
        {
            "tag_name": "v1.1.0",
            "name": "v1.1.0 Release",
            "html_url": "https://github.com/StayHomeLabNet/docker-xserver-sync/releases/tag/v1.1.0",
            "published_at": "2026-11-19T12:00:00Z",
            "body": "Added new features.",
        }
    ).encode("utf-8")
    result = check_for_updates(
        "1.0.0",
        urlopen_func=lambda _req, timeout=10.0: _DummyResponse(body),
    )

    assert result.update_available is True
    assert result.current_version == "1.0.0"
    assert result.latest_version == "1.1.0"
    assert result.release_url.endswith("/v1.1.0")
    assert "Added new features" in result.notes_excerpt


def test_check_for_updates_handles_up_to_date_and_sanitizes_url() -> None:
    body = json.dumps(
        {
            "tag_name": "v1.1.0",
            "html_url": "http://insecure.example.com/release",
        }
    ).encode("utf-8")
    result = check_for_updates(
        "1.1.0",
        urlopen_func=lambda _req, timeout=10.0: _DummyResponse(body),
    )

    assert result.update_available is False
    assert result.release_url == GITHUB_RELEASES_PAGE


def test_check_for_updates_uses_bundled_ca_certificates(monkeypatch) -> None:
    body = json.dumps(
        {
            "tag_name": "v1.1.3",
            "html_url": "https://github.com/StayHomeLabNet/docker-xserver-sync/releases/tag/v1.1.3",
        }
    ).encode("utf-8")
    ssl_context = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr("dxsync.updater.certifi.where", lambda: "/bundle/cacert.pem")
    def fake_create_default_context(*, cafile):
        captured["cafile"] = cafile
        return ssl_context

    monkeypatch.setattr(
        "dxsync.updater.ssl.create_default_context",
        fake_create_default_context,
    )

    def fake_urlopen(request, *, timeout, context):
        captured["request"] = request
        captured["timeout"] = timeout
        captured["context"] = context
        return _DummyResponse(body)

    monkeypatch.setattr("dxsync.updater.urllib.request.urlopen", fake_urlopen)

    result = check_for_updates("1.1.3")

    assert not result.update_available
    assert captured["cafile"] == "/bundle/cacert.pem"
    assert captured["context"] is ssl_context


def test_check_for_updates_raises_clear_error_on_http_or_json_failure() -> None:
    def fail_http(_req, timeout=10.0):
        raise urllib.error.HTTPError(
            "https://api.github.com/test",
            403,
            "rate limited",
            {},
            io.BytesIO(b""),
        )

    with pytest.raises(UpdateCheckError, match="HTTP 403"):
        check_for_updates("1.0.0", urlopen_func=fail_http)

    with pytest.raises(UpdateCheckError, match="JSON"):
        check_for_updates(
            "1.0.0",
            urlopen_func=lambda _req, timeout=10.0: _DummyResponse(b"not json"),
        )


def test_gui_update_check_menu_opens_release_url_when_accepted(
    tmp_path: Path, monkeypatch
) -> None:
    app()
    window = MainWindow(
        ProjectStore(tmp_path / "state.db", MemoryHostKeyStore()),
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
    )
    assert window.check_updates_action.text() == "アップデートを確認…"

    opened_urls: list[str] = []
    monkeypatch.setattr(
        "dxsync.gui.QDesktopServices.openUrl",
        lambda url: opened_urls.append(url.toString()) or True,
    )
    monkeypatch.setattr(
        "dxsync.gui.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )

    window._update_check_completed(
        UpdateCheckResult(
            current_version="1.0.0",
            latest_version="1.1.0",
            update_available=True,
            release_url="https://github.com/StayHomeLabNet/docker-xserver-sync/releases/tag/v1.1.0",
        )
    )
    assert opened_urls == [
        "https://github.com/StayHomeLabNet/docker-xserver-sync/releases/tag/v1.1.0"
    ]
    window.close()
