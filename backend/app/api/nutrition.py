from datetime import date
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import httpx

from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import (
    CatalogItem,
    CommunityFood,
    FoodBarcode,
    FoodSource,
    MealEntry,
    SavedRecipe,
    User,
)
from app.services.food_search import normalize_text, search_catalog as search_foods

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


_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,8}(?:[_-][A-Za-z0-9]{1,8})*$")


def normalize_language_tag(value: str) -> str:
    """Lowercase a BCP-47-like tag; reject anything that is not a language tag.

    Region and script subtags are kept, so ``ru-RU`` and ``zh-Hans`` remain
    valid.  Lookup falls back to the primary subtag, which keeps stored short
    codes working without adding per-language columns.
    """
    tag = str(value or "").strip().replace("_", "-")
    if not _LANGUAGE_TAG.match(tag):
        raise HTTPException(422, "lang must be a language tag such as ru, en-US or zh-Hans")
    return tag.lower()


def _translation_map(row: CatalogItem):
    return {translation.language.lower(): translation for translation in row.translations}


def _choose_translation(row: CatalogItem, language: str):
    translations = _translation_map(row)
    requested = language.lower()
    candidates = [requested]
    primary = requested.split("-")[0]
    if primary != requested:
        candidates.append(primary)
    candidates.extend(["en", "ru", "uk"])
    for code in candidates:
        if code in translations:
            return translations[code]
    primary = next((item for item in row.translations if item.is_primary), None)
    return primary or (row.translations[0] if row.translations else None)


def _primary_nutrient(row: CatalogItem):
    return next(
        (profile for profile in row.nutrient_profiles if profile.is_primary),
        row.nutrient_profiles[0] if row.nutrient_profiles else None,
    )


def _default_serving(row: CatalogItem):
    return next(
        (serving for serving in row.servings if serving.is_default),
        row.servings[0] if row.servings else None,
    )


def _search_terms(row: CatalogItem, chosen_name: str) -> list[str]:
    values = [chosen_name, row.name, row.name_original or ""]
    values.extend(translation.name for translation in row.translations)
    values.extend(alias.alias for alias in row.aliases)
    return sorted({value.strip() for value in values if value and value.strip()})


def serialize_catalog_item(row: CatalogItem, language: str = "ru"):
    translation = _choose_translation(row, language)
    profile = _primary_nutrient(row)
    serving = _default_serving(row)
    barcode = next((item.barcode for item in row.barcodes if item.barcode), None)
    translations = _translation_map(row)
    name = translation.name if translation else (row.name_ru or row.name)
    display_language = translation.language.lower() if translation else "und"
    name_ru = translations["ru"].name if "ru" in translations else (row.name_ru or row.name)
    name_uk = translations["uk"].name if "uk" in translations else row.name_uk
    nutrients = {
        "reference_amount": profile.reference_amount if profile else 100,
        "reference_unit": profile.reference_unit if profile else "g",
        "kcal": profile.kcal if profile and profile.kcal is not None else row.kcal,
        "protein": profile.protein if profile and profile.protein is not None else row.protein,
        "carbs": profile.carbs if profile and profile.carbs is not None else row.carbs,
        "fat": profile.fat if profile and profile.fat is not None else row.fat,
        "fiber": profile.fiber if profile else None,
        "sugar": profile.sugar if profile else None,
        "sodium": profile.sodium if profile else None,
        "saturated_fat": profile.saturated_fat if profile else None,
    }
    serving_size = serving.grams if serving and serving.grams else (serving.amount if serving else row.serving_size)
    serving_unit = serving.unit if serving else row.serving_unit
    review_statuses = [
        status for status in (
            profile.review_status if profile else None,
            translation.review_status if translation else None,
        ) if status
    ]
    if "rejected" in review_statuses:
        review_status = "rejected"
    elif "pending" in review_statuses:
        review_status = "pending"
    else:
        review_status = "approved"
    return {
        "id": f"curated:{row.id}", "barcode": barcode,
        "source": row.source, "source_id": row.source_id,
        "name": name, "name_original": row.name_original or row.name,
        "name_ru": name_ru, "name_uk": name_uk,
        "display_language": display_language,
        "category": row.category, "type": row.type,
        "kcal": nutrients["kcal"], "protein": nutrients["protein"],
        "carbs": nutrients["carbs"], "fat": nutrients["fat"],
        "nutrients": nutrients,
        "serving_size": serving_size, "serving_unit": serving_unit,
        "servings": [
            {
                "amount": item.amount, "unit": item.unit, "grams": item.grams,
                "description": item.description, "is_default": bool(item.is_default),
            }
            for item in row.servings
        ],
        "translations": {
            code: {
                "name": item.name, "description": item.description,
                "translation_source": item.translation_source,
                "translation_status": item.translation_status,
                "review_status": item.review_status,
                "is_primary": bool(item.is_primary),
            }
            for code, item in translations.items()
        },
        "aliases": [alias.alias for alias in row.aliases],
        "search_terms": _search_terms(row, name),
        "preparation_state": row.preparation_state,
        "approximate": bool(row.approximate), "source_url": row.source_url,
        "review_status": review_status,
    }


