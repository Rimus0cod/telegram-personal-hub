from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import User
router=APIRouter(prefix="/auth",tags=["auth"])
@router.get("/me")
def me(user:User=Depends(current_user)):
    return {"id":user.id,"telegram_id":user.telegram_id,"username":user.username,"first_name":user.first_name,"last_name":user.last_name,"last_city":user.last_city,"calorie_target":user.calorie_target,"protein_target":user.protein_target,"carbs_target":user.carbs_target,"fat_target":user.fat_target}

class PreferencesInput(BaseModel):
    calorie_target: int = Field(ge=500, le=10000)
    protein_target: int = Field(default=120, ge=0, le=1000)
    carbs_target: int = Field(default=250, ge=0, le=1500)
    fat_target: int = Field(default=70, ge=0, le=500)

@router.patch("/preferences")
def update_preferences(payload:PreferencesInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
    user.calorie_target=payload.calorie_target
    user.protein_target=payload.protein_target
    user.carbs_target=payload.carbs_target
    user.fat_target=payload.fat_target
    db.commit()
    return {"calorie_target":user.calorie_target,"protein_target":user.protein_target,"carbs_target":user.carbs_target,"fat_target":user.fat_target}
