from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.services.race_service import InvalidRaceIdError, InvalidRaceUrlError, RaceService

router = APIRouter(tags=["dev"])

DEV_SLACK_TEAM_ID = "DEV_TEAM"
DEV_SLACK_CHANNEL_ID = "DEV_CHANNEL"
DEV_REGISTERED_BY = "DEV_USER"


class AddRaceRequest(BaseModel):
    url: str


@router.get("/add")
def add_race_from_query(
    url: str = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return _register_dev_race(url=url, db=db)


@router.post("/add")
def add_race_from_body(
    request: AddRaceRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return _register_dev_race(url=request.url, db=db)


@router.get("/list")
def list_dev_races(db: Session = Depends(get_db)) -> dict[str, object]:
    race_service = RaceService(db)
    races = race_service.list_by_slack_channel(
        slack_team_id=DEV_SLACK_TEAM_ID,
        slack_channel_id=DEV_SLACK_CHANNEL_ID,
    )

    return {
        "count": len(races),
        "races": [_serialize_race(race) for race in races],
    }


@router.delete("/remove/{race_id}")
def remove_dev_race(
    race_id: int,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    race_service = RaceService(db)
    try:
        removed = race_service.remove_by_id_for_slack_channel(
            race_id_text=str(race_id),
            slack_team_id=DEV_SLACK_TEAM_ID,
            slack_channel_id=DEV_SLACK_CHANNEL_ID,
        )
    except InvalidRaceIdError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"race_id={race_id} was not found",
        )

    return {
        "removed": True,
        "race_id": race_id,
    }


def _register_dev_race(*, url: str, db: Session) -> dict[str, object]:
    race_service = RaceService(db)
    try:
        race = race_service.register_from_url(
            url=url,
            slack_team_id=DEV_SLACK_TEAM_ID,
            slack_channel_id=DEV_SLACK_CHANNEL_ID,
            registered_by=DEV_REGISTERED_BY,
        )
    except InvalidRaceUrlError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return _serialize_race(race)


def _serialize_race(race: object) -> dict[str, object]:
    entry_start_at = getattr(race, "entry_start_at")
    entry_deadline = getattr(race, "entry_deadline")
    last_checked_at = getattr(race, "last_checked_at")

    return {
        "id": getattr(race, "id"),
        "title": getattr(race, "title"),
        "url": getattr(race, "url"),
        "source_domain": getattr(race, "source_domain"),
        "page_status": getattr(race, "page_status"),
        "entry_start_at": entry_start_at.isoformat() if entry_start_at else None,
        "entry_deadline": entry_deadline.isoformat() if entry_deadline else None,
        "entry_status": getattr(race, "entry_status"),
        "last_checked_at": last_checked_at.isoformat() if last_checked_at else None,
        "last_extraction_method": getattr(race, "last_extraction_method"),
        "last_detected_text": getattr(race, "last_detected_text"),
    }
