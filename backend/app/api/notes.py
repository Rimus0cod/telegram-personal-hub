from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import Note, User

router = APIRouter(prefix="/notes", tags=["notes"])
class NoteInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=20000)
class NotePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, max_length=20000)

    @field_validator("title", "content", mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        # An omitted field keeps its default; an explicit null would write NULL
        # into a NOT NULL column, so it is a validation error.
        if value is None:
            raise ValueError("field cannot be null")
        return value
def output(n): return {"id":n.id,"title":n.title,"content":n.content,"created_at":n.created_at,"updated_at":n.updated_at}
@router.get("")
def list_notes(q: str | None = Query(default=None, max_length=100), db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.query(Note).filter(Note.user_id == user.id)
    if q: rows = rows.filter(Note.title.ilike(f"%{q}%") | Note.content.ilike(f"%{q}%"))
    return [output(n) for n in rows.order_by(Note.updated_at.desc()).all()]
@router.post("", status_code=201)
def create_note(body: NoteInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n=Note(user_id=user.id, **body.model_dump()); db.add(n); db.commit(); db.refresh(n); return output(n)
@router.get("/{note_id}")
def get_note(note_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n=db.query(Note).filter_by(id=note_id,user_id=user.id).first()
    if not n: raise HTTPException(404,"Note not found")
    return output(n)
@router.patch("/{note_id}")
def update_note(note_id: int, body: NotePatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n=db.query(Note).filter_by(id=note_id,user_id=user.id).first()
    if not n: raise HTTPException(404,"Note not found")
    for k,v in body.model_dump(exclude_unset=True).items(): setattr(n,k,v)
    n.updated_at=datetime.now(timezone.utc); db.commit(); db.refresh(n); return output(n)
@router.delete("/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n=db.query(Note).filter_by(id=note_id,user_id=user.id).first()
    if not n: raise HTTPException(404,"Note not found")
    db.delete(n); db.commit(); return Response(status_code=204)
