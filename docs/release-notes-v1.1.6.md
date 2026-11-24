# v1.1.6

## 日本語

Docker → XServer Sync **v1.1.6** は、**Codex・Claude Code・Antigravity** などの AI エージェントから本ツールをすぐに使い始められるようにする **AI / MCP 連携強化リリース** です。

### 🤖 AI / MCP ワンクリック連携（Codex / Claude Code / Antigravity）

- **GUI からのワンクリック登録（「設定」→「AI / MCP 連携設定（Codex / Claude / Antigravity）…」）**:
  - **Codex**（`codex mcp add` / `~/.codex/config.toml`）
  - **Claude Code**（`claude mcp add -s user` / `~/.claude.json`）
  - **Antigravity**（`agy mcp add` / `~/.gemini/antigravity/mcp_config.json`）
  の登録状態を一画面で確認し、**「登録 / 更新」「解除」** ボタンで即時に連携設定できます。
- **配布版 GUI バイナリ単体での `mcp` 直接起動に対応**:
  - macOS `.app` / Windows `.exe` / Linux バイナリに引数 `mcp` を渡すだけで stdio MCP サーバーとして起動するため、Python や `uv` 環境がないユーザーでもそのまま AI 連携を利用できます。
- **コマンド・設定・指示例のワンクリックコピー**:
  - 各 CLI コマンド、Codex TOML、JSON 設定、および選択中プロジェクト名入りの AI 指示例を 1 クリックでコピーできます。

### 🧪 テスト

- MCP 起動コマンド自動検出、Codex / Claude Code / Antigravity 登録・解除・設定ファイル保持、および GUI バイナリの `mcp` ディスパッチのユニットテストを追加し、全 **256件** のテストが合格しています。

---

## English

Docker → XServer Sync **v1.1.6** focuses on frictionless **AI / MCP integration**, making it effortless to connect **Codex, Claude Code, and Antigravity** directly to your saved projects.

### 🤖 One-Click AI / MCP Integration (Codex / Claude Code / Antigravity)

- **Built-in GUI Registration Dialog (`Settings → AI / MCP Integration (Codex / Claude / Antigravity)…`)**:
  - Check live registration status and click **Register / Update** or **Unregister** for **Codex** (`codex mcp add` / `~/.codex/config.toml`), **Claude Code** (`claude mcp add -s user` / `~/.claude.json`), and **Antigravity** (`agy mcp add` / `~/.gemini/antigravity/mcp_config.json`).
- **Packaged GUI Executable Supports `mcp` Directly**:
  - Passing `mcp` as the first argument to the packaged macOS `.app`, Windows `.exe`, or Linux binary launches the built-in stdio MCP server directly—no separate Python or `uv` installation required.
- **One-Click Copy for Configs & Prompts**:
  - Copy ready-to-run CLI commands, Codex TOML, JSON configs, and project-aware prompt examples directly from the dialog.

### 🧪 Tests

- Added unit tests for launch command detection, Codex / Claude Code / Antigravity registration and config preservation, and GUI `mcp` argument dispatch; all **256 unit tests** pass.

---

Release assets include Windows x64 portable and installer packages, a macOS Apple Silicon application archive, a Linux x64 archive, and `SHA256SUMS.txt`.
