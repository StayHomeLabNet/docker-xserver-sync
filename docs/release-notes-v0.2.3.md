# v0.2.3

## WordPress URL移行と完了表示の修正

- DB内のWordPress `home`と`siteurl`をWP-CLIで自動検出
- `http://localhost:9999`のようなポート付きURL全体を検出し、指定したXServer URLへ置換
- 現在の`https://demo.stayhomelab.net:9999`のような誤った値も、再開実行で正規のURLへ修正
- シリアライズデータ対応の`wp search-replace`後、`home`と`siteurl`を明示更新して再検証
- 入力したXServer URLをHTTPSヘルスチェックと完了表示に使用
- 親ドメインを誤って完了ポップアップへ表示する問題を修正
- 想定外のドメインや`:9999`など別ポートへのリダイレクトをHTTPS確認失敗として報告
- 完了ポップアップに完了状態、トップ・ログインURL、HTTP状態、DB、テーブル数、転送数、検出・最終URLを表示

## 検証

- 自動テスト121件に成功
- macOS Apple Silicon版とWindows x64版のビルド、起動、アーカイブ整合性を確認
- Windowsインストーラーのインストール、起動、アンインストールを確認

## SHA-256

```text
19d39927cbb09c7f9b0a93f712767027b446319737ce6f047150feea4f2ed21a  Docker XServer Sync-macOS-arm64.zip
f1c3b2ff8d72f42cede6180d3a42e5d062c133de7d8da1e000e32970a4aa3706  Docker XServer Sync-Windows-x64.zip
a8d5a1becade79e03a5a69736bfcbc83a0afa16e46d1b68bd14c69e5a847148a  Docker-XServer-Sync-Setup-v0.2.3.exe
```
