from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import FocusSession, User

router=APIRouter(prefix="/focus",tags=["focus"])
class SessionInput(BaseModel):
    duration:int=Field(ge=1,le=180); mode:str; completed:bool=True; started_at:datetime; finished_at:datetime|None=None
@router.post("/sessions",status_code=201)
def create_session(body:SessionInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if body.mode not in {"focus","short_break","long_break"}: raise HTTPException(422,"Invalid mode")
    data=body.model_dump(); data["finished_at"]=data["finished_at"] or datetime.now(timezone.utc)
    s=FocusSession(user_id=user.id,**data); db.add(s); db.commit(); db.refresh(s); return {"id":s.id,"duration":s.duration,"mode":s.mode,"completed":s.completed,"started_at":s.started_at,"finished_at":s.finished_at}
@router.get("/sessions")
def sessions(limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),user:User=Depends(current_user)):
    return [{"id":s.id,"duration":s.duration,"mode":s.mode,"completed":s.completed,"started_at":s.started_at,"finished_at":s.finished_at} for s in db.query(FocusSession).filter_by(user_id=user.id).order_by(FocusSession.finished_at.desc()).limit(limit)]
@router.get("/stats")
def stats(db:Session=Depends(get_db),user:User=Depends(current_user)):
    rows=db.query(FocusSession).filter_by(user_id=user.id,mode="focus",completed=True).all()
    return {"sessions":len(rows),"focus_minutes":sum(s.duration for s in rows),"history":[{"date":s.finished_at,"duration":s.duration} for s in rows[-30:]]}
