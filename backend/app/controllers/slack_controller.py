from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status

from backend.app.core.config import get_env
from backend.app.core.security import verify_slack_signature

router = APIRouter(prefix="/slack", tags=["slack"])


def _first_value(form_data: dict[str, list[str]], key: str) -> str:
    values = form_data.get(key, [""])
    return values[0] if values else ""


@router.post("/commands")
async def handle_slack_command(request: Request) -> dict[str, str]:
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

    if command != "/marathon":
        return {
            "response_type": "ephemeral",
            "text": "未対応のコマンドです。",
        }

    if text.startswith("add "):
        url = text.removeprefix("add ").strip()
        return {
            "response_type": "ephemeral",
            "text": f"大会URLを受け付けました: {url}",
        }

    return {
        "response_type": "ephemeral",
        "text": "使い方: /marathon add <大会URL>",
    }
