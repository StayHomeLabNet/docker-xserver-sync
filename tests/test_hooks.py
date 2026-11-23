from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from dxsync.auto_sync import AutoSyncPreferences, AutoSyncSettingsDialog
from dxsync.hooks import (
    filter_allowed_delete_paths,
    normalize_allowed_delete_folders,
    run_post_sync_remote_command,
    run_pre_sync_command,
    send_post_sync_webhook,
)


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _WebhookResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_normalize_and_filter_allowed_delete_folders() -> None:
    folders = normalize_allowed_delete_folders("assets\n/_astro/\n../bad\nassets/")
    assert folders == ("assets/", "_astro/")

    filtered = filter_allowed_delete_paths(
        ("assets/app-123.js", "_astro/chunk.css", "index.html", "uploads/a.jpg"),
        folders,
    )
    assert filtered == ("assets/app-123.js", "_astro/chunk.css")


def test_run_pre_sync_command_succeeds_and_reports_failure(tmp_path: Path) -> None:
    ok = run_pre_sync_command(f'"{sys.executable}" -c "print(\'built\')"', tmp_path)
    assert ok.ok
    assert "built" in ok.stdout

    fail = run_pre_sync_command(
        f'"{sys.executable}" -c "import sys; sys.stderr.write(\'boom\'); sys.exit(2)"',
        tmp_path,
    )
    assert not fail.ok
    assert fail.returncode == 2
    assert "boom" in fail.error


def test_run_post_sync_remote_command_uses_quoted_remote_root() -> None:
    executed: list[str] = []

    class FakeSSH:
        def exec_command(self, command: str, timeout: float = 120.0):
            executed.append(command)
            stdout = SimpleNamespace(
                read=lambda: b"cleared\n",
                channel=SimpleNamespace(recv_exit_status=lambda: 0),
            )
            stderr = SimpleNamespace(read=lambda: b"")
            return None, stdout, stderr

    result = run_post_sync_remote_command(
        FakeSSH(),
        "php artisan config:cache",
        "/home/user/example app/public_html",
    )
    assert result.ok
    assert "cd '/home/user/example app/public_html' && php artisan config:cache" in executed[0]


def test_send_post_sync_webhook_validates_https_and_posts_json() -> None:
    bad = send_post_sync_webhook(
        "http://insecure.example/hook",
        project_name="MySite",
        domain="example.com",
        status="succeeded",
        summary="done",
    )
    assert not bad.ok

    captured: dict[str, object] = {}

    def fake_opener(request, *, timeout: float):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _WebhookResponse(b"ok")

    good = send_post_sync_webhook(
        "https://hooks.slack.com/services/T000/B000/XXX",
        project_name="MySite",
        domain="example.com",
        status="succeeded",
        summary="3 files",
        opener=fake_opener,
    )
    assert good.ok
    assert captured["url"] == "https://hooks.slack.com/services/T000/B000/XXX"
    assert "MySite (example.com)" in captured["body"]["text"]


def test_auto_sync_settings_dialog_round_trips_hooks_and_allowed_folders() -> None:
    app()
    prefs = AutoSyncPreferences(
        skip_approval_dialog=True,
        pre_sync_command="npm run build",
        post_sync_remote_command="php artisan cache:clear",
        post_sync_webhook_url="https://hooks.example.com/123",
        auto_delete_allowed_folders=("assets/", "_astro/"),
    )
    dialog = AutoSyncSettingsDialog(prefs)
    result = dialog.selected_preferences()
    assert result == prefs
