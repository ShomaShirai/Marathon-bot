# マラソン大会締め切り通知 Slack Bot

マラソン大会のエントリー締め切りを監視し、Slack に通知するためのバックエンドです。

MVP では、Slack の Slash Command から大会URLを登録し、GitHub Actions cron で定期的にページを確認します。締め切りの検出、締め切り変更、募集終了、ページ変更、チェック失敗を記録し、必要なタイミングで Slack に通知します。

## 運用方針

このプロジェクトは、通常処理はできるだけ無料枠の範囲で運用し、画像内テキスト解析が必要な場合のみ OpenAI API の有料利用を許容します。

| 用途 | 採用技術 | 方針 |
| --- | --- | --- |
| Backend Hosting | Render | Free Web Service を使う |
| Database | Turso DB | Free プランを使う |
| Scheduler | GitHub Actions cron | Render Cron Jobs は有料のため使わない |
| Slack 通知 | Slack App | Bot Token と `chat.postMessage` を使う |
| Scraping | requests + BeautifulSoup | 静的HTMLを優先して取得する |
| Dynamic Scraping | Playwright | JavaScript描画が必要なページだけで使う |
| Image Analysis / LLM | OpenAI API | 画像内テキスト解析と締め切り候補の構造化抽出に使う |

注意点:

- Render の無料枠はスリープや起動遅延が発生する可能性があります。
- GitHub Actions はリポジトリ種別や利用量によって無料枠の扱いが変わります。
- Slack API にはレート制限があるため、通知は低頻度に抑えます。
- Playwright は実行時間と依存サイズが大きいため、必要な大会ページだけに限定します。
- OpenAI API は有料利用を前提にし、`requests + BeautifulSoup` で締め切りを検出できなかった場合のフォールバック時のみ呼び出します。
- OpenAI API キーは `OPENAI_API_KEY` 環境変数で管理し、README やコードに直接書きません。

参考:

- Render Pricing: https://render.com/pricing
- Turso Pricing: https://turso.tech/pricing
- GitHub Actions Billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- Slack `chat.postMessage`: https://docs.slack.dev/reference/methods/chat.postMessage/
- OpenAI Images and vision: https://developers.openai.com/api/docs/guides/images-vision

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
- OpenAI API
- openai Python SDK
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

MVP では、まず `/marathon add <大会URL>` を優先します。`add` はURLからページタイトルとドメインを取得し、`races` に保存します。登録されたチャンネルに対して、締め切り検出や締め切り前通知を送信します。

Slack からのリクエストでは `SLACK_SIGNING_SECRET` を使って署名検証を行います。通知送信には `SLACK_BOT_TOKEN` を使います。

