from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.core.config import get_env, is_local_env
from backend.app.core.database import get_db
from backend.app.services.deadline_check_service import DeadlineCheckService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/check-deadlines")
def check_deadlines(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    if not is_local_env():
        job_secret = get_env("JOB_SECRET")
        if not job_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JOB_SECRET is not configured",
            )

        if authorization != f"Bearer {job_secret}":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid job authorization",
            )

    summary = DeadlineCheckService(db).check_all()
    return {
        "checked_count": summary.checked_count,
        "updated_count": summary.updated_count,
        "notified_count": summary.notified_count,
        "failed_count": summary.failed_count,
        "html_count": summary.html_count,
        "llm_count": summary.llm_count,
    }
