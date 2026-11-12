from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import Base, engine
from app.api import auth, focus, notes, tasks, weather

Base.metadata.create_all(bind=engine)
app=FastAPI(title="Telegram Personal Hub API",version="1.0.0",description="Personal tools authenticated with Telegram Mini App initData.")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
for module in (auth,weather,notes,tasks,focus): app.include_router(module.router,prefix="/api/v1")
@app.get("/health",tags=["health"])
def health(): return {"status":"ok"}
