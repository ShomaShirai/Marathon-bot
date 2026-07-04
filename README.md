# マラソン大会締め切り通知 Slack Bot

マラソン大会のエントリー締め切りを監視し、Slack に通知するためのバックエンドです。

MVP では、Slack の Slash Command から大会URLを登録し、GitHub Actions cron で定期的にページを確認します。締め切りの検出、締め切り変更、募集終了、ページ変更、チェック失敗を記録し、必要なタイミングで Slack に通知します。

## 無料運用方針

このプロジェクトは、できるだけ無料枠の範囲で運用することを前提にします。

| 用途 | 採用技術 | 方針 |
| --- | --- | --- |
| Backend Hosting | Render | Free Web Service を使う |
| Database | Turso DB | Free プランを使う |
| Scheduler | GitHub Actions cron | Render Cron Jobs は有料のため使わない |
| Slack 通知 | Slack App | Bot Token と `chat.postMessage` を使う |
| Scraping | requests + BeautifulSoup | 静的HTMLを優先して取得する |
| Dynamic Scraping | Playwright | JavaScript描画が必要なページだけで使う |

注意点:

- Render の無料枠はスリープや起動遅延が発生する可能性があります。
- GitHub Actions はリポジトリ種別や利用量によって無料枠の扱いが変わります。
- Slack API にはレート制限があるため、通知は低頻度に抑えます。
- Playwright は実行時間と依存サイズが大きいため、必要な大会ページだけに限定します。

参考:

- Render Pricing: https://render.com/pricing
- Turso Pricing: https://turso.tech/pricing
- GitHub Actions Billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- Slack `chat.postMessage`: https://docs.slack.dev/reference/methods/chat.postMessage/

## 技術スタック

- Python
- FastAPI
- SQLAlchemy
- Alembic
- Turso DB
- Slack Web API
- requests
- BeautifulSoup
- Playwright
- GitHub Actions
- Render

## アーキテクチャ概要

```text
Slack Slash Command
        |
        v
FastAPI Backend on Render
        |
        +--> Turso DB
        |
        +--> Scraper
        |
        +--> Slack chat.postMessage

GitHub Actions cron
        |
        v
POST /jobs/check-deadlines
```

主な流れ:

1. Slack の Slash Command で大会URLを登録する。
2. FastAPI がURL、Slackチーム、Slackチャンネル、登録者をDBに保存する。
3. GitHub Actions cron が定期的にバックエンドのジョブAPIを呼び出す。
4. バックエンドが登録済み大会ページを取得し、締め切りやページ変更を検出する。
5. 変更内容を `race_events` に保存する。
6. 通知条件に合う場合、Slack にメッセージを送信し、`notifications` に記録する。

## Slack 連携方針

Slack App では以下を使います。

- Slash Command
- Bot Token
- `chat.postMessage`

想定する Slash Command:

```text
/marathon add <大会URL>
/marathon list
/marathon remove <race_id>
```

MVP では、まず `/marathon add <大会URL>` を優先します。登録されたチャンネルに対して、締め切り検出や締め切り前通知を送信します。

Slack からのリクエストでは `SLACK_SIGNING_SECRET` を使って署名検証を行います。通知送信には `SLACK_BOT_TOKEN` を使います。

