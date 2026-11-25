# v1.1.7

## 日本語

Docker → XServer Sync **v1.1.7** は、AI / MCP 連携の信頼性・可視性の大幅強化に加え、SFTP 転送の高速化、複数マッピングの一括同期、WordPress / Laravel メンテナンスモード自動連携などを追加するリリースです。

### 🤖 AI / MCP 連携の信頼性向上・進捗通知・スキル配置

- **配布版バイナリの実行可能チェック & macOS `.app` バンドル解決**: `os.X_OK` チェックと `Info.plist` (`CFBundleExecutable`) の厳密な解決により、内部共有ライブラリ誤登録による `Permission denied` を防止しました（`dxsync` CLI が利用可能な場合は CLI を優先登録します）。
- **登録前 MCP 起動テスト（自己診断）**: GUI の「登録 / 更新」時に `initialize` リクエストで実際に起動・応答を検証し、壊れた設定の書き込みを防止します（「起動テスト」ボタンも追加）。
- **`state.db` 自動検出・マイグレーション**: GUI と CLI/MCP の参照先ディレクトリを統一し、旧 1 重階層に保存されたプロジェクトも自動移行します。
- **MCP `notifications/progress` 進捗通知**: `_meta.progressToken` 付きの `dxsync_dry_run` / `dxsync_auto_sync` 実行中に、バックアップ・転送・ヘルスチェックの進捗をリアルタイム通知します。
- **スキル定義（`SKILL.md`）のワンクリック配置**: GUI の「AI / MCP 連携設定」から、MCP 設定と同時に Codex / Claude Code / Antigravity のスキルフォルダへ `SKILL.md` を自動配置できます。

### ⚡ 高速化・一括同期・メンテナンス連携・アップデート導線

- **SFTP 転送高速化 & 並列数設定**: バックアップ・アップロード時の親ディレクトリ確認キャッシュにより重複 `stat()` を削減し、自動実行設定・CLI（`--sftp-workers`）・MCP（`sftp_max_workers`）から SFTP 並列数（1〜16、既定 4）を設定可能にしました。
- **複数マッピングの一括ドライラン／一括安全同期**: GUI（「プロジェクト」メニュー）、CLI（`--all-mappings`）、MCP（`all_mappings: true`）から、Laravel などの複数マッピングを順番に一括実行できます。
- **WordPress / Laravel メンテナンスモード自動切替**: 自動実行設定・CLI（`--maintenance-mode`）・MCP（`auto_maintenance_mode: true`）で有効化すると、転送中に `.maintenance` または `php artisan down` を有効化し、完了後（および失敗時も `finally` で確実に）自動解除します。
- **OS 別推奨アップデートパッケージの直接誘導**: 「アップデートを確認…」で新バージョン検出時、現在の OS に最適なインストーラー／アーカイブを直接ダウンロードできるようになりました。

### 🧪 テスト

- MCP 進捗通知、スキル配置・削除、親ディレクトリ検証キャッシュ、並列数正規化、OS 別アセット選択、全マッピング一括実行、WordPress / Laravel メンテナンスモード、実行権限チェック、`state.db` マイグレーションのテストを追加し、全 **266件** のテストが合格しています。

---

## English

Docker → XServer Sync **v1.1.7** hardens AI / MCP integration, adds real-time MCP progress notifications and one-click skill installation, speeds up SFTP transfers, and introduces multi-mapping batch sync and automatic WordPress / Laravel maintenance mode.

### 🤖 AI / MCP Reliability, Progress & Skill Setup

- **Executable Validation & macOS `.app` Bundle Resolution**: Enforces `os.X_OK` checks and resolves `CFBundleExecutable` from `Info.plist` (preventing `Permission denied` errors), while preferring the `dxsync` CLI when installed.
- **Pre-Registration MCP Self-Test**: Verifies `initialize` JSON-RPC handshake before saving client configs, plus a dedicated **Launch Test** button in the dialog.
- **Automatic `state.db` Discovery & Migration**: Aligns CLI/MCP state directory with the GUI and automatically migrates legacy single-folder databases.
- **MCP `notifications/progress`**: Emits real-time progress notifications during `dxsync_dry_run` and `dxsync_auto_sync` when `_meta.progressToken` is provided.
- **One-Click `SKILL.md` Installation**: Installs `SKILL.md` alongside MCP registration for Codex, Claude Code, and Antigravity.

### ⚡ Speedups, Batch Sync, Maintenance Mode & Updates

- **SFTP Directory Verification Cache & Configurable Concurrency**: Eliminates redundant `stat()` round-trips during backup/deploy and exposes SFTP worker concurrency (`1-16`, default `4`) across GUI, CLI (`--sftp-workers`), and MCP (`sftp_max_workers`).
- **Multi-Mapping Batch Dry-Run & Auto-Sync**: Run dry-run or auto-sync across all configured mappings in order via GUI, CLI (`--all-mappings`), or MCP (`all_mappings: true`).
- **Automatic WordPress / Laravel Maintenance Mode**: Optionally toggles `.maintenance` or `php artisan down`/`up` around deployment with `finally` cleanup.
- **OS-Aware Update Downloads**: Update check now identifies and offers direct download of the recommended installer/archive for the current OS.

### 🧪 Tests

- Added tests covering MCP progress notifications, skill installation, `verified_dirs` caching, concurrency normalization, asset selection, batch mapping execution, WordPress/Laravel maintenance mode, executable checks, and `state.db` migration; all **266 unit tests** pass.

---

Release assets include Windows x64 portable and installer packages, a macOS Apple Silicon application archive, a Linux x64 archive, and `SHA256SUMS.txt`.
