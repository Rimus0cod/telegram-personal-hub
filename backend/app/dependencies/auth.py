from fastapi import Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import User
from app.services.telegram_auth import validate_init_data

# Read the real column capacities so the policy cannot drift from the models.
_PROFILE_LIMITS = {
    name: User.__table__.columns[name].type.length
    for name in ("username", "first_name", "last_name")
}


def _profile(data: dict) -> dict:
    """Validate the Telegram fields that map to bounded user columns.

    Telegram limits these fields well below the column sizes, so a correct
    launch never reaches the rejection path. A malformed (but correctly signed)
    payload is rejected with 401 instead of reaching PostgreSQL and turning a
    string-too-long error into a 500. Values are never silently truncated.
    """
    first_name = data.get("first_name")
    if first_name is None or first_name == "":
        first_name = "Friend"
    username = data.get("username")
    last_name = data.get("last_name")

    if not isinstance(first_name, str):
        raise HTTPException(401, "Invalid Telegram user profile")
    if len(first_name) > _PROFILE_LIMITS["first_name"]:
        raise HTTPException(401, "Invalid Telegram user profile")

    for field, value in (("username", username), ("last_name", last_name)):
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > _PROFILE_LIMITS[field]:
            raise HTTPException(401, "Invalid Telegram user profile")

    return {
        "telegram_id": data["id"],
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
    }


def _apply_profile(user: User, profile: dict) -> bool:
    """Copy only changed fields; returns True when the row was modified."""
    changed = False
    for key, value in profile.items():
        if key == "telegram_id":
            continue
        if getattr(user, key) != value:
            setattr(user, key, value)
            changed = True
    return changed


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("tma "): raise HTTPException(401, "Telegram initData required")
    data = validate_init_data(authorization[4:])
    profile = _profile(data)
    user = db.query(User).filter(User.telegram_id == profile["telegram_id"]).first()

    if user is None:
        user = User(**profile)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # A parallel request may have created the same telegram_id first.
            # Only accept the conflict when the row is really there; otherwise
            # this is a different constraint failure and must stay visible.
            db.rollback()
            user = db.query(User).filter(User.telegram_id == profile["telegram_id"]).first()
            if user is None:
                raise
            if _apply_profile(user, profile):
                db.commit()
                db.refresh(user)
            return user
        db.refresh(user)
        return user

    if _apply_profile(user, profile):
        db.commit()
        db.refresh(user)
    return user
