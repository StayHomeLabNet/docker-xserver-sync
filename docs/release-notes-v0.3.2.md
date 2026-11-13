# v0.3.2

## 日本語

### プロジェクトの複製機能の追加
- メインウィンドウのプロジェクト操作バーに「複製…」ボタンを追加しました（配置順: 「プロジェクト」→「新規…」→「複製…」→「編集…」→「削除」→「安全性確認」→「履歴…」）。
- 「プロジェクトを編集」ダイアログ内の「入力項目の確認方法」ボタンの左隣にも「複製…」ボタンを追加しました。
- サイト種別（独自PHP / WordPress / Laravel）、ドメイン、SSH接続情報、秘密鍵、確認コード、パスマッピング設定を引き継ぎ、重複しない名前（例: `〜 のコピー`）で新しい独立したプロジェクトとして即座に複製・保存できます。

### 一時停止・リジューム機能の強化（Laravel）
- Laravel初回セットアップの未完了状態を起動時に正しく認識するようステータス管理を改善しました。
- セットアップパネルに「中断したセットアップを再開…」ボタンを追加し、ファイル境界で停止した後の安全な高速リジューム（配置済みファイルのスキップ）に対応しました。

### 網羅的な詳細マニュアルの追加とヘルプメニューの拡充
- ヘルプメニューに「オンラインマニュアル」項目を追加し、詳細マニュアルへブラウザからワンクリックでアクセスできるようになりました。
- `doc/`（および `docs/`）に、独自PHP・WordPress・Laravelの全機能、安全設計、一時停止・リジューム、セキュリティ、トラブルシューティングを網羅した詳細マニュアル（日本語版 `manual.md` / 英語版 `manual.en.md`）を新規追加しました。
- ヘルプの「バージョン情報」ダイアログ内に GitHub リポジトリへのハイパーリンクを追加しました。

---

## English

### Project Duplication (Clone Feature)
- Added "Clone…" (複製…) button to the main window control bar between "New…" and "Edit…".
- Added "Clone…" button inside the project edit dialog to the left of "How to check inputs".
- One-click project duplication copying all SSH connection settings, keys, domain, and path mappings with auto-incremented naming (`<Name> のコピー`).

### Enhanced Pause & Resume for Laravel
- Improved state handling so interrupted Laravel setups are properly identified upon application restart.
- Added a dedicated "Resume Interrupted Setup…" button to the setup panel for Laravel projects.

### Comprehensive User Manuals & Online Manual Menu
- Added "Online Manual" item under the Help menu.
- Added comprehensive, exhaustive user manuals in both Japanese (`docs/manual.md`) and English (`docs/manual.en.md`).
- Added clickable GitHub repository link to the Help → About dialog.

---

## SHA-256

```text
aab2bf8f468b6135ce92e4028ce4759bf4f2c22fb331e2e00cd745c1ee02d756  Docker XServer Sync-macOS-arm64.zip
5582cd4bb094ce04c425dd260c20014a7f3686f0d3f732ec937c926f3613ce35  Docker XServer Sync-Windows-x64.zip
6ae436c2647218a9cee155a729fd83119a738693658fd39cb7d714ac377114d6  Docker-XServer-Sync-Setup-v0.3.2.exe
339838efee3c01ed7484a537e123e94f8d98b052d852ef7cd7093f9bb205eb5b  Docker XServer Sync-Linux-x64.tar.gz
```
