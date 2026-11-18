"""Recipe composition and nutrient calculation for the import pipeline.

A recipe is a canonical ``catalog_foods`` row of type ``prepared_meal`` plus
``recipe_ingredients`` links and one ``recipe_details`` row.  Nutrients are
calculated from the referenced ingredients' primary profiles and the cooked
yield:

    nutrient_per_100g = sum(nutrient_i * grams_i * edible_portion_i / 100)
                        / cooked_weight_grams * 100

Conventions:

* amounts are grams; ``edible_portion`` is a fraction (default 1.0);
* ``preparation_state`` records whether the amount is raw or cooked;
* optional nutrients are only aggregated when *every* ingredient has a value,
  otherwise they stay ``None`` (never zero-filled);
* a recipe is ``approximate`` when its yield quality is estimated or any
  ingredient is approximate;
* ingredient nutrient values are snapshotted so an old recipe keeps provenance.

Nothing here writes to the session; ``tools.catalog_import`` applies the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import CatalogItem, NutrientProfile

REQUIRED_MACROS = ("kcal", "protein", "carbs", "fat")
OPTIONAL_MACROS = ("fiber", "sugar", "sodium", "saturated_fat")
ALL_NUTRIENTS = REQUIRED_MACROS + OPTIONAL_MACROS
VALID_STATES = {"raw", "cooked"}
VALID_QUALITY = {"measured", "estimated"}
MAX_INGREDIENTS = 50
MIN_YIELD_RATIO = 0.3
MAX_YIELD_RATIO = 3.0
MAX_KCAL = 950.0
MAX_MACRO = 100.0
MAX_SODIUM = 40_000.0


class RecipeValidationError(ValueError):
    """One recipe is missing data or has physically impossible values."""


@dataclass(frozen=True)
class RecipeLine:
    ingredient_food_id: int
    ingredient_name: str
    grams: float
    edible_portion: float
    preparation_state: str | None
    nutrients: dict[str, float | None]
    snapshot: dict[str, float | None]


@dataclass(frozen=True)
class PreparedRecipe:
    nutrients: dict[str, float | None]
    approximate: bool
    lines: tuple[RecipeLine, ...]
    details: dict[str, Any]


def _number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RecipeValidationError(f"{field} must be a number") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise RecipeValidationError(
            f"{field} must be between {minimum:g} and {maximum:g}"
        )
    return number


def _ingredient_nutrients(session: Session, food: CatalogItem) -> dict[str, float | None]:
    profile = (
        session.query(NutrientProfile)
        .filter_by(food_id=food.id, is_primary=True)
        .first()
    )
    if profile is None:
        profile = session.query(NutrientProfile).filter_by(food_id=food.id).first()
    if profile is not None:
        values = {field: getattr(profile, field) for field in ALL_NUTRIENTS}
    else:
        values = {field: getattr(food, field, None) for field in REQUIRED_MACROS}
        values.update({field: None for field in OPTIONAL_MACROS})
    missing = [field for field in REQUIRED_MACROS if values[field] is None]
    if missing:
        raise RecipeValidationError(
            f"ingredient {food.name!r} has no {', '.join(missing)} value"
        )
    for field in REQUIRED_MACROS:
        if values[field] < 0:
            raise RecipeValidationError(
                f"ingredient {food.name!r} has a negative {field}"
            )
    return values


def _resolve_line(session: Session, raw: dict[str, Any], index: int) -> RecipeLine:
    if not isinstance(raw, dict):
        raise RecipeValidationError(f"ingredient {index} must be an object")
    source = str(raw.get("source") or raw.get("food_source") or "").strip()
    source_id = str(raw.get("source_id") or raw.get("food_source_id") or "").strip()
    if not source or not source_id:
        raise RecipeValidationError(
            f"ingredient {index} needs source and source_id"
        )
    food = (
        session.query(CatalogItem)
        .filter_by(source=source, source_id=source_id)
        .one_or_none()
    )
    if food is None:
        raise RecipeValidationError(
            f"ingredient {index} references unknown food {source}:{source_id}"
        )
    if not food.is_active:
        raise RecipeValidationError(
            f"ingredient {index} references inactive food {source}:{source_id}"
        )
    grams = _number(raw.get("grams"), f"ingredient {index} grams", minimum=0.1, maximum=20_000)
    edible = _number(
        raw.get("edible_portion", 1.0),
        f"ingredient {index} edible_portion",
        minimum=0.01, maximum=1.0,
    )
    state = raw.get("preparation_state")
    if state is not None:
        state = str(state).strip().lower()
        if state not in VALID_STATES:
            raise RecipeValidationError(
                f"ingredient {index} preparation_state must be raw or cooked"
            )
    nutrients = _ingredient_nutrients(session, food)
    return RecipeLine(
        ingredient_food_id=food.id,
        ingredient_name=food.name[:200],
        grams=round(grams, 3),
        edible_portion=round(edible, 4),
        preparation_state=state,
        nutrients=nutrients,
        snapshot={field: nutrients[field] for field in ALL_NUTRIENTS},
    )


def prepare_recipe(
    session: Session,
    recipe: dict[str, Any],
    *,
    dataset_version: str | None,
) -> PreparedRecipe:
    """Validate a recipe block and calculate its per-100-g nutrients."""
    if not isinstance(recipe, dict):
        raise RecipeValidationError("recipe must be an object")
    raw_ingredients = recipe.get("ingredients")
    if not isinstance(raw_ingredients, list) or not raw_ingredients:
        raise RecipeValidationError("recipe.ingredients must be a non-empty array")
    if len(raw_ingredients) > MAX_INGREDIENTS:
        raise RecipeValidationError(
            f"recipe.ingredients must contain at most {MAX_INGREDIENTS} items"
        )
    cooked_weight = _number(
        recipe.get("cooked_weight_grams"),
        "recipe.cooked_weight_grams", minimum=1, maximum=200_000,
    )
    portion_count = recipe.get("portion_count")
    if portion_count is not None:
        portion_count = int(
            _number(portion_count, "recipe.portion_count", minimum=1, maximum=100)
        )
    quality = str(recipe.get("quality") or "estimated").strip().lower()
    if quality not in VALID_QUALITY:
        raise RecipeValidationError("recipe.quality must be measured or estimated")

    lines = tuple(
        _resolve_line(session, raw, index)
        for index, raw in enumerate(raw_ingredients, start=1)
    )
    edible_mass = sum(line.grams * line.edible_portion for line in lines)
    ratio = cooked_weight / edible_mass
    if not MIN_YIELD_RATIO <= ratio <= MAX_YIELD_RATIO:
        raise RecipeValidationError(
            "cooked weight is implausible for the ingredient mass "
            f"(yield ratio {ratio:.2f}, expected {MIN_YIELD_RATIO}-{MAX_YIELD_RATIO})"
        )

    totals: dict[str, float | None] = {}
    for field in ALL_NUTRIENTS:
        values = [line.nutrients[field] for line in lines]
        if any(value is None for value in values):
            totals[field] = None
            continue
        totals[field] = sum(
            values[index] * lines[index].grams * lines[index].edible_portion / 100
            for index in range(len(lines))
        )

    nutrients_per_100g = {
        field: (round(total / cooked_weight * 100, 4) if total is not None else None)
        for field, total in totals.items()
    }
    kcal = nutrients_per_100g["kcal"]
    if kcal is None or not 0 <= kcal <= MAX_KCAL:
        raise RecipeValidationError(f"calculated kcal/100 g is invalid: {kcal}")
    for field in ("protein", "carbs", "fat"):
        value = nutrients_per_100g[field]
        if value is None or not 0 <= value <= MAX_MACRO:
            raise RecipeValidationError(f"calculated {field}/100 g is invalid: {value}")
    sodium = nutrients_per_100g["sodium"]
    if sodium is not None and not 0 <= sodium <= MAX_SODIUM:
        raise RecipeValidationError(f"calculated sodium/100 g is invalid: {sodium}")

    approximate = quality != "measured" or any(
        bool(getattr(session.get(CatalogItem, line.ingredient_food_id), "approximate", False))
        for line in lines
    )

    return PreparedRecipe(
        nutrients={
            "reference_amount": 100.0,
            "reference_unit": "g",
            **nutrients_per_100g,
        },
        approximate=approximate,
        lines=lines,
        details={
            "cooked_weight_grams": round(cooked_weight, 3),
            "uncooked_weight_grams": round(edible_mass, 3),
            "portion_count": portion_count,
            "quality": quality,
            "calculation_method": "ingredient_sum",
            "dataset_version": dataset_version,
            "notes": str(recipe.get("notes") or "").strip()[:2000] or None,
        },
    )
