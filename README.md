# NicoNico Tag Monitor

ニコニコ動画のタグが**消された**ことを検知すると、LINE Notify または Microsoft Teams に通知します。

## セットアップ

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```
`.env` を編集（LINE / Teams / VIDEOSを設定）

## 使い方

```bash
python monitor_niconico.py --videos sm9,sm12345678
# もしくは .env の VIDEOS を設定して引数なしで実行
python monitor_niconico.py
```

## 30分おきに実行（cron）

```cron
*/30 * * * * /path/to/venv/bin/python /path/to/niconico-tag-monitor/monitor_niconico.py --state /path/to/state.json >> /path/to/monitor.log 2>&1
```

## GitHub Actions
`.github/workflows/monitor.yml` を使って、クラウドで30分おきに実行：
- Secrets に `LINE_NOTIFY_TOKEN`, `TEAMS_WEBHOOK_URL`, `VIDEOS` を登録
