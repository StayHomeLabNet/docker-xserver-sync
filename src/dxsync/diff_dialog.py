from __future__ import annotations

import difflib

from PySide6.QtGui import QColor, QFontDatabase, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

MAX_DIFF_BYTES = 1_048_576


def build_text_diff(
    relative_path: str,
    local_bytes: bytes,
    remote_bytes: bytes,
    *,
    max_bytes: int = MAX_DIFF_BYTES,
) -> tuple[bool, str]:
    if len(local_bytes) > max_bytes or len(remote_bytes) > max_bytes:
        return (
            False,
            f"ファイルサイズが上限（{max_bytes // 1024} KiB）を超えているためテキスト差分を表示できません。\n"
            f"ローカル: {len(local_bytes):,} bytes / XServer: {len(remote_bytes):,} bytes",
        )
    if b"\x00" in local_bytes or b"\x00" in remote_bytes:
        return (
            False,
            "バイナリファイルのためテキスト差分を表示できません。\n"
            f"ローカル: {len(local_bytes):,} bytes / XServer: {len(remote_bytes):,} bytes",
        )
    try:
        local_text = local_bytes.decode("utf-8")
        remote_text = remote_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return (
            False,
            "UTF-8 テキストとして読み取れないため差分を表示できません。\n"
            f"ローカル: {len(local_bytes):,} bytes / XServer: {len(remote_bytes):,} bytes",
        )

    diff_lines = list(
        difflib.unified_diff(
            remote_text.splitlines(keepends=True),
            local_text.splitlines(keepends=True),
            fromfile=f"XServer/{relative_path}",
            tofile=f"Local/{relative_path}",
            n=3,
        )
    )
    if not diff_lines:
        return True, "ファイル内容は一致しています。"
    return True, "".join(diff_lines)


class _DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self._added = QTextCharFormat()
        self._added.setForeground(QColor("#2da44e"))
        self._removed = QTextCharFormat()
        self._removed.setForeground(QColor("#cf222e"))
        self._hunk = QTextCharFormat()
        self._hunk.setForeground(QColor("#0969da"))

    def highlightBlock(self, text: str) -> None:
        if text.startswith("+") and not text.startswith("+++"):
            self.setFormat(0, len(text), self._added)
        elif text.startswith("-") and not text.startswith("---"):
            self.setFormat(0, len(text), self._removed)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self._hunk)


class DiffViewerDialog(QDialog):
    def __init__(
        self,
        *,
        relative_path: str,
        diff_text: str,
        local_summary: str,
        remote_summary: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"差分プレビュー — {relative_path}")
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        header = QLabel(
            f"対象: {relative_path}\nローカル: {local_summary} / XServer: {remote_summary}"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.editor.setPlainText(diff_text)
        self._highlighter = _DiffHighlighter(self.editor.document())
        layout.addWidget(self.editor, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
