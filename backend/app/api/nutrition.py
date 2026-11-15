from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import httpx

from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import CommunityFood, MealEntry, SavedRecipe, User

router = APIRouter(prefix="/nutrition", tags=["nutrition"])


class CatalogFoodInput(BaseModel):
    barcode: str = Field(pattern=r"^\d{8,14}$")
    name: str = Field(min_length=1, max_length=200)
    brand: str = Field(default="", max_length=120)
    kcal: float = Field(ge=0, le=1000)
    protein: float = Field(ge=0, le=100)
    carbs: float = Field(ge=0, le=100)
    fat: float = Field(ge=0, le=100)


def catalog_food(row: CommunityFood):
    return {"found": True, "source": "community", "product": {
        "code": row.barcode, "product_name": row.name, "brands": row.brand,
        "nutriments": {"energy-kcal_100g": row.kcal, "proteins_100g": row.protein,
                       "carbohydrates_100g": row.carbs, "fat_100g": row.fat}}}


@router.get("/catalog/{barcode}")
def lookup_catalog_food(barcode: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    code = "".join(ch for ch in barcode if ch.isdigit())
    if not 8 <= len(code) <= 14:
        raise HTTPException(422, "Enter a barcode with 8 to 14 digits")
    # Variable-measure EAN-13 (prefix 2): first seven digits identify the PLU;
    # the remaining digits encode weight/price and the check digit.
    weighted = len(code) == 13 and code.startswith("2")
    if weighted:
        row = db.query(CommunityFood).filter(CommunityFood.barcode_prefix == code[:7]).first()
        if row:
            result = catalog_food(row)
            result["matched_by"] = "weight_prefix"
            return result
    row = db.query(CommunityFood).filter(CommunityFood.barcode == code).first()
    if row:
        return catalog_food(row)
    try:
        response = httpx.get(
            f"https://world.openfoodfacts.org/api/v2/product/{code}.json",
            params={"fields": "code,product_name,product_name_ru,brands,nutriments"},
            headers={"User-Agent": "TelegramPersonalHub/1.0 (food barcode lookup)"}, timeout=8.0)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Open Food Facts is temporarily unavailable")
    if result.get("status") != 1 or not result.get("product"):
        return {"found": False, "source": "openfoodfacts", "barcode": code,
                "weighted": weighted, "barcode_prefix": code[:7] if weighted else None}
    product = result["product"]
    product["source"] = "openfoodfacts"
    return {"found": True, "source": "openfoodfacts", "product": product,
            "weighted": weighted, "barcode_prefix": code[:7] if weighted else None}


@router.post("/catalog", status_code=201)
def add_catalog_food(payload: CatalogFoodInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    barcode = payload.barcode
    prefix = barcode[:7] if len(barcode) == 13 and barcode.startswith("2") else None
    row = db.query(CommunityFood).filter(CommunityFood.barcode == barcode).first()
    if row:
        if row.submitted_by != user.id:
            raise HTTPException(409, "This barcode is already in the shared catalog")
        for field in ("name", "brand", "kcal", "protein", "carbs", "fat"):
            setattr(row, field, getattr(payload, field))
        db.commit()
        db.refresh(row)
        return catalog_food(row)

    row = CommunityFood(barcode=barcode, barcode_prefix=prefix, name=payload.name.strip(),
                        brand=payload.brand.strip(), kcal=payload.kcal, protein=payload.protein,
                        carbs=payload.carbs, fat=payload.fat, submitted_by=user.id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Another user may have inserted the same barcode between the lookup
        # and the commit. Only a confirmed duplicate is a 409; any other
        # constraint failure stays visible.
        db.rollback()
        existing = db.query(CommunityFood).filter(CommunityFood.barcode == barcode).first()
        if existing is None:
            raise
        raise HTTPException(409, "This barcode is already in the shared catalog") from None
    db.refresh(row)
    return catalog_food(row)


@router.get("/catalog-search")
def search_food_catalog(q: str = Query(min_length=2, max_length=80), user: User = Depends(current_user)):
    try:
        response = httpx.get("https://world.openfoodfacts.org/api/v2/search",
                             params={"search_terms": q, "page_size": 20,
                                     "fields": "code,product_name,product_name_ru,brands,nutriments"},
                             headers={"User-Agent": "TelegramPersonalHub/1.0 (food search)"}, timeout=8.0)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Open Food Facts search is temporarily unavailable")
    return [{"source": "openfoodfacts", **product} for product in data.get("products", [])]


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
                    serving_grams=payload.serving_grams, base_serving_grams=payload.serving_grams,
                    base_calories=payload.calories, base_protein=payload.protein,
                    base_carbs=payload.carbs, base_fat=payload.fat)
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize(row)


@router.patch("/{meal_id}")
def update_portion(meal_id: int, payload: PortionInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.query(MealEntry).filter(MealEntry.id == meal_id, MealEntry.user_id == user.id).first()
    if row is None:
        raise HTTPException(404, "Meal not found")
    ratio = payload.serving_grams / max(row.base_serving_grams, 1)
    row.calories = round(row.base_calories * ratio)
    row.protein = round(row.base_protein * ratio, 1)
    row.carbs = round(row.base_carbs * ratio, 1)
    row.fat = round(row.base_fat * ratio, 1)
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


class CopyDayInput(BaseModel):
    source_day: date
    target_day: date


@router.post("/copy-day", status_code=201)
def copy_day(payload: CopyDayInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if payload.source_day == payload.target_day:
        raise HTTPException(400, "Choose a different destination day")
    source = db.query(MealEntry).filter(MealEntry.user_id == user.id, MealEntry.day == payload.source_day.isoformat()).all()
    if not source:
        raise HTTPException(404, "No meals found on the source day")
    copied = [MealEntry(user_id=user.id, day=payload.target_day.isoformat(), meal_type=m.meal_type,
                        name=m.name, calories=m.calories, protein=m.protein, carbs=m.carbs,
                        fat=m.fat, ingredients=m.ingredients, serving_grams=m.serving_grams,
                        base_serving_grams=m.base_serving_grams, base_calories=m.base_calories,
                        base_protein=m.base_protein, base_carbs=m.base_carbs, base_fat=m.base_fat) for m in source]
    db.add_all(copied)
    db.commit()
    return {"copied": len(copied), "day": payload.target_day.isoformat()}


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
