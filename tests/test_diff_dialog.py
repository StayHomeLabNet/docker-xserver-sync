from __future__ import annotations

from PySide6.QtWidgets import QApplication

from dxsync.diff_dialog import DiffViewerDialog, build_text_diff


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_build_text_diff_renders_unified_diff_and_rejects_binary() -> None:
    is_text, diff = build_text_diff(
        "index.html",
        b"<h1>Hello</h1>\n<p>New</p>\n",
        b"<h1>Hello</h1>\n<p>Old</p>\n",
    )
    assert is_text
    assert "-<p>Old</p>" in diff
    assert "+<p>New</p>" in diff

    is_bin, bin_msg = build_text_diff("logo.png", b"\x89PNG\x00\x01", b"\x89PNG\x00\x02")
    assert not is_bin
    assert "バイナリ" in bin_msg


def test_diff_viewer_dialog_shows_diff_text() -> None:
    app()
    dialog = DiffViewerDialog(
        relative_path="app.css",
        diff_text="-body { color: red; }\n+body { color: blue; }\n",
        local_summary="24 bytes",
        remote_summary="23 bytes",
    )
    assert "app.css" in dialog.windowTitle()
    assert "+body { color: blue; }" in dialog.editor.toPlainText()
