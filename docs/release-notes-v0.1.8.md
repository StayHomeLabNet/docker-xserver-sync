# v0.1.8

## WordPressセットアップの進捗表示

- 初回セットアップ全体を10段階に分け、現在の手順を表示
- WordPressファイル転送中は、完了件数/全件数、割合、現在のファイル、推定残り時間を表示
- SQLダンプ転送中は、転送済み容量/全容量、割合、推定残り時間を表示
- SQLインポートなど進捗量を取得できない処理でも、経過時間と最終更新からの時間を毎秒更新

## WordPress初回セットアップUI

- MySQLホストの初期値と入力例を`localhost`に変更
- APIキーを使わない場合、XServer側で空のDB・MySQLユーザー・アクセス権を事前準備する必要があることを強調表示
- 事前準備の確認チェックを追加し、各入力欄を拡張
- 選択中のプロジェクト種別に合わせ、WordPressまたはLaravelの初回セットアップ案内だけを表示

## 検証

- 自動テス110件に成功
- macOS Apple Silicon版とWindows x64版のビルド、アーカイブ整合性を確認

## SHA-256

```text
ebbb4155f1d978b07b1002da854bbfd94e3582580be32029a77f912ade6f20c9  Docker XServer Sync-macOS-arm64.zip
WINDOWS_ZIP_SHA256  Docker XServer Sync-Windows-x64.zip
WINDOWS_INSTALLER_SHA256  Docker-XServer-Sync-Setup-v0.1.8.exe
```