## API候補

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/health` | Render のヘルスチェック |
| `POST` | `/slack/commands` | Slack Slash Command の受信 |
| `POST` | `/jobs/check-deadlines` | GitHub Actions cron から締め切り確認を実行 |

`/jobs/check-deadlines` は外部から直接呼ばれるため、`JOB_SECRET` で認証します。ただし `APP_ENV=local` の場合はローカル確認を優先し、認証なしで実行できます。

`/slack/commands` は Slack の `X-Slack-Signature` と `X-Slack-Request-Timestamp` を検証します。`SLACK_SIGNING_SECRET` が未設定の場合は受信処理を行いません。

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
| `page_status` | ページ取得状態。`available`, `pending`, `error` |
| `entry_start_at` | 検出したエントリー開始日 |
| `entry_deadline` | 検出したエントリー締め切り |
| `entry_status` | エントリー状態 |
| `last_checked_at` | 最終確認日時 |
| `last_content_hash` | 前回取得内容のハッシュ |
| `last_extraction_method` | 締め切り検出に使った抽出方法。`html` または `llm` |
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
- `entry_schedule_detected`
- `entry_schedule_changed`
- `page_changed`
- `page_pending`
- `page_available`
- `entry_closed`
- `check_failed`

### notifications

Slack へ送信済みの通知を保持し、重複通知を防ぎます。

| Column | Description |
| --- | --- |
| `id` | 通知ID |
| `race_id` | 対象レースID |
| `notification_type` | 通知種別 |
| `dedupe_key` | 重複通知防止キー。日程変更通知では日程文字列、開始・締切接近通知では対象日 |
| `sent_at` | 送信日時 |

`notification_type` の候補:

- `entry_schedule_detected`
- `entry_schedule_changed`
- `entry_start_30_days_before`
- `entry_start_14_days_before`
- `entry_start_7_days_before`
- `entry_deadline_30_days_before`
- `entry_deadline_14_days_before`
- `entry_deadline_7_days_before`

## Scheduler 方針

Render Cron Jobs は有料のため使わず、GitHub Actions cron を使います。

GitHub Actions は定期的に Render 上の FastAPI に対して `POST /jobs/check-deadlines` を呼び出します。ジョブAPIは `JOB_SECRET` によって保護します。本番環境以外の `APP_ENV=local` では、手動確認しやすいように認証を省略できます。

GitHub Actions には以下の Secrets を設定します。

- `APP_BASE_URL`: Render 上のアプリURL
- `JOB_SECRET`: アプリ側の `JOB_SECRET` と同じ値

想定頻度:

- MVP: 1日1回
- 必要に応じて: 1日2回程度

頻度を上げすぎると、GitHub Actions の無料枠、対象サイトへの負荷、Slack通知数に影響するため、まずは低頻度で運用します。

## Scraping 方針

基本方針は、軽量で安定した取得を優先し、検出できない場合だけ段階的に重い処理へフォールバックします。

1. `requests` でHTMLを取得する。
2. `BeautifulSoup` でタイトル、本文、`img alt`、リンクテキスト、画像URL、締め切り候補の周辺テキストを抽出する。
3. ページ本文のハッシュを計算し、前回との差分を検出する。
4. 既存の正規表現ベース検出で締め切りを判定する。
5. 静的HTMLで締め切りが取れない場合のみ Playwright を使い、レンダリング後DOM、表示テキスト、表示画像、必要に応じてスクリーンショットを取得する。
6. Playwright でもテキストから締め切りを検出できない場合のみ、OpenAI API に画像URLまたはスクリーンショットのBase64を渡し、画像内の締め切り候補を構造化抽出する。

締め切り検出では、まず以下のような日本語表現を対象にします。

- エントリー締切
- 申込締切
- 募集締切
- 受付終了
- 申込期間
- エントリー期間

OpenAI API を使う場合も、用途は画像内の締め切り、申込期間、受付終了などの抽出に限定し、Slack 通知文の生成には使いません。

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
| `APP_ENV` | 実行環境。`local` の場合はスクレイピング検出ログを出力 |
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token |
| `SLACK_SIGNING_SECRET` | Slackリクエスト署名検証用のsecret |
| `DATABASE_URL` | DB接続URL。未設定時は `sqlite:///./local.db` |
| `APP_BASE_URL` | Render上のアプリURL |
| `JOB_SECRET` | GitHub Actions cron からジョブAPIを呼ぶためのsecret |
| `OPENAI_API_KEY` | OpenAI API キー。画像解析フォールバックで使用し、コードやREADMEには直接書かない |
| `ENABLE_LLM_IMAGE_ANALYSIS` | OpenAI API による画像解析を有効化するフラグ。未設定時は `OPENAI_API_KEY` があれば有効 |
| `OPENAI_VISION_MODEL` | 画像解析に使う OpenAI モデル名。未設定時は `gpt-4o-mini` |
| `OPENAI_VISION_MAX_IMAGES` | 画像解析に渡す最大画像数。未設定時は `3` |

## Database / Migration

DBアクセスには SQLAlchemy を使い、migration には Alembic を使います。

現時点では、Turso 固有の接続設定はまだ確定していません。ローカル開発では `DATABASE_URL` が未設定の場合、デフォルトで `sqlite:///./local.db` を使います。

migration を作成します。

```bash
make db-revision message="create races"
```

migration を適用します。

```bash
make db-upgrade
```

直前のmigrationを戻します。

```bash
make db-downgrade
```

## ローカル起動

Docker を使って起動します。

```bash
make up
```

ログを前面で見ながら開発する場合は、以下を使います。

```bash
make dev
```

ヘルスチェックを確認します。

```bash
curl http://127.0.0.1:8000/health
```

期待するレスポンス:

```json
{"status":"ok"}
```

開発用に、Slackを経由せずURL登録を確認できます。

```bash
curl "http://127.0.0.1:8000/add?url=https://www.sendaihalf.com/runner/entry/"
```

開発用に、登録済み大会の一覧確認と削除もできます。

```bash
curl http://127.0.0.1:8000/list
curl -X DELETE http://127.0.0.1:8000/remove/1
```

停止します。

```bash
make down
```

コンテナログを確認します。

```bash
make logs
```

Docker を使わずに起動する場合は、仮想環境を作成して依存関係をインストールします。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
uvicorn backend.app.main:app --reload
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
10. Playwright によるレンダリング後DOM取得のフォールバックを実装する。
11. OpenAI API による画像解析フォールバックを実装する。
12. Slack 通知送信を実装する。
13. GitHub Actions cron から `/jobs/check-deadlines` を呼び出す。
14. Render にデプロイする。