def serialize_community_catalog_item(row: CommunityFood):
    return {
        "id": f"community:{row.id}", "barcode": row.barcode,
        "source": "community", "source_id": row.barcode,
        "name": row.name, "name_original": row.name, "name_ru": row.name,
        "name_uk": None, "display_language": "und", "brand": row.brand,
        "category": "Общий каталог", "type": "ingredient",
        "kcal": row.kcal, "protein": row.protein,
        "carbs": row.carbs, "fat": row.fat,
        "nutrients": {
            "reference_amount": 100, "reference_unit": "g",
            "kcal": row.kcal, "protein": row.protein,
            "carbs": row.carbs, "fat": row.fat,
            "fiber": None, "sugar": None, "sodium": None, "saturated_fat": None,
        },
        "serving_size": 100, "serving_unit": "g",
        "servings": [{"amount": 100, "unit": "g", "grams": 100, "description": "100 g", "is_default": True}],
        "translations": {}, "aliases": [], "search_terms": [row.name],
        "preparation_state": None, "approximate": False, "source_url": None,
        "review_status": "approved",
    }


def catalog_product_from_item(row: CatalogItem, barcode: str, language: str = "ru"):
    translation = _choose_translation(row, language)
    profile = _primary_nutrient(row)
    name = translation.name if translation else (row.name_ru or row.name)
    kcal = profile.kcal if profile and profile.kcal is not None else row.kcal
    protein = profile.protein if profile and profile.protein is not None else row.protein
    carbs = profile.carbs if profile and profile.carbs is not None else row.carbs
    fat = profile.fat if profile and profile.fat is not None else row.fat
    return {
        "code": barcode, "product_name": name, "brands": "",
        "nutriments": {
            "energy-kcal_100g": kcal, "proteins_100g": protein,
            "carbohydrates_100g": carbs, "fat_100g": fat,
        },
        "source": "catalog", "catalog_food_id": row.id, "type": row.type,
    }


@router.get("/catalog-items")
def list_catalog_items(
    limit: int = Query(1000, ge=1, le=2000),
    language: str = Query("ru", alias="lang", min_length=2, max_length=16),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Return the shared catalog without exposing submitter identities.

    Curated foods come from ``catalog_foods``; barcode products saved by users
    come from ``community_foods``. Diary entries remain user-scoped elsewhere.
    """
    language = normalize_language_tag(language)
    curated = (db.query(CatalogItem).filter(CatalogItem.is_active.is_(True))
               .order_by(CatalogItem.type.desc(), CatalogItem.name)
               .limit(limit).all())
    community = db.query(CommunityFood).order_by(CommunityFood.name).limit(limit).all()
    items = ([serialize_catalog_item(row, language) for row in curated]
             + [serialize_community_catalog_item(row) for row in community])
    return {"items": items, "count": len(items), "language": language}


@router.get("/food-sources")
def list_food_sources(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Return the public source/license registry without provider secrets."""
    rows = db.query(FoodSource).order_by(FoodSource.code).all()
    return [
        {
            "code": row.code, "name": row.name, "data_types": row.data_types,
            "acquisition_method": row.acquisition_method, "license": row.license,
            "attribution_required": bool(row.attribution_required),
            "storage_permission": bool(row.storage_permission),
            "translation_permission": bool(row.translation_permission),
            "redistribution_permission": bool(row.redistribution_permission),
            "approval_status": row.approval_status, "limitations": row.limitations,
        }
        for row in rows
    ]


@router.get("/catalog/search")
def search_catalog_foods(
    q: str = Query(min_length=1, max_length=80),
    limit: int = Query(50, ge=1, le=200),
    language: str = Query("ru", alias="lang", min_length=2, max_length=16),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Ranked multilingual search over the PostgreSQL catalog.

    Queries canonical names, approved translations, aliases and generated
    transliterations.  ``lang`` only affects ranking preference; it never limits
    results to one language.  Nutrients and canonical ids are not modified.
    """
    language = normalize_language_tag(language)
    hits = search_foods(db, q, language=language, limit=limit)
    items = [
        serialize_catalog_item(hit.item, language)
        if hit.kind == "curated"
        else serialize_community_catalog_item(hit.item)
        for hit in hits
    ]
    return {
        "items": items,
        "count": len(items),
        "language": language,
        "query": q.strip(),
    }


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
    catalog_barcode = db.query(FoodBarcode).filter(FoodBarcode.barcode == code).first()
    if catalog_barcode:
        catalog_item = db.get(CatalogItem, catalog_barcode.food_id)
        if catalog_item is not None and catalog_item.is_active:
            return {
                "found": True, "source": "catalog",
                "product": catalog_product_from_item(catalog_item, code),
            }
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
        row.normalized_name = normalize_text(row.name)[:200]
        db.commit()
        db.refresh(row)
        return catalog_food(row)

    row = CommunityFood(barcode=barcode, barcode_prefix=prefix, name=payload.name.strip(),
                        normalized_name=normalize_text(payload.name)[:200],
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


# AI assistance: DeepSeek helped implement and debug the serving-size and macro recalculation.
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
