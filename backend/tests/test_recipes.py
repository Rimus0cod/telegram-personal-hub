"""Recipe calculation, composition and prepared-meal import tests."""

import pytest

from app.models.models import (
    CatalogItem,
    FoodServing,
    FoodSource,
    NutrientProfile,
    RecipeDetails,
    RecipeIngredient,
)
from tools.catalog_import import import_records
from tools.recipes import RecipeValidationError, prepare_recipe

PREFIX = "/api/v1"


def _ingredient(
    db, source_id, name, *, kcal, protein, carbs, fat,
    profile=None, approximate=False,
):
    item = CatalogItem(
        source="test", source_id=source_id, name=name, name_original=name,
        category="Тест", type="ingredient", kcal=kcal, protein=protein,
        carbs=carbs, fat=fat, serving_size=100, serving_unit="g",
        approximate=approximate, is_active=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    values = {
        "kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat,
        "fiber": 0.0, "sugar": 0.0, "sodium": 0.0, "saturated_fat": 0.0,
    }
    values.update(profile or {})
    db.add(NutrientProfile(
        food_id=item.id, reference_amount=100, reference_unit="g",
        **values, source_code="test", is_primary=True, review_status="approved",
    ))
    db.commit()
    return item


def _line(item, grams, *, edible=1.0, state="raw"):
    return {
        "source": item.source,
        "source_id": item.source_id,
        "grams": grams,
        "edible_portion": edible,
        "preparation_state": state,
    }


def _record(
    source_id, lines, cooked_weight, *, quality="estimated",
    type_="prepared_meal", name="Test stew", ru="Тестовое рагу", uk="Тестове рагу",
):
    return {
        "source": "test",
        "source_id": source_id,
        "type": type_,
        "category": "Готовые блюда",
        "original_language": "en",
        "name_original": name,
        "translations": [
            {"language": "en", "name": name, "translation_source": "manual",
             "translation_status": "verified", "review_status": "approved",
             "is_primary": True},
            {"language": "ru", "name": ru, "translation_source": "manual",
             "translation_status": "verified", "review_status": "approved"},
            {"language": "uk", "name": uk, "translation_source": "manual",
             "translation_status": "verified", "review_status": "approved"},
        ],
        "aliases": [{"language": "ru", "alias": "рагу тестовое", "alias_type": "search"}],
        "servings": [
            {"amount": 1, "unit": "bowl", "grams": 250,
             "description": "1 bowl", "is_default": True},
        ],
        "recipe": {
            "cooked_weight_grams": cooked_weight,
            "portion_count": 2,
            "quality": quality,
            "notes": "test recipe",
            "ingredients": lines,
        },
        "preparation_state": "cooked",
        "dataset_version": "recipes-test",
    }


@pytest.fixture
def recipe_db(api):
    def reset():
        for item in api.db.query(CatalogItem).filter(CatalogItem.source == "test").all():
            api.db.delete(item)
        api.db.commit()

    reset()
    if api.db.query(FoodSource).filter_by(code="test").one_or_none() is None:
        api.db.add(FoodSource(
            code="test", name="Test source", data_types="ingredient,prepared_meal",
            acquisition_method="test", license="test", attribution_required=False,
            storage_permission=True, translation_permission=True,
            redistribution_permission=True, approval_status="approved",
            limitations="test only",
        ))
        api.db.commit()
    try:
        yield api
    finally:
        reset()


def test_recipe_calculation_with_edible_portion(recipe_db):
    db = recipe_db.db
    a = _ingredient(db, "ing:a", "Ingredient A", kcal=100, protein=10, carbs=20,
                    fat=5, profile={"fiber": 2.0, "sugar": 1.0, "sodium": 100.0,
                                    "saturated_fat": 1.0})
    b = _ingredient(db, "ing:b", "Ingredient B", kcal=50, protein=5, carbs=10,
                    fat=2, profile={"fiber": 0.0, "sugar": 0.0, "sodium": 50.0,
                                    "saturated_fat": 0.0})
    recipe = _record("recipe:calc", [_line(a, 200, edible=0.5), _line(b, 300)], 500)
    prepared = prepare_recipe(db, recipe["recipe"], dataset_version="recipes-test")

    assert prepared.nutrients["kcal"] == pytest.approx(50.0)
    assert prepared.nutrients["protein"] == pytest.approx(5.0)
    assert prepared.nutrients["carbs"] == pytest.approx(10.0)
    assert prepared.nutrients["fat"] == pytest.approx(2.2)
    assert prepared.nutrients["fiber"] == pytest.approx(0.4)
    assert prepared.nutrients["sugar"] == pytest.approx(0.2)
    assert prepared.nutrients["sodium"] == pytest.approx(50.0)
    assert prepared.nutrients["saturated_fat"] == pytest.approx(0.2)
    assert prepared.details["uncooked_weight_grams"] == pytest.approx(400.0)
    assert prepared.details["cooked_weight_grams"] == pytest.approx(500.0)
    assert prepared.approximate is True
    assert [line.ingredient_name for line in prepared.lines] == [
        "Ingredient A", "Ingredient B",
    ]


def test_optional_nutrients_stay_null_if_any_ingredient_lacks_them(recipe_db):
    db = recipe_db.db
    a = _ingredient(db, "ing:a2", "Known", kcal=100, protein=10, carbs=10, fat=1,
                    profile={"fiber": 2.0, "sugar": None})
    b = _ingredient(db, "ing:b2", "Unknown fiber", kcal=50, protein=5, carbs=5, fat=1,
                    profile={"fiber": None, "sugar": None})
    prepared = prepare_recipe(
        db, _record("recipe:null", [_line(a, 100), _line(b, 100)], 200)["recipe"],
        dataset_version="recipes-test",
    )
    assert prepared.nutrients["fiber"] is None
    assert prepared.nutrients["sugar"] is None
    assert prepared.nutrients["kcal"] == pytest.approx(75.0)


def test_recipe_rejects_missing_macro_and_unknown_ingredient(recipe_db):
    db = recipe_db.db
    good = _ingredient(db, "ing:c", "Good", kcal=100, protein=10, carbs=10, fat=1)
    bad = _ingredient(db, "ing:d", "No protein", kcal=100, protein=1,
                      carbs=10, fat=1, profile={"protein": None})

    with pytest.raises(RecipeValidationError, match="no protein"):
        prepare_recipe(
            db, _record("recipe:bad1", [_line(bad, 100)], 100)["recipe"],
            dataset_version="recipes-test",
        )
    unknown = {"source": "test", "source_id": "ing:nope", "grams": 100}
    with pytest.raises(RecipeValidationError, match="unknown food"):
        prepare_recipe(
            db, _record("recipe:bad2", [unknown], 100)["recipe"],
            dataset_version="recipes-test",
        )
    assert good.id


def test_recipe_rejects_implausible_yield(recipe_db):
    db = recipe_db.db
    item = _ingredient(db, "ing:e", "Bulk", kcal=100, protein=1, carbs=1, fat=1)
    for cooked in (50, 5000):
        with pytest.raises(RecipeValidationError, match="implausible"):
            prepare_recipe(
                db, _record("recipe:yield", [_line(item, 1000)], cooked)["recipe"],
                dataset_version="recipes-test",
            )


def test_import_creates_recipe_composition_and_is_searchable(recipe_db):
    db = recipe_db.db
    a = _ingredient(db, "ing:a3", "Ingredient A", kcal=100, protein=10, carbs=20,
                    fat=5, profile={"fiber": 2.0, "sugar": 1.0, "sodium": 100.0,
                                    "saturated_fat": 1.0})
    b = _ingredient(db, "ing:b3", "Ingredient B", kcal=50, protein=5, carbs=10,
                    fat=2, profile={"fiber": 0.0, "sugar": 0.0, "sodium": 50.0,
                                    "saturated_fat": 0.0})

    report = import_records(
        db, [_record("recipe:stew", [_line(a, 200, edible=0.5), _line(b, 300)], 500)],
        commit=True,
    )
    assert report["imported"] == 1, report
    assert report["recipes"] == 1
    assert report["recipe_ingredients"] == 2

    item = db.query(CatalogItem).filter_by(source_id="recipe:stew").one()
    assert item.type == "prepared_meal"
    assert item.kcal == pytest.approx(50.0)
    assert item.approximate is True

    profile = db.query(NutrientProfile).filter_by(food_id=item.id, is_primary=True).one()
    assert profile.dataset_version == "recipes-test"
    assert profile.reference_amount == 100.0
    assert profile.fiber == pytest.approx(0.4)

    details = db.query(RecipeDetails).filter_by(food_id=item.id).one()
    assert details.cooked_weight_grams == pytest.approx(500.0)
    assert details.uncooked_weight_grams == pytest.approx(400.0)
    assert details.portion_count == 2
    assert details.quality == "estimated"
    assert details.dataset_version == "recipes-test"

    ingredients = (
        db.query(RecipeIngredient)
        .filter_by(recipe_food_id=item.id)
        .order_by(RecipeIngredient.position)
        .all()
    )
    assert [row.ingredient_name for row in ingredients] == ["Ingredient A", "Ingredient B"]
    assert [row.position for row in ingredients] == [1, 2]
    assert ingredients[0].grams == pytest.approx(200.0)
    assert ingredients[0].edible_portion == pytest.approx(0.5)
    assert ingredients[0].preparation_state == "raw"
    assert ingredients[0].nutrient_snapshot["kcal"] == pytest.approx(100.0)
    assert ingredients[1].nutrient_snapshot["fiber"] == 0.0

    servings = db.query(FoodServing).filter_by(food_id=item.id).all()
    assert len(servings) == 1
    assert servings[0].unit == "bowl" and servings[0].grams == 250.0

    for query, language in [
        ("Test stew", "en"),
        ("Тестовое рагу", "ru"),
        ("Тестове рагу", "uk"),
        ("testove rahu", "uk"),
        ("рагу тестовое", "ru"),
    ]:
        response = recipe_db.client.get(
            f"{PREFIX}/nutrition/catalog/search",
            params={"q": query, "lang": language},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["items"], (query, payload)
        assert payload["items"][0]["source_id"] == "recipe:stew", query


def test_recipe_import_is_idempotent(recipe_db):
    db = recipe_db.db
    a = _ingredient(db, "ing:a4", "A", kcal=80, protein=8, carbs=8, fat=1)
    record = _record("recipe:repeat", [_line(a, 100)], 100)

    assert import_records(db, [record], commit=True)["imported"] == 1
    item = db.query(CatalogItem).filter_by(source_id="recipe:repeat").one()
    first = db.query(RecipeIngredient).filter_by(recipe_food_id=item.id).count()

    repeated = import_records(db, [record], commit=True)
    assert repeated["imported"] == 0
    assert repeated["skipped"] == 1
    assert db.query(RecipeIngredient).filter_by(recipe_food_id=item.id).count() == first
    assert db.query(RecipeDetails).filter_by(food_id=item.id).count() == 1


def test_partial_recipe_failure_keeps_valid_rows(recipe_db):
    db = recipe_db.db
    a = _ingredient(db, "ing:a5", "A", kcal=80, protein=8, carbs=8, fat=1)
    good = _record("recipe:good", [_line(a, 100)], 100)
    bad = _record(
        "recipe:bad",
        [{"source": "test", "source_id": "ing:missing", "grams": 100}],
        100,
        name="Broken stew", ru="Сломанное рагу", uk="Зламане рагу",
    )

    report = import_records(db, [good, bad], commit=True)
    assert report["imported"] == 1
    assert len(report["errors"]) == 1
    assert "unknown food" in report["errors"][0]["error"]
    assert db.query(CatalogItem).filter_by(source_id="recipe:bad").count() == 0
    assert db.query(RecipeDetails).count() == 1


def test_measured_recipe_with_exact_ingredients_is_not_approximate(recipe_db):
    db = recipe_db.db
    a = _ingredient(db, "ing:a6", "A", kcal=80, protein=8, carbs=8, fat=1)
    record = _record("recipe:measured", [_line(a, 100)], 100, quality="measured")
    import_records(db, [record], commit=True)
    item = db.query(CatalogItem).filter_by(source_id="recipe:measured").one()
    assert item.approximate is False


def test_recipe_requires_prepared_meal_type(recipe_db):
    db = recipe_db.db
    a = _ingredient(db, "ing:a7", "A", kcal=80, protein=8, carbs=8, fat=1)
    record = _record("recipe:wrong-type", [_line(a, 100)], 100, type_="ingredient")
    report = import_records(db, [record], commit=True)
    assert report["imported"] == 0
    assert report["errors"]
    assert "prepared_meal" in report["errors"][0]["error"]
