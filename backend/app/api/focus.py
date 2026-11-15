from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import FocusSession, User

router = APIRouter(prefix="/focus", tags=["focus"])

VALID_MODES = {"focus", "short_break", "long_break"}

# Validation windows. They are deliberately generous enough for real devices
# with slightly skewed clocks, but stop obviously forged sessions.
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_AGE = timedelta(days=7)
MAX_SESSION_SPAN = timedelta(hours=24)
# A pause can only make the wall-clock interval longer than the recorded focus
# duration, never shorter. This tolerance therefore only guards the lower edge.
DURATION_TOLERANCE = timedelta(minutes=1)


class SessionInput(BaseModel):
    duration: int = Field(ge=1, le=180)
    mode: str
    completed: bool = True
    started_at: datetime
    finished_at: datetime | None = None
    # Optional for backward compatibility with clients released before 4.4.
    client_id: UUID | None = None


def _normalize(value: datetime) -> datetime:
    """Return an aware UTC datetime; SQLite loses the offset on round-trip."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _validate_session_times(
    started_at: datetime,
    finished_at: datetime,
    duration_minutes: int,
) -> None:
    """Validate the business windows of a brand-new session.

    Both values must already be normalized to UTC. Existing sessions are never
    re-validated: an old session that was legitimately stored must stay
    replayable with its ``client_id`` (see ``create_session``).
    """
    now = datetime.now(timezone.utc)

    if finished_at < started_at:
        raise HTTPException(422, "finished_at must not be earlier than started_at")
    if started_at > now + FUTURE_TOLERANCE:
        raise HTTPException(422, "started_at is too far in the future")
    if started_at < now - MAX_AGE:
        raise HTTPException(422, "started_at is older than 7 days")
    if finished_at - started_at > MAX_SESSION_SPAN:
        raise HTTPException(422, "The session interval exceeds 24 hours")
    if (
        finished_at - started_at + DURATION_TOLERANCE
        < timedelta(minutes=duration_minutes)
    ):
        raise HTTPException(422, "duration does not match the session timestamps")


def _find_session(db: Session, user_id: int, client_id: str) -> FocusSession | None:
    return (
        db.query(FocusSession)
        .filter(FocusSession.user_id == user_id, FocusSession.client_id == client_id)
        .first()
    )


def _assert_same_payload(
    existing: FocusSession,
    body: SessionInput,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Reusing a client_id is only safe for the exact same session.

    ``started_at``/``finished_at`` are the already-normalized UTC values of the
    incoming request, so the comparison does not depend on the database driver
    preserving timezone information (SQLite does not).
    """
    same = (
        existing.duration == body.duration
        and existing.mode == body.mode
        and existing.completed == body.completed
        and _normalize(existing.started_at) == started_at
    )
    if body.finished_at is not None:
        same = same and _normalize(existing.finished_at) == finished_at
    if not same:
        raise HTTPException(
            409,
            "client_id was already used for a different session",
        )


def _response(session: FocusSession) -> dict:
    return {
        "id": session.id,
        "duration": session.duration,
        "mode": session.mode,
        "completed": session.completed,
        "started_at": _normalize(session.started_at),
        "finished_at": _normalize(session.finished_at),
        "client_id": session.client_id,
    }


@router.post("/sessions", status_code=201)
def create_session(
    body: SessionInput,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if body.mode not in VALID_MODES:
        raise HTTPException(422, "Invalid mode")

    # Normalize before anything touches the database. SQLite silently drops the
    # offset of a stored ``datetime`` (``06:23:25+05:00`` becomes ``06:23:25``),
    # which would shift the stored instant and make a byte-identical retry look
    # like a different session. Storing UTC keeps SQLite and PostgreSQL aligned.
    started_at = _require_aware(body.started_at, "started_at")
    finished_at = (
        _require_aware(body.finished_at, "finished_at")
        if body.finished_at is not None
        else datetime.now(timezone.utc)
    )
    client_id = str(body.client_id) if body.client_id is not None else None

    # The idempotency lookup runs before the business windows on purpose: a
    # session that is already stored must stay replayable with the same
    # client_id even after it became "too old" to create. Otherwise a queued
    # retry that outlived MAX_AGE could never be resolved.
    if client_id is not None:
        existing = _find_session(db, user.id, client_id)
        if existing is not None:
            _assert_same_payload(existing, body, started_at, finished_at)
            return _response(existing)

    # Only a genuinely new session is subject to the validation windows.
    _validate_session_times(started_at, finished_at, body.duration)

    session = FocusSession(
        user_id=user.id,
        duration=body.duration,
        mode=body.mode,
        completed=body.completed,
        started_at=started_at,
        finished_at=finished_at,
        client_id=client_id,
    )
    db.add(session)
    try:
        db.commit()
    except IntegrityError:
        # The unique (user_id, client_id) index is the final race guard: another
        # request committed the same session between our lookup and our insert.
        db.rollback()
        if client_id is None:
            raise HTTPException(500, "Could not save the focus session")
        existing = _find_session(db, user.id, client_id)
        if existing is None:
            raise HTTPException(500, "Could not save the focus session")
        _assert_same_payload(existing, body, started_at, finished_at)
        return _response(existing)

    db.refresh(session)
    return _response(session)


@router.get("/sessions")
def sessions(
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return [
        {
            "id": s.id,
            "duration": s.duration,
            "mode": s.mode,
            "completed": s.completed,
            "started_at": _normalize(s.started_at),
            "finished_at": _normalize(s.finished_at),
        }
        for s in db.query(FocusSession)
        .filter_by(user_id=user.id)
        .order_by(FocusSession.finished_at.desc())
        .limit(limit)
    ]


@router.get("/stats")
def stats(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = (
        db.query(FocusSession)
        .filter_by(user_id=user.id, mode="focus", completed=True)
        .all()
    )
    return {
        "sessions": len(rows),
        "focus_minutes": sum(s.duration for s in rows),
        "history": [{"date": _normalize(s.finished_at), "duration": s.duration} for s in rows[-30:]],
    }
