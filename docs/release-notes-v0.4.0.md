# v0.4.0

## 日本語

### 🤖 AI / MCP (Model Context Protocol) 対応
- **ゼロ依存組み込み MCP サーバー（`dxsync mcp`）の実装**:
  - Claude Desktop、Cursor、Codex、Antigravity CLI / IDE などの AI エージェントから、標準入出力（stdio）経由で Docker XServer Sync を直接・安全に操作できるようになりました。
  - 公開ツール:
    - `dxsync_list_projects`: 登録済みプロジェクト一覧の取得
    - `dxsync_get_project`: プロジェクト設定、パスマッピング、セキュリティ状態の確認
    - `dxsync_dry_run`: 読み取り専用の安全な差分プレビュー
    - `dxsync_auto_sync`: 4段階パイプラインによる一発安全自動同期
    - `dxsync_verify_safety`: 残留一時ファイル（`.new`）の検査と自動クリーンアップ
    - `dxsync_list_backups`: サーバー側バックアップ履歴の参照
    - `dxsync_restore_backup`: 指定バックアップからの安全ロールバック

### 💻 CLI（コマンドラインインターフェース）の全面拡充
- スクリプト連携、CI/CD パイプライン、AI 実行に最適化されたサブコマンド体系を追加しました。
- 全コマンドで `--json` フラグをサポートし、構造化された JSON 出力を取得可能です。
  - `dxsync project list [--json]` / `dxsync project show <project> [--json]`
  - `dxsync run dry-run <project> [--mapping <name>] [--json]`
  - `dxsync run auto-sync <project> [--mapping <name>] [--yes] [--json]`
  - `dxsync run verify <project> [--cleanup] [--json]`
  - `dxsync backup list <project> [--json]`
  - `dxsync backup restore <project> --backup-id <id> [--yes] [--json]`
  - `dxsync mcp`

### 🛡️ 4段階安全パイプラインの完全保証
- AI や CLI から「一発自動同期」を実行する場合でも、内部で GUI と全く同一の堅牢なパイプライン（ドライラン差分走査 → 計画の再検証 → サーバー側バックアップ作成 → 一時ファイル `.new` を用いたアトミック転送 → HTTPS 疎通確認）が自動実行され、本番環境の破壊を防ぎます。
- SSH 秘密鍵や接続先確認コード（ホスト鍵フィンガープリント）などの機密情報は、従来通り GUI / OS 資格情報ストア（Keychain / Windows 資格情報マネージャー）で安全に隔離・保管されます。

### 📚 AI スキル定義およびマニュアルの追加
- Antigravity 向けスキル定義ファイル（`skills/docker-xserver-sync/SKILL.md` および `.gemini/skills/docker-xserver-sync/SKILL.md`）を同梱。
- 詳細マニュアル（`docs/manual.md` / `docs/manual.en.md`）に「11. CLIモード」「12. AI / MCP 連携と自動化」を追加しました。

---

## English

### 🤖 AI & MCP (Model Context Protocol) Integration
- **Zero-Dependency Built-in MCP Server (`dxsync mcp`)**:
  - Direct, safe remote operation via stdio for AI assistants including Claude Desktop, Cursor, Codex, and Antigravity CLI / IDE.
  - Exposed tools:
    - `dxsync_list_projects`: List all configured projects
    - `dxsync_get_project`: Retrieve project details, path mappings, and security states
    - `dxsync_dry_run`: Execute read-only difference preview without remote side-effects
    - `dxsync_auto_sync`: Run full 4-stage automated synchronization
    - `dxsync_verify_safety`: Inspect and clean up residual `.new` temporary files
    - `dxsync_list_backups`: Query remote backup records
    - `dxsync_restore_backup`: Safe rollback from a backup snapshot

### 💻 Comprehensive CLI Subcommand Suite
- Added rich subcommands with optional `--json` output, ideal for automation, CI/CD, and AI agents:
  - `dxsync project list [--json]` / `dxsync project show <project> [--json]`
  - `dxsync run dry-run <project> [--mapping <name>] [--json]`
  - `dxsync run auto-sync <project> [--mapping <name>] [--yes] [--json]`
  - `dxsync run verify <project> [--cleanup] [--json]`
  - `dxsync backup list <project> [--json]`
  - `dxsync backup restore <project> --backup-id <id> [--yes] [--json]`
  - `dxsync mcp`

### 🛡️ Enforced 4-Stage Safety Pipeline
- Automated sync execution from AI/CLI adheres to the exact same 4-stage pipeline as the GUI: Dry-Run Scan -> Plan Revalidation -> Automated Remote Backup -> Atomic Deployment via `.new` files -> HTTPS Health Check.
- SSH keys and credentials remain safely managed in OS secure credential stores.

### 📚 AI Skill Definition & Documentation
- Included official Antigravity skill definitions (`skills/docker-xserver-sync/SKILL.md` and `.gemini/skills/docker-xserver-sync/SKILL.md`).
- Expanded user manuals (`docs/manual.md` / `docs/manual.en.md`) with comprehensive CLI and AI/MCP chapters.
