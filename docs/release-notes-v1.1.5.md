# v1.1.5

## 日本語

Docker → XServer Sync **v1.1.5** は、完全自動実行・同期前後フック・並列SFTP転送・テキスト差分ビューア・Docker Compose連携・APIキー資格情報保存を一挙に追加する自動化・生産性向上リリースです。

### 🤖 完全自動実行と同期前後フック

- **ドライラン後の確認画面スキップ設定**: 自動実行設定の「ドライラン後の確認画面をスキップして完全自動実行する」を有効にすると、安全上限・保護チェック通過後に確認画面を表示せず再検証・バックアップ・同期まで自動完了できます。
- **許可フォルダ限定の自動削除（静的サイト向け）**: 確認画面スキップかつバックアップ有効時に、「自動削除許可フォルダ」（例: `assets/`、`_astro/`）を指定すると、該当フォルダ配下の `REMOTE_ONLY` ファイルのみ事前バックアップのうえ自動削除します。
- **同期前ローカルコマンド（Pre-sync フック）**: 同期前にローカルフォルダでビルドコマンド（例: `npm run build`）を自動実行し、失敗した場合はドライラン・同期を中止します。
- **同期後リモートコマンド ＆ Webhook 通知（Post-sync フック）**: 同期成功後に XServer 上でリモートコマンド（例: `php artisan config:cache`）を実行し、Slack / Discord 互換 Webhook（`https://`）へ完了通知を送信できます。

### ⚡ 転送高速化と差分ビューア

- **SFTP 並列転送**: 複数SFTPチャネルを用いた並列アップロードおよび並列リモートバックアップに対応し、多数ファイル更新時の転送時間を短縮しました。
- **ドライラン結果のテキスト差分（Diff）ビューア**: ドライラン結果の `UPDATE` 行をダブルクリックすると、ローカルファイルと XServer 上のファイル内容の unified diff をハイライト表示で確認できます（バイナリ／1 MiB超は自動判定）。

### 🐳 初回セットアップ・認証の利便性向上

- **Docker Compose 自動検出 ＆ SQLダンプ自動生成**: WordPress / Laravel 初回セットアップウィザードの「Dockerから生成…」ボタンから、`compose.yaml` / `docker-compose.yml` の MySQL / MariaDB サービスを検出し、SQLダンプをワンクリックで自動出力できます。
- **XServer API キーの OS 資格情報ストア保存**: ウィザード画面で「APIキーをOS資格情報ストアに保存する」にチェックを入れると、macOS キーチェーン / Windows 資格情報マネージャー / Linux Secret Service に暗号化保存・自動復元できます。

### 🧪 テスト

- フック実行、許可フォルダ抽出、並列SFTPアップロード／バックアップ、Diffビューア、Docker Compose解析・SQL出力、APIキー資格情報保存、確認画面スキップ自動実行のテストを追加し、全 **248件** のユニットテストが合格しています。

---

## English

Docker → XServer Sync **v1.1.5** is a major automation and productivity release introducing hands-free auto-sync, pre/post-sync hooks, parallel SFTP transfers, a dry-run diff viewer, Docker Compose SQL dump generation, and OS credential-store persistence for XServer API keys.

### 🤖 Hands-Free Auto-Sync & Hooks

- **Skip Approval Dialog Option**: Enable **Skip confirmation dialog after dry run** in Auto-Sync Settings to proceed directly through revalidation, backup, and deployment once safety limits and protection checks pass.
- **Allowlisted Auto-Delete Folders**: When skipping the approval dialog with backups enabled, specify allowed folders (e.g. `assets/`, `_astro/`) to automatically back up and remove obsolete `REMOTE_ONLY` build artifacts inside those folders only.
- **Pre-Sync Local Command**: Automatically run a local build command (e.g. `npm run build`) before dry run; if it exits non-zero, sync is aborted safely.
- **Post-Sync Remote Command & Webhook Notifications**: Execute a remote command on XServer (e.g. `php artisan config:cache`) after deployment and send Slack/Discord-compatible JSON notifications over HTTPS.

### ⚡ Parallel Transfers & Diff Viewer

- **Parallel SFTP Transfers**: Uploads and remote backups now use concurrent SFTP channels on the SSH connection to speed up multi-file deployments.
- **Text Diff Viewer**: Double-click any `UPDATE` row in the dry-run table to inspect a syntax-highlighted unified diff between the local and XServer file (with automatic binary and 1 MiB size guards).

### 🐳 Setup Wizards & Credentials

- **Docker Compose Auto-Detection & SQL Dump Generation**: Click **Generate from Docker…** in the WordPress or Laravel setup wizard to detect MySQL/MariaDB services from `compose.yaml` / `docker-compose.yml` and generate a SQL dump automatically.
- **OS Credential Store for XServer API Keys**: Opt in to saving and restoring XServer API keys securely via macOS Keychain, Windows Credential Manager, or Linux Secret Service.

### 🧪 Tests

- Added automated tests covering hooks, allowlisted folder filtering, parallel SFTP deployment/backup, diff rendering, Docker Compose detection & SQL export, API key storage, and approval-skip auto-sync; all **248 unit tests** pass.

---

Release assets include Windows x64 portable and installer packages, a macOS Apple Silicon application archive, a Linux x64 archive, and `SHA256SUMS.txt`.
