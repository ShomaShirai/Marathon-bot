import requests

from backend.app.core.config import get_env
from backend.app.models.race import Race

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_REQUEST_TIMEOUT_SECONDS = 10


class SlackNotificationError(RuntimeError):
    pass


class SlackNotificationService:
    def __init__(self) -> None:
        self.bot_token = get_env("SLACK_BOT_TOKEN")

    def send_race_notification(
        self,
        *,
        race: Race,
        notification_type: str,
    ) -> None:
        if not self.bot_token:
            raise SlackNotificationError("SLACK_BOT_TOKEN is not configured")

        response = requests.post(
            SLACK_POST_MESSAGE_URL,
            headers={
                "Authorization": f"Bearer {self.bot_token}",
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
        start_text = race.entry_start_at.date().isoformat() if race.entry_start_at else "未検出"
        deadline_text = race.entry_deadline.date().isoformat() if race.entry_deadline else "未検出"

        if notification_type == "entry_schedule_detected":
            heading = "エントリー日程を検出しました。"
        elif notification_type == "entry_schedule_changed":
            heading = "エントリー日程が変更されました。"
        elif notification_type == "7_days_before":
            heading = "エントリー締切まであと7日です。"
        elif notification_type == "3_days_before":
            heading = "エントリー締切まであと3日です。"
        elif notification_type == "1_day_before":
            heading = "エントリー締切は明日です。"
        elif notification_type == "deadline_today":
            heading = "エントリー締切は本日です。"
        else:
            heading = "マラソン大会の更新があります。"

        return "\n".join(
            [
                heading,
                f"大会: {title}",
                f"エントリー開始: {start_text}",
                f"締切: {deadline_text}",
                url,
            ]
        )
