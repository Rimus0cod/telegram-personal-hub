from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import Task, User

router=APIRouter(prefix="/tasks",tags=["tasks"])
class TaskInput(BaseModel):
    title:str=Field(min_length=1,max_length=200); description:str=Field(default="",max_length=10000)
    completed:bool=False; priority:str="medium"; due_date:datetime|None=None
    def validate_priority(self):
        if self.priority not in {"low","medium","high"}: raise ValueError("priority must be low, medium, or high")
class TaskPatch(BaseModel):
    title:str|None=Field(default=None,min_length=1,max_length=200); description:str|None=Field(default=None,max_length=10000)
    completed:bool|None=None; priority:str|None=None; due_date:datetime|None=None
def output(t): return {"id":t.id,"title":t.title,"description":t.description,"completed":t.completed,"priority":t.priority,"due_date":t.due_date,"created_at":t.created_at,"updated_at":t.updated_at}
@router.get("")
def list_tasks(status:str=Query("all",pattern="^(all|active|completed)$"),sort:str=Query("created",pattern="^(created|due)$"),db:Session=Depends(get_db),user:User=Depends(current_user)):
    q=db.query(Task).filter(Task.user_id==user.id)
    if status=="active": q=q.filter(Task.completed.is_(False))
    if status=="completed": q=q.filter(Task.completed.is_(True))
    return [output(t) for t in q.order_by(Task.due_date.asc().nullslast() if sort=="due" else Task.created_at.desc()).all()]
@router.post("",status_code=201)
def create_task(body:TaskInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if body.priority not in {"low","medium","high"}: raise HTTPException(422,"Invalid priority")
    t=Task(user_id=user.id,**body.model_dump()); db.add(t); db.commit(); db.refresh(t); return output(t)
@router.get("/{task_id}")
def get_task(task_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    t=db.query(Task).filter_by(id=task_id,user_id=user.id).first()
    if not t: raise HTTPException(404,"Task not found")
    return output(t)
@router.patch("/{task_id}")
def update_task(task_id:int,body:TaskPatch,db:Session=Depends(get_db),user:User=Depends(current_user)):
    t=db.query(Task).filter_by(id=task_id,user_id=user.id).first()
    if not t: raise HTTPException(404,"Task not found")
    updates=body.model_dump(exclude_unset=True)
    if updates.get("priority") not in (None,"low","medium","high"): raise HTTPException(422,"Invalid priority")
    for k,v in updates.items(): setattr(t,k,v)
    t.updated_at=datetime.now(timezone.utc); db.commit(); db.refresh(t); return output(t)
@router.delete("/{task_id}",status_code=204)
def delete_task(task_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    t=db.query(Task).filter_by(id=task_id,user_id=user.id).first()
    if not t: raise HTTPException(404,"Task not found")
    db.delete(t); db.commit(); return Response(status_code=204)
