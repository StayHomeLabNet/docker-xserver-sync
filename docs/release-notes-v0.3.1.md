# v0.3.1

## 日本語

### Laravel初回セットアップの安定化・高速化

- **PHPバージョンの自動判別と互換性バリデーション**:
  - ローカルの `vendor/composer/platform_check.php` および `composer.json` を自動解析し、プロジェクトが必要とするPHPバージョン（例: PHP 8.4）を自動検出してウィザードの初期選択値に設定します。
  - 要件を満たさない低いPHPバージョンが選択された場合、事前にエラーを表示して進行をブロックし、Composerのプラットフォームチェックによる不整合を防止します。
- **同名ファイル上書きの安全性向上（「Failure」エラーの解消）**:
  - OpenSSH SFTPの拡張プロトコル `posix_rename` およびフォールバック処理を実装しました。
  - これにより、サーバー上に同名ファイルがすでに存在する場合でも、SFTP標準規格による「Failure」エラーを起こすことなく安全・確実にアトミック更新できるようになりました。
- **配置済みファイルの高速スキップ機能（レジュームの超高速化）**:
  - サーバー側のファイル一覧とファイルサイズを0.2秒で高速取得し、すでに同一サイズで転送済みのファイルを自動判別してスキップします。
  - 万が一途中で処理が中断した場合でも、何時間も再転送を待つことなく約20秒で検証を完了し、後続処理（Artisanコマンド、DBマイグレーション等）へ即座に復帰します。
- **エラーメッセージの可視化**:
  - SFTP操作時に「Failure」とだけ表示される問題を解消し、対象ファイル名やエラー原因の詳細がダイアログに明示されるように改善しました。
- **キャッシュ生成コマンドの安全化**:
  - ルーティングにクロージャ（無名関数）が含まれるプロジェクト等でもセットアップが途中で中断しないよう、キャッシュコマンドのエラーハンドリングを強化しました。

### ドキュメントの改善

- `README.md` を日本語化し、デザインヘッダー（HTMLセンタリング・バッジ構成）を統一。
- 英語版ドキュメントを `README.en.md` として分離公開。

---

## English

### Laravel Setup Improvements & Bug Fixes

- **Automatic PHP Version Detection & Compatibility Validation**:
  - Automatically parses local `vendor/composer/platform_check.php` and `composer.json` to detect the required PHP version (e.g., PHP 8.4) and sets it as the default choice in the wizard.
  - Prevents fatal Composer platform check errors by warning and blocking selections below the required PHP version.
- **Safe File Overwrites via POSIX Rename (Fixes "Failure" Error)**:
  - Implemented OpenSSH `posix_rename` extension support with safe removal fallback.
  - Prevents SFTP `SSH_FX_FAILURE` when destination files already exist on the server.
- **Fast Resumption by Skipping Existing Identical Files**:
  - Fast remote file scanning retrieves remote file list and sizes in ~0.2s.
  - Skips re-uploading files that are already present on XServer with identical sizes, reducing resumption from hours to ~20 seconds.
- **Detailed Error Reporting**:
  - Replaced ambiguous "Failure" messages with detailed contextual error messages identifying the exact file and operation.
- **Graceful Cache Optimization**:
  - Prevents non-critical cache generation failures (e.g. closure-based routes) from stopping the setup process.

### Documentation

- Standardized `README.md` as the primary Japanese README with HTML-centered badges and links.
- Published English documentation as `README.en.md`.

---

## SHA-256

```text
TBD_MACOS_SHA256  Docker XServer Sync-macOS-arm64.zip
TBD_WIN_ZIP_SHA256  Docker XServer Sync-Windows-x64.zip
TBD_WIN_SETUP_SHA256  Docker-XServer-Sync-Setup-v0.3.1.exe
TBD_LINUX_SHA256  Docker XServer Sync-Linux-x64.tar.gz
```
