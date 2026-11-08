# v0.1.9

## WP-CLI用PHPバージョン選択

- WordPress初回セットアップウィザードに「WP-CLI用PHP」を追加
- XServerで利用可能なPHP 8.0〜8.5をドロップダウンから選択可能
- 既定値をPHP 8.3に設定
- XServerの「PHP Ver.切替」で対象ドメインに設定したバージョンと合わせる案内を追加

## PHP互換性の事前検査

- WP-CLIを`/usr/bin/php8.3 /usr/local/bin/wp ...`のように、選択したXServer公式のPHP CLIパスで明示実行
- DB作成、SQLインポート、WordPressファイル転送の前にPHP実体とWP-CLIの起動を確認
- 古い既定PHPでWordPressを読み込み、URL置換時に構文エラーになる問題を修正

## 失敗時の状態表示

- SQLインポートを「未開始」「途中の可能性」「完了」に分けて表示
- WordPressファイル配置を「未開始」「一部配置の可能性」「wp-config.phpを含め完了」に分けて表示

## 検証

- 自動テス111件に成功
- macOS Apple Silicon版とWindows x64版のビルド、起動、アーカイブ整合性を確認
- Windowsインストーラーのインストール、起動、アンインストールを確認

## SHA-256

```text
03b8aa801492a9dfdd3b3c8a0327e2a3ecefc3e0adf1d3410c63885a542fb383  Docker XServer Sync-macOS-arm64.zip
e582c9300a69bf5916dd9c55dd4171d5f7a449c7d537ad7eeb67c88f52debd55  Docker XServer Sync-Windows-x64.zip
b9986488b39101bd7164166646dddfd385c4c1fbcf250ad23027f55f1b2add0f  Docker-XServer-Sync-Setup-v0.1.9.exe
```
