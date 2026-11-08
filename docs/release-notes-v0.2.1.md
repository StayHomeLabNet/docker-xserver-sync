# v0.2.1

## WordPress再開時のDB認証修正

- 配置済み`wp-config.php`がある場合、再開時はそのDB接続情報をXServer内でWP-CLIが使用
- DBパスワードをアプリや操作履歴へ読み出さず、DB接続とテーブル数を確認
- 再開ウィザードの「配置済みwp-config.phpのDB接続情報を使用（推奨）」を既定で有効化
- 推奨モードではDB名、DBユーザー、DBパスワード、MySQLホストの再入力を不要化
- `wp-config.php`がまだない部分転送では、推奨モードを解除してXServerのDB接続情報を入力可能

## 検証

- 自動テスト118件に成功
- macOS Apple Silicon版とWindows x64版のビルド、起動、アーカイブ整合性を確認
- Windowsインストーラーのインストール、起動、アンインストールを確認

## SHA-256

```text
1a5907565a88c14c983b370b63ce3b22df099c1faee00d4e21b2d40553c1e572  Docker XServer Sync-macOS-arm64.zip
a1cbf52efbb8f407caf043671c442fd27accdf4bd1aa40f8e65a89dac648b3a6  Docker XServer Sync-Windows-x64.zip
9abb135f7cd390b28a16a6a4d03ac00144eb9b42456f9ca71eaad0c74e467920  Docker-XServer-Sync-Setup-v0.2.1.exe
```
