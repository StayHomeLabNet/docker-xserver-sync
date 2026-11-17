# v1.0.0

## 日本語

Docker → XServer Sync の初の正式メジャーリリース **v1.0.0** です。静的サイト専用モードの追加、計画有効時間のカスタマイズ、全操作にわたる進捗・ETA 表示の充実、CLI / MCP パイプラインの信頼性・セキュリティ修正を含みます。

### 🌐 静的サイト（Static Site）モードの追加
- **専用プリセット `静的サイト（HTML / ビルド済み）` を追加**:
  - 静的 HTML/CSS/JS や Astro / Next.js（`out/`）/ Hugo / Vite などのビルド済み成果物をそのまま安全に同期できます。
  - PHP サイト向けの自動保護パスや確認チェックを強制せず、ルート配下の公開フォルダ（`/docs/`、`/test/`、`/README*`、`/LICENSE*` など）や `.htaccess` も正しく同期対象になります（`.git/`、`.env`、`node_modules/`、`docker-compose*.yml` 等の開発・機密ファイルは引き続き除外）。
  - 必要に応じてプロジェクト編集画面の「保護パスを設定…」からサーバー側で残したいディレクトリを任意指定できます。
- **削除候補の一括選択 / 一括解除**:
  - ハッシュ付きビルド成果物（`assets/index-xxxx.js` 等）の更新時に便利な **「削除候補をすべて選択」** / **「削除候補をすべて解除」** ボタンを承認ダイアログへ追加しました（「削除しないフォルダー」に指定したフォルダは保護されます）。

### ⏱️ 計画の有効時間を設定可能に（1〜60分）
- 読み取り専用ドライランで作成される計画の有効時間（既定 15 分）を、メニューの **「設定」→「計画の有効時間設定…」** から **1〜60分** の範囲で選択・保存できるようになりました（CLI `--validity-minutes` にも対応）。

### 📊 詳細な進捗・経過時間・推定残り時間（ETA）表示の拡充
- 静的サイトモードを含む **安全同期の自動実行**、**計画の再検証**、**読み取り専用ドライラン**、**バックアップ作成**、**承認付き同期**、**復元**、**安全性確認**、**初回セットアップ** のすべてで、ファイル数・パーセンテージ・経過時間・推定残り時間（ETA）・現在処理中のパスがリアルタイムに表示されるようになりました。

### 🔒 プロジェクト保護と操作安全性の強化
- **保存済みプロジェクトの主要項目ロック**: 編集時の誤操作を防ぐため、プロジェクト編集ダイアログでサイト種別・ドメイン・ローカルルート・リモートルート・接続情報を既定で保護し、意図的な変更時のみロック解除する設計にしました。
- **静的サイト自動実行・再検証の修正**: 静的サイトモードでの自動実行時に計画再検証後からバックアップ作成・承認付き同期へ正しく進行するよう修正しました。
- **CLI / MCP パイプラインの修正・堅牢化**:
  - `dxsync dry-run` / `dxsync auto-sync` / `dxsync_auto_sync` で設定ハッシュ（`config_sha256`）を保存・検証するようにし、計画再検証が正しく通過するよう修正しました。
  - `auto-sync` の HTTPS ヘルスチェックおよび操作ステータス記録の不具合を修正しました。
  - `execute_verify(cleanup=True)` の削除失敗時エラー記録と、`backup restore` / `dxsync_restore_backup` のハッシュ検証・ステータス更新（`restored`）を修正しました。
  - CLI / MCP でも GUI と同様に OS 資格情報ストア保護状態および独自 PHP 本番書き込み先確認の事前ガードを強制し、非対話CLIの書き込みコマンドでは `--yes` (`-y`) を必須としました。

### 📄 ライセンスファイル整備
- リポジトリルートに MIT License の `LICENSE` ファイルを追加しました。

---

## English

**v1.0.0** is the first major release of Docker → XServer Sync, introducing Static Site mode, configurable plan validity, detailed real-time progress and ETA reporting across all server workflows, and critical CLI/MCP pipeline fixes.

### 🌐 Static Site Mode
- **Added dedicated `Static Site` preset (`静的サイト（HTML / ビルド済み）`)**:
  - Designed for static HTML/CSS/JS and pre-built outputs (Astro, Next.js `out/`, Hugo, Vite, etc.).
  - Does not enforce PHP runtime path confirmation or skip public `/docs/`, `/test/`, `/README*`, `/LICENSE*`, or `.htaccess` files, while still excluding `.git/`, `.env`, `node_modules/`, and `docker-compose*.yml`.
  - Optional protected directories can still be configured per mapping via **Configure Protected Paths…**.
- **Bulk Deletion Controls**:
  - Added **Select All Deletions** and **Clear All Deletions** actions in approval dialogs while keeping folders under "Do Not Delete Folders" safely excluded.

### ⏱️ Configurable Plan Validity (1–60 Minutes)
- Plan validity (default: 15 minutes) can now be customized from **1 to 60 minutes** via **Settings → Plan Validity Settings…** (and `--validity-minutes` in the CLI).

### 📊 Detailed Progress, Elapsed Time & ETA Telemetry
- Added consistent real-time progress (processed file counts, percentage, elapsed time, ETA, and active file path) across **Auto Safe Sync** (including Static Site mode), **Plan Revalidation**, **Dry Run**, **Backup**, **Approved Sync**, **Restore**, **Safety Verification**, and **Setup Wizards**.

### 🔒 Guardrails, CLI/MCP Fixes & Reliability
- **Saved Project Field Protection**: Locked key identity/path fields by default when editing existing projects to prevent accidental destructive changes.
- **Static Site Auto-Sync Progression Fix**: Fixed progression from Dry Run and Plan Revalidation into Backup and Approved Deployment in Static Site mode.
- **CLI & MCP Hardening**:
  - Persisted `config_sha256` during `execute_dry_run` and `execute_auto_sync` so plan revalidation succeeds consistently.
  - Fixed Phase 5 HTTPS health check invocation, status recording, `execute_verify(cleanup=True)` issue reporting, and `execute_restore` expected-hash verification + `restored` status updates.
  - Added GUI-parity preflight guards for secure host-key storage and Custom PHP writable path confirmation, and enforced `--yes` (`-y`) for non-interactive CLI write commands.

### 📄 Licensing
- Added the root MIT `LICENSE` file.

---

## SHA-256

```text
97d14415034d0b1231c13564692cdb9efd801d180e82c640a0b65a5f67785b01  Docker XServer Sync-macOS-arm64.zip
bef9dd3233a0c7e2e7d4c31f981ca138a7b50c0742e3567ae7ae5d8b38eba7cb  Docker XServer Sync-Windows-x64.zip
1319b01a6342e3604c85a9e310293b4e2ea187946c04de08206b33923dc18823  Docker-XServer-Sync-Setup-v1.0.0.exe
b75e9fdf3da5537b9c019475cd923b832052a4cb27e05c1071a4773ef948cf0e  Docker XServer Sync-Linux-x64.tar.gz
```
