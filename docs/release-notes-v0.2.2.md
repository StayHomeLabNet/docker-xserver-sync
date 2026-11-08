# v0.2.2

## 空のDBからのWordPress再開復旧

- `wp-config.php`とWordPressファイルは配置済みで、DBが0テーブルの中断状態に対応
- 再開ウィザードで元のSQLダンプを任意選択可能
- DBが空であることを実行直前に再確認し、0テーブルの場合だけSQLをインポート
- 既存テーブルが1つでもある場合はSQLを再投入しない
- 配置済み`wp-config.php`のDB認証情報をXServer内のWP-CLIで使い、パスワードはアプリへ読み出さない
- DBが空でSQL未選択の場合は、次に必要な操作を明確に表示

## 検証

- 自動テスト120件に成功
- macOS Apple Silicon版とWindows x64版のビルド、起動、アーカイブ整合性を確認
- Windowsインストーラーのインストール、起動、アンインストールを確認

## SHA-256

```text
7eb026df68e7182841e633872b01c64ebe421311281135555657b7468f0161ce  Docker XServer Sync-macOS-arm64.zip
3f8cee8b0d7efad9ab6f8f734cb71bc28ebb17dbb9e1bf3b3474317d02c6b079  Docker XServer Sync-Windows-x64.zip
f4a9edec04941d27e7dd164e697039a3ae8eb4fef9abf0a8d68bbd58031edb15  Docker-XServer-Sync-Setup-v0.2.2.exe
```
