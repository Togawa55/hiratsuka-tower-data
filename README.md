# hiratsuka-tower-data

平塚沖総合実験タワーの観測値を、GitHub Actionsで自動保存するためのリポジトリです。

## 取得項目

- 波高
- 波周期
- 風速
- 風向

## 保存先

`data/hiratsuka_tower.csv`

GitHub Actionsは毎日23時45分ごろに起動します。GitHub側の混雑により、開始が遅れることがあります。

## 手動実行

GitHubの `Actions` → `Collect Hiratsuka Tower data` → `Run workflow` から実行できます。

## 補足

取得に失敗した場合の確認用として、最新の画面を `debug/latest.png`、HTMLを `debug/latest.html` に保存します。
