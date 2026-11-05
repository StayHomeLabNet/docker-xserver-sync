from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)


MASK_FIELDS = (
    ("host", "SSHホスト"),
    ("username", "SSHユーザー"),
    ("remote_root", "リモートルート"),
    ("fingerprint", "接続先の確認コード"),
    ("private_key", "SSH秘密鍵のパス"),
)


class MaskSettingsDialog(QDialog):
    def __init__(self, values: dict[str, bool], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("接続情報のマスク設定")
        layout = QVBoxLayout(self)
        note = QLabel(
            "アプリ起動時に伏せ字で表示する項目を選択します。"
            "設定には値そのものではなく、マスクの有無だけを保存します。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.checkboxes: dict[str, QCheckBox] = {}
        for key, label in MASK_FIELDS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(values.get(key, True))
            self.checkboxes[key] = checkbox
            layout.addWidget(checkbox)
        passphrase = QLabel(
            "秘密鍵のパスフレーズは保存せず、常に起動時マスクの対象です。"
        )
        passphrase.setWordWrap(True)
        layout.addWidget(passphrase)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_values(self) -> dict[str, bool]:
        return {key: checkbox.isChecked() for key, checkbox in self.checkboxes.items()}
