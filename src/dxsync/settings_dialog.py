from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from .planner import (
    DEFAULT_PLAN_VALIDITY_MINUTES,
    MAX_PLAN_VALIDITY_MINUTES,
    MIN_PLAN_VALIDITY_MINUTES,
    normalize_plan_validity_minutes,
)


MASK_FIELDS = (
    ("host", "SSHホスト"),
    ("username", "SSHユーザー"),
    ("remote_root", "リモートルート"),
    ("fingerprint", "接続先の確認コード"),
    ("private_key", "SSH秘密鍵のパス"),
)

PLAN_VALIDITY_SETTING_KEY = "plan/validity_minutes"
PLAN_VALIDITY_PRESETS = (5, 10, 15, 30, 45, 60)


def load_plan_validity_minutes(settings: QSettings) -> int:
    raw = settings.value(PLAN_VALIDITY_SETTING_KEY, DEFAULT_PLAN_VALIDITY_MINUTES)
    return normalize_plan_validity_minutes(raw)


def save_plan_validity_minutes(settings: QSettings, minutes: int) -> int:
    normalized = normalize_plan_validity_minutes(minutes)
    settings.setValue(PLAN_VALIDITY_SETTING_KEY, normalized)
    settings.sync()
    return normalized


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


class PlanValiditySettingsDialog(QDialog):
    def __init__(self, current_minutes: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("計画の有効時間設定")
        self.resize(460, 230)
        normalized = normalize_plan_validity_minutes(current_minutes)

        layout = QVBoxLayout(self)
        note = QLabel(
            "ドライランで作成した同期計画の有効時間を選択または変更します。"
            f"（既定: {DEFAULT_PLAN_VALIDITY_MINUTES}分 / 最大: {MAX_PLAN_VALIDITY_MINUTES}分）"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self.preset_combo = QComboBox()
        for minutes in PLAN_VALIDITY_PRESETS:
            if minutes == DEFAULT_PLAN_VALIDITY_MINUTES:
                label = f"{minutes}分（既定）"
            elif minutes == MAX_PLAN_VALIDITY_MINUTES:
                label = f"{minutes}分（最大）"
            else:
                label = f"{minutes}分"
            self.preset_combo.addItem(label, minutes)
        self.preset_combo.addItem("カスタム（1〜60分）", None)

        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(
            MIN_PLAN_VALIDITY_MINUTES,
            MAX_PLAN_VALIDITY_MINUTES,
        )
        self.minutes_spin.setSuffix(" 分")
        self.minutes_spin.setValue(normalized)

        form.addRow("プリセットから選択", self.preset_combo)
        form.addRow("有効時間（1〜60分）", self.minutes_spin)
        layout.addLayout(form)

        safety_note = QLabel(
            "安全のため有効時間の上限は60分です。"
            "有効時間内であっても、承認前にはローカル・リモート・設定の再検証が行われます。"
        )
        safety_note.setWordWrap(True)
        layout.addWidget(safety_note)

        self._sync_combo_to_minutes(normalized)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.minutes_spin.valueChanged.connect(self._on_spin_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.RestoreDefaults
            | QDialogButtonBox.Save
            | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.RestoreDefaults).setText(
            f"既定値に戻す（{DEFAULT_PLAN_VALIDITY_MINUTES}分）"
        )
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            self._restore_defaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _sync_combo_to_minutes(self, minutes: int) -> None:
        index = self.preset_combo.findData(minutes)
        if index < 0:
            index = self.preset_combo.findData(None)
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(max(index, 0))
        self.preset_combo.blockSignals(False)

    def _on_preset_changed(self, _index: int) -> None:
        data = self.preset_combo.currentData()
        if isinstance(data, int):
            self.minutes_spin.blockSignals(True)
            self.minutes_spin.setValue(data)
            self.minutes_spin.blockSignals(False)

    def _on_spin_changed(self, value: int) -> None:
        self._sync_combo_to_minutes(value)

    def _restore_defaults(self) -> None:
        self.minutes_spin.setValue(DEFAULT_PLAN_VALIDITY_MINUTES)

    def selected_minutes(self) -> int:
        return normalize_plan_validity_minutes(self.minutes_spin.value())
