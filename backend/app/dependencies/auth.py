from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import User
from app.services.telegram_auth import validate_init_data

def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("tma "): raise HTTPException(401, "Telegram initData required")
    data = validate_init_data(authorization[4:])
    user = db.query(User).filter(User.telegram_id == data["id"]).first()
    profile = dict(telegram_id=data["id"], username=data.get("username"), first_name=data.get("first_name", "Friend"), last_name=data.get("last_name"))
    if user is None:
        user = User(**profile); db.add(user)
    else:
        for key, value in profile.items():
            if key != "telegram_id": setattr(user, key, value)
    db.commit(); db.refresh(user)
    return user
