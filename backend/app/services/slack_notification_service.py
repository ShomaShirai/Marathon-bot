import requests

from backend.app.core.config import get_env
from backend.app.models.race import Race
from backend.app.services.race_service import CATEGORY_TENNIS

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_REQUEST_TIMEOUT_SECONDS = 10


class SlackNotificationError(RuntimeError):
    pass


class SlackNotificationService:
    def __init__(self) -> None:
        self.marathon_bot_token = get_env("SLACK_BOT_TOKEN")
        self.tennis_bot_token = get_env("SLACK_TENNIS_BOT_TOKEN")

    def send_race_notification(
        self,
        *,
        race: Race,
        notification_type: str,
    ) -> None:
        bot_token = self._bot_token_for_race(race)
        if not bot_token:
            env_name = "SLACK_TENNIS_BOT_TOKEN" if race.category == CATEGORY_TENNIS else "SLACK_BOT_TOKEN"
            raise SlackNotificationError(f"{env_name} is not configured")

        response = requests.post(
            SLACK_POST_MESSAGE_URL,
            headers={
                "Authorization": f"Bearer {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "channel": race.slack_channel_id,
                "text": self._build_message_text(race=race, notification_type=notification_type),
            },
            timeout=SLACK_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            error = payload.get("error", "unknown_error")
            raise SlackNotificationError(f"Slack chat.postMessage failed: {error}")

    def _build_message_text(
        self,
        *,
        race: Race,
        notification_type: str,
    ) -> str:
        title = race.title
        url = race.url
        race_label = "テニス大会" if race.category == CATEGORY_TENNIS else "マラソン大会"
        start_text = race.entry_start_at.date().isoformat() if race.entry_start_at else "未検出"
        deadline_text = race.entry_deadline.date().isoformat() if race.entry_deadline else "未検出"

        if notification_type == "entry_schedule_detected":
            heading = "エントリー日程を検出しました。"
        elif notification_type == "entry_schedule_changed":
            heading = "エントリー日程が変更されました。"
        elif notification_type == "entry_start_30_days_before":
            heading = "エントリー開始まであと1か月です。"
        elif notification_type == "entry_start_14_days_before":
            heading = "エントリー開始まであと2週間です。"
        elif notification_type == "entry_start_7_days_before":
            heading = "エントリー開始まであと1週間です。"
        elif notification_type == "entry_deadline_30_days_before":
            heading = "エントリー締切まであと1か月です。"
        elif notification_type == "entry_deadline_14_days_before":
            heading = "エントリー締切まであと2週間です。"
        elif notification_type == "entry_deadline_7_days_before":
            heading = "エントリー締切まであと1週間です。"
        else:
            heading = f"{race_label}の更新があります。"

        return "\n".join(
            [
                heading,
                f"{race_label}: {title}",
                f"エントリー開始: {start_text}",
                f"締切: {deadline_text}",
                url,
            ]
        )

    def _bot_token_for_race(self, race: Race) -> str | None:
        if race.category == CATEGORY_TENNIS:
            return self.tennis_bot_token

        return self.marathon_bot_token
