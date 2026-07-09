import logging
from urllib.parse import parse_qs

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.core.config import get_env
from backend.app.core.database import SessionLocal, get_db
from backend.app.core.security import verify_slack_signature
from backend.app.repositories.channel_subscription_repository import ChannelSubscriptionRepository
from backend.app.services.race_service import CATEGORY_MARATHON, CATEGORY_TENNIS
from backend.app.services.race_service import InvalidRaceIdError, InvalidRaceUrlError, RaceService

router = APIRouter(prefix="/slack", tags=["slack"])
logger = logging.getLogger(__name__)
SLACK_RESPONSE_URL_TIMEOUT_SECONDS = 10


def _first_value(form_data: dict[str, list[str]], key: str) -> str:
    values = form_data.get(key, [""])
    return values[0] if values else ""


@router.post("/commands")
async def handle_slack_command(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    body = await request.body()
    form_data = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    command = _first_value(form_data, "command")

    signing_secret = _signing_secret_for_command(command)
    if not signing_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Signing secret is not configured for command: {command}",
        )

    is_verified = verify_slack_signature(
        signing_secret=signing_secret,
        timestamp=request.headers.get("x-slack-request-timestamp"),
        signature=request.headers.get("x-slack-signature"),
        body=body,
    )
    if not is_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Slack signature",
        )

    text = _first_value(form_data, "text").strip()
    slack_team_id = _first_value(form_data, "team_id")
    slack_channel_id = _first_value(form_data, "channel_id")
    registered_by = _first_value(form_data, "user_id")
    response_url = _first_value(form_data, "response_url")

    if command == "/tennis":
        return _handle_tennis_command(
            text=text,
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            registered_by=registered_by,
            db=db,
        )

    if command != "/marathon":
        return {
            "response_type": "ephemeral",
            "text": "未対応のコマンドです。",
        }

    if text.startswith("add "):
        url = text.removeprefix("add ").strip()
        race_service = RaceService(db)
        try:
            race = race_service.register_from_url(
                url=url,
                slack_team_id=slack_team_id,
                slack_channel_id=slack_channel_id,
                registered_by=registered_by,
                category=CATEGORY_MARATHON,
            )
        except InvalidRaceUrlError as exc:
            return {
                "response_type": "ephemeral",
                "text": str(exc),
            }

        return {
            "response_type": "ephemeral",
            "text": _build_add_response_text(race),
        }

    if text == "list":
        if response_url:
            background_tasks.add_task(
                _send_marathon_list_response,
                response_url=response_url,
                slack_team_id=slack_team_id,
                slack_channel_id=slack_channel_id,
            )
            return {
                "response_type": "ephemeral",
                "text": "登録済みの大会を取得しています。",
            }

        race_service = RaceService(db)
        races = race_service.list_by_slack_channel(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            category=CATEGORY_MARATHON,
        )
        return {
            "response_type": "ephemeral",
            "text": _build_list_response_text(races),
        }

    if text.startswith("remove "):
        race_id_text = text.removeprefix("remove ").strip()
        race_service = RaceService(db)
        try:
            removed = race_service.remove_by_id_for_slack_channel(
                race_id_text=race_id_text,
                slack_team_id=slack_team_id,
                slack_channel_id=slack_channel_id,
                category=CATEGORY_MARATHON,
            )
        except InvalidRaceIdError as exc:
            return {
                "response_type": "ephemeral",
                "text": str(exc),
            }

        if not removed:
            return {
                "response_type": "ephemeral",
                "text": f"race_id={race_id_text} の大会は、このチャンネルには見つかりませんでした。",
            }

        return {
            "response_type": "ephemeral",
            "text": f"race_id={race_id_text} の大会を削除しました。",
        }

    return {
        "response_type": "ephemeral",
        "text": "使い方: /marathon add <大会URL>、/marathon list、/marathon remove <race_id>",
    }


def _send_marathon_list_response(
    *,
    response_url: str,
    slack_team_id: str,
    slack_channel_id: str,
) -> None:
    db = SessionLocal()
    try:
        race_service = RaceService(db)
        races = race_service.list_by_slack_channel(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            category=CATEGORY_MARATHON,
        )
        text = _build_list_response_text(races)
    except Exception as exc:
        logger.warning("failed to build marathon list response error=%s", exc)
        text = "登録済み大会の取得に失敗しました。時間をおいて再度お試しください。"
    finally:
        db.close()

    try:
        response = requests.post(
            response_url,
            json={
                "response_type": "ephemeral",
                "text": text,
            },
            timeout=SLACK_RESPONSE_URL_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("failed to send marathon list response to Slack error=%s", exc)


def _signing_secret_for_command(command: str) -> str | None:
    if command == "/tennis":
        return get_env("SLACK_TENNIS_SIGNING_SECRET")

    return get_env("SLACK_SIGNING_SECRET")


def _handle_tennis_command(
    *,
    text: str,
    slack_team_id: str,
    slack_channel_id: str,
    registered_by: str,
    db: Session,
) -> dict[str, str]:
    if text == "subscribe":
        _, created = ChannelSubscriptionRepository(db).get_or_create(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
            category=CATEGORY_TENNIS,
            registered_by=registered_by,
        )
        if created:
            message = "このチャンネルをテニス大会の締切通知先として登録しました。"
        else:
            message = "このチャンネルは既にテニス大会の締切通知先として登録済みです。"

        return {
            "response_type": "ephemeral",
            "text": message,
        }

    return {
        "response_type": "ephemeral",
        "text": "使い方: /tennis subscribe",
    }


def _build_add_response_text(race: object) -> str:
    title = getattr(race, "title")
    entry_start_at = getattr(race, "entry_start_at")
    entry_deadline = getattr(race, "entry_deadline")
    lines = [f"大会を登録しました: {title}"]

    if entry_start_at:
        lines.append(f"エントリー開始: {entry_start_at.date().isoformat()}")

    if entry_deadline:
        lines.append(f"締切: {entry_deadline.date().isoformat()}")
    else:
        lines.append("締切はまだ検出できませんでした。")

    return "\n".join(lines)


def _build_list_response_text(races: list[object]) -> str:
    if not races:
        return "このチャンネルには、まだ大会が登録されていません。"

    lines = ["登録済みの大会:"]
    for race in races:
        race_id = getattr(race, "id")
        title = getattr(race, "title")
        url = getattr(race, "url")
        entry_start_at = getattr(race, "entry_start_at")
        entry_deadline = getattr(race, "entry_deadline")
        entry_status = getattr(race, "entry_status") or "unknown"

        start_text = entry_start_at.date().isoformat() if entry_start_at else "未検出"
        deadline_text = entry_deadline.date().isoformat() if entry_deadline else "未検出"
        lines.append(
            f"{race_id}. {title} / 開始: {start_text} / 締切: {deadline_text} / 状態: {entry_status}\n{url}"
        )

    return "\n".join(lines)