## API候補

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/health` | Render のヘルスチェック |
| `POST` | `/slack/commands` | Slack Slash Command の受信 |
| `POST` | `/jobs/check-deadlines` | GitHub Actions cron から締め切り確認を実行 |

`/jobs/check-deadlines` は外部から直接呼ばれるため、`JOB_SECRET` で認証します。

## DB設計

### races

登録された大会ページと現在の検出状態を保持します。

| Column | Description |
| --- | --- |
| `id` | レースID |
| `slack_team_id` | Slack workspace ID |
| `slack_channel_id` | 通知先 Slack channel ID |
| `registered_by` | 登録した Slack user ID |
| `title` | 大会名 |
| `url` | 大会ページURL |
| `source_domain` | URLのドメイン |
| `entry_deadline` | 検出したエントリー締め切り |
| `entry_status` | エントリー状態 |
| `last_checked_at` | 最終確認日時 |
| `last_content_hash` | 前回取得内容のハッシュ |
| `last_detected_text` | 締め切り検出に使ったテキスト |
| `created_at` | 作成日時 |
| `updated_at` | 更新日時 |

### race_events

締め切り検出、変更、ページ変更、失敗などの履歴を保持します。

| Column | Description |
| --- | --- |
| `id` | イベントID |
| `race_id` | 対象レースID |
| `event_type` | イベント種別 |
| `old_value` | 変更前の値 |
| `new_value` | 変更後の値 |
| `created_at` | 作成日時 |

`event_type` の候補:

- `deadline_detected`
- `deadline_changed`
- `page_changed`
- `entry_closed`
- `check_failed`

### notifications

Slack へ送信済みの通知を保持し、重複通知を防ぎます。

| Column | Description |
| --- | --- |
| `id` | 通知ID |
| `race_id` | 対象レースID |
| `notification_type` | 通知種別 |
| `sent_at` | 送信日時 |

`notification_type` の候補:

- `deadline_detected`
- `7_days_before`
- `3_days_before`
- `1_day_before`
- `deadline_today`

## Scheduler 方針

Render Cron Jobs は有料のため使わず、GitHub Actions cron を使います。

GitHub Actions は定期的に Render 上の FastAPI に対して `POST /jobs/check-deadlines` を呼び出します。ジョブAPIは `JOB_SECRET` によって保護します。

想定頻度:

- MVP: 1日1回
- 必要に応じて: 1日2回程度

頻度を上げすぎると、GitHub Actions の無料枠、対象サイトへの負荷、Slack通知数に影響するため、まずは低頻度で運用します。

## Scraping 方針

基本方針は、軽量で安定した取得を優先します。

1. `requests` でHTMLを取得する。
2. `BeautifulSoup` でタイトル、本文、締め切り候補のテキストを抽出する。
3. ページ本文のハッシュを計算し、前回との差分を検出する。
4. 静的HTMLで締め切りが取れない場合のみ Playwright を使う。

締め切り検出では、まず以下のような日本語表現を対象にします。

- エントリー締切
- 申込締切
- 募集締切
- 受付終了
- 申込期間
- エントリー期間

MVP では完全な自動抽出を目指しすぎず、検出できない場合は `check_failed` または `page_changed` として記録し、改善しやすい形にします。

## MVC構成案

簡潔な MVC ベースの構成にします。

```text
backend/
  app/
    main.py
    controllers/
      health_controller.py
      slack_controller.py
      job_controller.py
    models/
      race.py
      race_event.py
      notification.py
    schemas/
      slack.py
      race.py
      job.py
    repositories/
      race_repository.py
      race_event_repository.py
      notification_repository.py
    services/
      race_service.py
      scraping_service.py
      deadline_detection_service.py
      notification_service.py
      slack_service.py
    core/
      config.py
      security.py
      database.py
  migrations/
```

役割:

- `controllers`: FastAPIルーティング、Slack Slash Command、cron起動API
- `models`: SQLAlchemyモデル、DBエンティティ
- `schemas`: Pydanticのリクエスト/レスポンス定義
- `repositories`: Turso/SQLiteへのDBアクセス
- `services`: レース登録、スクレイピング、締切判定、通知送信
- `core`: 設定、署名検証、DB接続などの共通処理

## 環境変数

| Name | Description |
| --- | --- |
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token |
| `SLACK_SIGNING_SECRET` | Slackリクエスト署名検証用のsecret |
| `DATABASE_URL` | Turso DB 接続URL |
| `APP_BASE_URL` | Render上のアプリURL |
| `JOB_SECRET` | GitHub Actions cron からジョブAPIを呼ぶためのsecret |

## ローカル起動

仮想環境を作成して依存関係をインストールします。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

FastAPI を起動します。

```bash
uvicorn backend.app.main:app --reload
```

ヘルスチェックを確認します。

```bash
curl http://127.0.0.1:8000/health
```

期待するレスポンス:

```json
{"status":"ok"}
```

## 今後の実装ステップ

1. FastAPI の最小構成を作成する。
2. SQLAlchemy と Alembic を導入する。
3. Turso DB 接続を設定する。
4. `races`, `race_events`, `notifications` の migration を作成する。
5. `/health` を実装する。
6. Slack Slash Command の署名検証と受信処理を実装する。
7. `/marathon add <URL>` で大会を登録できるようにする。
8. requests + BeautifulSoup のスクレイピング処理を実装する。
9. 締め切り検出とイベント記録を実装する。
10. Slack 通知送信を実装する。
11. GitHub Actions cron から `/jobs/check-deadlines` を呼び出す。
12. Render にデプロイする。
