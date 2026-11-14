from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import MealEntry, SavedRecipe, User

router = APIRouter(prefix="/nutrition", tags=["nutrition"])


class MealInput(BaseModel):
    day: date
    meal_type: str = Field(pattern="^(breakfast|lunch|dinner|snack)$")
    name: str = Field(min_length=1, max_length=160)
    calories: int = Field(ge=0, le=20000)
    protein: float = Field(default=0, ge=0, le=2000)
    carbs: float = Field(default=0, ge=0, le=2000)
    fat: float = Field(default=0, ge=0, le=2000)
    ingredients: str = Field(default="", max_length=4000)
    serving_grams: int = Field(default=100, ge=1, le=10000)


class IngredientInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="Другое", max_length=50)
    kcal: float = Field(ge=0, le=10000)
    p: float = Field(default=0, ge=0, le=1000)
    c: float = Field(default=0, ge=0, le=1000)
    f: float = Field(default=0, ge=0, le=1000)
    grams: int = Field(ge=1, le=10000)


class RecipeInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    meal_type: str = Field(pattern="^(breakfast|lunch|dinner|snack)$")
    ingredients: list[IngredientInput] = Field(min_length=1, max_length=100)


class PortionInput(BaseModel):
    serving_grams: int = Field(ge=1, le=10000)


def serialize(meal: MealEntry):
    return {"id": meal.id, "day": meal.day, "meal_type": meal.meal_type, "name": meal.name,
            "calories": meal.calories, "protein": meal.protein, "carbs": meal.carbs,
            "fat": meal.fat, "ingredients": meal.ingredients, "serving_grams": meal.serving_grams}


@router.get("")
def list_meals(start: date = Query(alias="from"), end: date = Query(alias="to"),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    if (end - start).days > 13 or end < start:
        raise HTTPException(400, "Date range must be between 1 and 14 days")
    rows = (db.query(MealEntry).filter(MealEntry.user_id == user.id,
            MealEntry.day >= start.isoformat(), MealEntry.day <= end.isoformat())
            .order_by(MealEntry.created_at).all())
    return [serialize(row) for row in rows]


@router.post("", status_code=201)
def add_meal(payload: MealInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = MealEntry(user_id=user.id, day=payload.day.isoformat(), meal_type=payload.meal_type,
                    name=payload.name.strip(), calories=payload.calories, protein=payload.protein,
                    carbs=payload.carbs, fat=payload.fat, ingredients=payload.ingredients,
                    serving_grams=payload.serving_grams)
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize(row)


@router.patch("/{meal_id}")
def update_portion(meal_id: int, payload: PortionInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.query(MealEntry).filter(MealEntry.id == meal_id, MealEntry.user_id == user.id).first()
    if row is None:
        raise HTTPException(404, "Meal not found")
    ratio = payload.serving_grams / max(row.serving_grams, 1)
    row.calories = round(row.calories * ratio)
    row.protein = round(row.protein * ratio, 1)
    row.carbs = round(row.carbs * ratio, 1)
    row.fat = round(row.fat * ratio, 1)
    row.serving_grams = payload.serving_grams
    db.commit()
    db.refresh(row)
    return serialize(row)


@router.delete("/{meal_id}", status_code=204)
def delete_meal(meal_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.query(MealEntry).filter(MealEntry.id == meal_id, MealEntry.user_id == user.id).first()
    if row is None:
        raise HTTPException(404, "Meal not found")
    db.delete(row)
    db.commit()


@router.get("/recipes")
def list_recipes(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [{"id": r.id, "name": r.name, "meal_type": r.meal_type, "ingredients": r.ingredients}
            for r in db.query(SavedRecipe).filter(SavedRecipe.user_id == user.id).order_by(SavedRecipe.name).all()]


@router.post("/recipes", status_code=201)
def save_recipe(payload: RecipeInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    existing = db.query(SavedRecipe).filter(SavedRecipe.user_id == user.id, SavedRecipe.name == payload.name.strip()).first()
    if existing:
        existing.meal_type = payload.meal_type
        existing.ingredients = [i.model_dump() for i in payload.ingredients]
        row = existing
    else:
        row = SavedRecipe(user_id=user.id, name=payload.name.strip(), meal_type=payload.meal_type,
                          ingredients=[i.model_dump() for i in payload.ingredients])
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "meal_type": row.meal_type, "ingredients": row.ingredients}


@router.delete("/recipes/{recipe_id}", status_code=204)
def delete_recipe(recipe_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.query(SavedRecipe).filter(SavedRecipe.id == recipe_id, SavedRecipe.user_id == user.id).first()
    if row is None:
        raise HTTPException(404, "Recipe not found")
    db.delete(row)
    db.commit()
