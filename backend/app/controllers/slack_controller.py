from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.core.config import get_env
from backend.app.core.database import get_db
from backend.app.core.security import verify_slack_signature
from backend.app.services.race_service import InvalidRaceUrlError, RaceService

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
            "text": f"大会を登録しました: {race.title}\n{race.url}",
        }

    return {
        "response_type": "ephemeral",
        "text": "使い方: /marathon add <大会URL>",
    }
