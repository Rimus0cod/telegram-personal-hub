import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import auth, focus, notes, tasks, weather, nutrition
from app.core.rate_limit import enforce_rate_limit
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# Schema changes are applied exclusively by Alembic (`alembic upgrade head`).
# The application must not create or patch tables at import time: a fresh
# process, a reload and multiple workers would otherwise race each other and
# could leave databases at the same revision with different schemas.
app=FastAPI(title="Telegram Personal Hub API",version="1.0.0",description="Personal tools authenticated with Telegram Mini App initData.")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
for module in (auth,weather,notes,tasks,focus,nutrition): app.include_router(module.router,prefix="/api/v1",dependencies=[Depends(enforce_rate_limit)])
@app.get("/health",tags=["health"])
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        # Never return or log driver details: they can contain host names or
        # database names. The Docker healthcheck only needs a boolean answer.
        logger.warning("Database health check failed (%s)", type(error).__name__)
        raise HTTPException(503, "Database unavailable") from None
    return {"status":"ok"}
