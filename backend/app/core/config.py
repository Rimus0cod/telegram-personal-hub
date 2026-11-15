import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./hub.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:5173")
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "86400"))
# Empty disables rate limiting (local tests without Redis). Compose always
# provides an internal Redis URL.
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_TIMEOUT_SECONDS = float(os.getenv("REDIS_TIMEOUT_SECONDS", "0.25"))
