import hashlib, hmac, json, time
from urllib.parse import parse_qsl
from fastapi import HTTPException
from app.core.config import BOT_TOKEN, INIT_DATA_MAX_AGE

def validate_init_data(init_data: str, bot_token: str | None = None) -> dict:
    token = bot_token if bot_token is not None else BOT_TOKEN
    if not token: raise HTTPException(503, "Telegram bot is not configured")
    try: fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError: raise HTTPException(401, "Invalid Telegram initData")
    received = fields.pop("hash", None)
    if not received: raise HTTPException(401, "Missing Telegram signature")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received): raise HTTPException(401, "Invalid Telegram signature")
    try:
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
    except (KeyError, ValueError, TypeError): raise HTTPException(401, "Incomplete Telegram initData")
    age = int(time.time()) - auth_date
    if age < -60 or age > INIT_DATA_MAX_AGE: raise HTTPException(401, "Telegram initData expired")
    if not isinstance(user.get("id"), int): raise HTTPException(401, "Invalid Telegram user")
    return user
