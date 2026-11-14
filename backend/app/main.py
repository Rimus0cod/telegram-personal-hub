from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from app.database import Base, engine
from app.api import auth, focus, notes, tasks, weather, nutrition

Base.metadata.create_all(bind=engine)
if "serving_grams" not in {column["name"] for column in inspect(engine).get_columns("meal_entries")}:
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE meal_entries ADD COLUMN serving_grams INTEGER NOT NULL DEFAULT 100"))
app=FastAPI(title="Telegram Personal Hub API",version="1.0.0",description="Personal tools authenticated with Telegram Mini App initData.")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
for module in (auth,weather,notes,tasks,focus,nutrition): app.include_router(module.router,prefix="/api/v1")
@app.get("/health",tags=["health"])
def health(): return {"status":"ok"}
