# v1.1.4

## 日本語

Docker → XServer Sync **v1.1.4** は、表示テーマ切替の追加、SSHバッチSHA-256計算によるスキャン高速化、プロジェクト設定での保存前接続テスト、古い操作履歴・計画の整理機能、パッケージ版でのアップデート確認SSL修正を含む機能改善・品質向上リリースです。

### 🎨 表示テーマ切替（システム設定 / ライト / ダーク）

- メイン画面上部に **「表示テーマ」** セレクタを追加し、「システム設定」「ライト」「ダーク」をいつでも切り替えられるようになりました。
- 選択したテーマは保存され、次回起動時にも引き継がれます。

### ⚡ 高速化・操作性向上

- **SSHバッチSHA-256計算**: リモート走査および同期時のSHA-256ハッシュ計算をSSHコマンドでバッチ実行し、多数のファイルがある環境での通信往復を大幅に削減しました（利用できない環境では従来のSFTPストリーム計算へ自動フォールバックします）。
- **プロジェクト設定の「接続テスト…」ボタン**: プロジェクトを保存する前に、SSH/SFTP接続、確認コード（SHA-256）、リモートパスの疎通をバックグラウンドでテストできるようになりました。
- **古い操作履歴・計画の整理**: 履歴ダイアログに **「古い操作履歴・計画を整理…」** ボタンを追加し、90日より前の操作履歴（および最新200件を超えた古い履歴）と不要な計画データをローカルDBから安全に削除できるようになりました（XServer上のファイル・バックアップには影響しません）。

### 🔧 修正・コード品質・CI

- **アップデート確認・HTTPS動作確認・API通信のSSL証明書解決を修正**: `certifi` のCA証明書バンドルを `updater.py`、`health.py`、`xserver_api.py` および各プラットフォームのNuitkaビルドに含め、パッケージ化されたアプリでもGitHub Releasesへのアップデート確認や同期後のHTTPS動作確認、XServer API通信が正しく動作するよう修正しました。
- **バックアップスキップ時の動作確認失敗メッセージとモーダル表示順序を改善**: バックアップをスキップした同期でHTTPS動作確認に失敗した場合に履歴から復元できない旨を明確に表示し、自動実行完了・エラー時のモーダルダイアログ表示中に自動実行の次段階が誤って再実行されないよう修正しました。
- **UIモジュール分割とコード品質改善**: `file_table_model.py` と `runtime_paths_dialog.py` を分離して保守性を向上させ、全ソース・テストに対して `ruff` による静的検査を導入しました（CIワークフローにも組み込み済み）。

### 📚 ドキュメント・テスト

- README（日英）、詳細マニュアル（日英）、実装状況ドキュメントを更新しました。
- テーマ切替、バッチハッシュ計算、接続テスト、履歴整理、SSL証明書検証、自動実行完了時の状態遷移テストを追加し、全 **234件** のユニットテストが合格しています。

---

## English

Docker → XServer Sync **v1.1.4** introduces a persistent appearance selector, faster remote scanning via SSH batch SHA-256 hashing, a pre-save connection test in Project Settings, local history/plan pruning, and an SSL certificate fix for in-app update checks in packaged builds.

### 🎨 Persistent Theme Selector (System / Light / Dark)

- Added a **Theme** selector in the main window header to switch between **System Setting**, **Light**, and **Dark** appearance at any time.
- Your theme preference is persisted across application launches.

### ⚡ Performance & Usability Improvements

- **SSH Batch SHA-256 Hashing**: Remote scanning and deployment now compute remote SHA-256 digests in batches over SSH to significantly reduce SFTP round-trips on large sites, with automatic fallback to SFTP streaming when unavailable.
- **Pre-Save Connection Test**: Added a **Test Connection…** button in Project Settings to asynchronously verify SSH/SFTP connectivity, host key fingerprint (SHA-256), and remote directory access before saving.
- **Prune Old History & Plans**: Added a **Prune Old History & Plans…** button in the History dialog to clean up operation records older than 90 days (or beyond the latest 200 entries) and orphaned plans from the local database without touching files or backups on XServer.

### 🔧 Fixes, Refactoring & CI

- **Packaged SSL Certificate Fix**: Configured `updater.py`, `health.py`, `xserver_api.py`, and Nuitka build scripts (`build-app.sh`, `build-app.ps1`, `build-app-linux.sh`) to use and bundle `certifi` CA certificates so in-app update checks, post-sync HTTPS health checks, and XServer API calls work reliably in standalone binaries.
- **Clearer Backup-Skipped & Modal Auto-Sync Fix**: Updated the post-sync health-check failure dialog to clearly note when backup creation was skipped and restoration from history is unavailable, and stopped auto-sync state before showing modal completion/error dialogs so the finished worker signal cannot re-trigger deployment.
- **Modularization & Linting**: Extracted `file_table_model.py` and `runtime_paths_dialog.py`, resolved lint issues across the codebase, and added `ruff check` to the CI workflow.

### 📚 Documentation & Tests

- Updated the Japanese and English READMEs, user manuals, and implementation status notes.
- Added automated tests for theme switching, batch remote hashing, connection testing, history pruning, `certifi` SSL context usage, and auto-sync modal completion handling; all **234 unit tests** pass.

---

Release assets include Windows x64 portable and installer packages, a macOS Apple Silicon application archive, a Linux x64 archive, and `SHA256SUMS.txt`.
