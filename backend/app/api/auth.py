from fastapi import APIRouter, Depends
from app.dependencies.auth import current_user
from app.models.models import User
router=APIRouter(prefix="/auth",tags=["auth"])
@router.get("/me")
def me(user:User=Depends(current_user)):
    return {"id":user.id,"telegram_id":user.telegram_id,"username":user.username,"first_name":user.first_name,"last_name":user.last_name,"last_city":user.last_city}
