from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.core.config import get_env
from backend.app.core.database import get_db
from backend.app.core.security import verify_slack_signature
from backend.app.services.race_service import InvalidRaceIdError, InvalidRaceUrlError, RaceService

router = APIRouter(prefix="/slack", tags=["slack"])


def _first_value(form_data: dict[str, list[str]], key: str) -> str:
    values = form_data.get(key, [""])
    return values[0] if values else ""


@router.post("/commands")
async def handle_slack_command(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    signing_secret = get_env("SLACK_SIGNING_SECRET")
    if not signing_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SLACK_SIGNING_SECRET is not configured",
        )

    body = await request.body()
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

    form_data = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    command = _first_value(form_data, "command")
    text = _first_value(form_data, "text").strip()
    slack_team_id = _first_value(form_data, "team_id")
    slack_channel_id = _first_value(form_data, "channel_id")
    registered_by = _first_value(form_data, "user_id")

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
        race_service = RaceService(db)
        races = race_service.list_by_slack_channel(
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
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


def _build_add_response_text(race: object) -> str:
    title = getattr(race, "title")
    entry_deadline = getattr(race, "entry_deadline")

    if entry_deadline:
        return f"大会を登録しました: {title}\n締切: {entry_deadline.date().isoformat()}"

    return f"大会を登録しました: {title}\n締切はまだ検出できませんでした。"


def _build_list_response_text(races: list[object]) -> str:
    if not races:
        return "このチャンネルには、まだ大会が登録されていません。"

    lines = ["登録済みの大会:"]
    for race in races:
        race_id = getattr(race, "id")
        title = getattr(race, "title")
        url = getattr(race, "url")
        entry_deadline = getattr(race, "entry_deadline")
        entry_status = getattr(race, "entry_status") or "unknown"

        deadline_text = entry_deadline.date().isoformat() if entry_deadline else "未検出"
        lines.append(f"{race_id}. {title} / 締切: {deadline_text} / 状態: {entry_status}\n{url}")

    return "\n".join(lines)
