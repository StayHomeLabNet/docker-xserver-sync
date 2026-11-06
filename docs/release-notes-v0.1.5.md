# v0.1.5

## 安全同期の自動実行

- 「読み取り専用ドライラン」の下に「安全同期を自動実行」を追加
- ドライラン後の統合確認を一度承認すると、再検証、バックアップ、承認付き同期、HTTPS確認を自動継続
- 1/4から4/4までの段階表示と、同期中のファイル件数進捗を表示
- 途中の失敗、計画変更、安全上限超過で即時停止
- REMOTE_ONLYの自動削除と無条件上書きへの自動切替は行わない

## 自動実行設定

- プロジェクト単位のバックアップ方式
- REMOTE_ONLYを残す、または存在時に停止
- HTTPSヘルスチェックの有無とパス
- ADD＋UPDATE数、UPDATE数、転送量の上限
- パスフレーズと実行承認は保存しない

## 画面調整

- メニューバーと「Docker → XServer Sync」の間の上余白を20pxから4pxへ縮小

## 検証

- 自動テスト89件に成功
- Windows ZIP、Windowsインストーラー、macOS ZIP、Linux tar.gzの圧縮形式・実行形式を確認

## SHA-256

```text
28228b9d4b707e2e90369e34bca1af47a3a37dfdb3b983d8fcc0f82f853a0bee  Docker XServer Sync-Linux-x64.tar.gz
0911662ab2864126278b02e7a9a5f14862b50782f727bbc5894e0f39b8acaa10  Docker XServer Sync-Windows-x64.zip
1c69aaea386403009c9bc42e5208c2b2a9544bfea9aef6c051627deaed8959a1  Docker XServer Sync-macOS-arm64.zip
cdbfabc3609deacd5638fc8594d7773f70c0392ca8321e0a1319217485b19c18  Docker-XServer-Sync-Setup-v0.1.5.exe
```
