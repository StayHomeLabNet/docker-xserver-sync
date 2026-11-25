from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .mcp_setup import (
    MCP_CLIENTS,
    ClientId,
    McpLaunchSpec,
    detect_mcp_launch_spec,
    format_client_cli_command,
    format_codex_toml,
    format_mcp_json,
    get_client_mcp_status,
    get_client_skill_status,
    install_client_skill,
    register_client_mcp,
    remove_client_skill,
    unregister_client_mcp,
    verify_mcp_launch_spec,
)


class McpIntegrationDialog(QDialog):
    def __init__(self, project_label: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI / MCP 連携設定（Codex / Claude Code / Antigravity）")
        self.resize(680, 560)

        self.spec: McpLaunchSpec = detect_mcp_launch_spec()
        target_name = project_label or "example.com"

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Docker XServer Sync には組み込み MCP サーバー（dxsync mcp）が搭載されています。\n"
            "Codex / Claude Code / Antigravity などに登録すると、AI へ話しかけるだけで"
            "ドライラン・4段階安全同期・バックアップ確認を実行できます。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        clients_group = QGroupBox("AI エージェントへのワンクリック登録")
        clients_grid = QGridLayout(clients_group)
        clients_grid.setHorizontalSpacing(10)
        clients_grid.setVerticalSpacing(8)
        clients_grid.setColumnStretch(1, 1)

        self._status_labels: dict[ClientId, QLabel] = {}
        self._unregister_buttons: dict[ClientId, QPushButton] = {}

        for row, (client_id, client) in enumerate(MCP_CLIENTS.items()):
            name_label = QLabel(f"<b>{client.display_name}</b>")
            status_label = QLabel()
            status_label.setWordWrap(True)
            register_btn = QPushButton("登録 / 更新")
            register_btn.clicked.connect(
                lambda _checked=False, cid=client_id: self._on_register_client(cid)
            )
            unregister_btn = QPushButton("解除")
            unregister_btn.clicked.connect(
                lambda _checked=False, cid=client_id: self._on_unregister_client(cid)
            )
            clients_grid.addWidget(name_label, row, 0)
            clients_grid.addWidget(status_label, row, 1)
            clients_grid.addWidget(register_btn, row, 2)
            clients_grid.addWidget(unregister_btn, row, 3)

            self._status_labels[client_id] = status_label
            self._unregister_buttons[client_id] = unregister_btn

        self.install_skill_check = QCheckBox(
            "スキル定義 (SKILL.md) も同時に配置する（安全運用ルールを自動適用・推奨）"
        )
        self.install_skill_check.setChecked(True)
        clients_grid.addWidget(self.install_skill_check, len(MCP_CLIENTS), 0, 1, 4)

        layout.addWidget(clients_group)

        config_tabs = QTabWidget()
        config_tabs.addTab(
            self._build_copy_tab(
                "Codex CLI で登録するコマンド:",
                format_client_cli_command("codex", self.spec),
                "コマンドをコピー",
            ),
            "Codex CLI",
        )
        config_tabs.addTab(
            self._build_copy_tab(
                "Claude Code CLI で登録するコマンド:",
                format_client_cli_command("claude_code", self.spec),
                "コマンドをコピー",
            ),
            "Claude Code CLI",
        )
        config_tabs.addTab(
            self._build_copy_tab(
                "Antigravity CLI (agy) で登録するコマンド:",
                format_client_cli_command("antigravity", self.spec),
                "コマンドをコピー",
            ),
            "Antigravity CLI",
        )
        config_tabs.addTab(
            self._build_copy_tab(
                "~/.codex/config.toml に追記する設定:",
                format_codex_toml(self.spec),
                "TOML をコピー",
            ),
            "Codex TOML",
        )
        config_tabs.addTab(
            self._build_copy_tab(
                "Claude Code / Antigravity / Claude Desktop / Cursor 用 JSON:",
                format_mcp_json(self.spec),
                "JSON をコピー",
            ),
            "JSON 設定",
        )
        layout.addWidget(config_tabs)

        prompt_group = QGroupBox("AI への指示例")
        prompt_layout = QVBoxLayout(prompt_group)
        self.prompt_example = (
            f"「{target_name}」プロジェクトをドライランして、"
            "問題なければ安全同期（auto_sync）を実行して"
        )
        prompt_text = QPlainTextEdit(self.prompt_example)
        prompt_text.setReadOnly(True)
        prompt_text.setMaximumHeight(56)
        prompt_layout.addWidget(prompt_text)

        prompt_btn_row = QHBoxLayout()
        copy_prompt_btn = QPushButton("指示例をコピー")
        copy_prompt_btn.clicked.connect(
            lambda: self._copy_text(self.prompt_example, "指示例をコピーしました。")
        )
        prompt_btn_row.addWidget(copy_prompt_btn)
        prompt_btn_row.addStretch(1)
        prompt_layout.addLayout(prompt_btn_row)
        layout.addWidget(prompt_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.verify_button = QPushButton("起動テスト")
        self.verify_button.clicked.connect(self._on_verify_mcp)
        buttons.addButton(self.verify_button, QDialogButtonBox.ActionRole)
        buttons.button(QDialogButtonBox.Close).setText("閉じる")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_status()

    def _build_copy_tab(self, label_text: str, content: str, button_text: str) -> QWidget:
        widget = QWidget()
        tab_layout = QVBoxLayout(widget)
        tab_layout.addWidget(QLabel(label_text))
        editor = QPlainTextEdit(content)
        editor.setReadOnly(True)
        editor.setMaximumHeight(110)
        tab_layout.addWidget(editor)
        row = QHBoxLayout()
        btn = QPushButton(button_text)
        btn.clicked.connect(lambda: self._copy_text(content, f"{button_text}しました。"))
        row.addWidget(btn)
        row.addStretch(1)
        tab_layout.addLayout(row)
        return widget

    def _copy_text(self, text: str, message: str) -> None:
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "コピー完了", message)

    def _refresh_status(self) -> None:
        for client_id in MCP_CLIENTS:
            registered, detail = get_client_mcp_status(client_id)
            skill_installed = get_client_skill_status(client_id)
            skill_suffix = " / スキル配置済み" if skill_installed else ""
            self._status_labels[client_id].setText(f"{detail}{skill_suffix}")
            self._unregister_buttons[client_id].setEnabled(registered or skill_installed)

    def _on_verify_mcp(self) -> None:
        ok, message = verify_mcp_launch_spec(self.spec)
        if ok:
            QMessageBox.information(
                self,
                "MCP 起動テスト成功",
                f"{message}\n\nコマンド: {self.spec.command} {' '.join(self.spec.args)}",
            )
        else:
            QMessageBox.warning(self, "MCP 起動テスト失敗", message)

    def _on_register_client(self, client_id: ClientId) -> None:
        client = MCP_CLIENTS[client_id]
        verify_ok, verify_msg = verify_mcp_launch_spec(self.spec)
        if not verify_ok:
            QMessageBox.warning(
                self,
                f"{client.display_name} 連携エラー（起動テスト失敗）",
                f"登録前の MCP サーバー自己診断テストに失敗したため登録を中止しました。\n\n{verify_msg}",
            )
            return
        ok, message = register_client_mcp(client_id, self.spec)
        skill_note = ""
        if ok and self.install_skill_check.isChecked():
            skill_ok, skill_msg = install_client_skill(client_id)
            skill_note = f"\n{skill_msg}" if skill_ok else f"\n※ スキル配置エラー: {skill_msg}"
        self._refresh_status()
        if ok:
            QMessageBox.information(
                self,
                f"{client.display_name} 連携完了",
                f"{message}\n{verify_msg}{skill_note}\n\n"
                f"{client.display_name} を再起動（または新しいセッションを開始）すると、"
                "すぐに利用できます。\n"
                "※ 対象プロジェクトがまだ未保存の場合は、先に GUI で保存してください。",
            )
        else:
            QMessageBox.warning(self, f"{client.display_name} 連携エラー", message)

    def _on_unregister_client(self, client_id: ClientId) -> None:
        client = MCP_CLIENTS[client_id]
        ok, message = unregister_client_mcp(client_id)
        skill_ok, skill_msg = remove_client_skill(client_id)
        self._refresh_status()
        if ok and skill_ok:
            QMessageBox.information(self, f"{client.display_name} 連携解除", f"{message}\n{skill_msg}")
        else:
            QMessageBox.warning(self, f"{client.display_name} 連携エラー", f"{message}\n{skill_msg}")
