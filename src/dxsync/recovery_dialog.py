from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .recovery import RecoveryAnalysisResult


class RecoveryAnalysisDialog(QDialog):
    def __init__(self, result: RecoveryAnalysisResult, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("残存一時ファイルの復旧確認")
        self.resize(1100, 560)
        layout = QVBoxLayout(self)
        note = QLabel(
            "一時ファイルと本来の公開先をSHA-256で比較しました。"
            "この画面は判定と推奨手順を表示するだけで、削除・復帰・公開切り替えは実行しません。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(len(result.artifacts), 7)
        table.setHorizontalHeaderLabels(
            ("一時ファイル", "本来の公開先", "種別", "サイズ", "判定", "推奨", "SHA-256")
        )
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        for row, artifact in enumerate(result.artifacts):
            values = (
                artifact.temporary_path,
                artifact.destination_path,
                artifact.kind.upper(),
                f"{artifact.temporary_size:,} bytes",
                artifact.assessment,
                artifact.recommendation,
                artifact.temporary_sha256,
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        layout.addWidget(table, 1)
        if result.issues:
            warning = QLabel("未確認項目:\n" + "\n".join(result.issues))
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #b42318;")
            layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
