import pytest

from app.models.models import (
    CatalogItem,
    FoodAlias,
    FoodBarcode,
    FoodServing,
    FoodSource,
    FoodTranslation,
    NutrientProfile,
)
from tools.catalog_import import (
    ImportValidationError,
    fatsecret_preview,
    import_records,
    normalize_record,
    normalize_v2_record,
    preview_from_food,
    translate_fatsecret_name,
)


def test_normalize_record_accepts_curated_values():
    record = normalize_record(
        {
            "source": "builtin",
            "source_id": "builtin:test-borscht",
            "name": "Борщ",
            "name_ru": "Борщ",
            "name_uk": "Борщ",
            "category": "Супы",
            "type": "prepared_meal",
            "kcal": 60,
            "protein": 3,
            "carbs": 7,
            "fat": 2,
            "serving_size": 100,
            "serving_unit": "g",
            "preparation_state": "ready",
            "approximate": True,
        }
    )
    assert record["type"] == "prepared_meal"
    assert record["source_id"] == "builtin:test-borscht"
    assert record["approximate"] is True


@pytest.mark.parametrize(
    "override,message",
    [
        ({"type": "other"}, "type must be"),
        ({"serving_unit": "kg"}, "serving_unit must be"),
        ({"kcal": -1}, "kcal must be"),
        ({"protein": "nope"}, "protein must be a number"),
        ({"source": "x" * 33}, "source must contain 1-32 characters"),
    ],
)
def test_normalize_record_rejects_invalid_values(override, message):
    base = {
        "name": "Test",
        "category": "Test",
        "type": "ingredient",
        "kcal": 100,
        "protein": 1,
        "carbs": 2,
        "fat": 3,
        "serving_size": 100,
        "serving_unit": "g",
    }
    with pytest.raises(ImportValidationError, match=message):
        normalize_record({**base, **override})


def _catalog_record(source_id="test:catalog-1", name="Тестовое блюдо"):
    return {
        "source": "builtin",
        "source_id": source_id,
        "name": name,
        "name_ru": name,
        "name_uk": name,
        "category": "Тест",
        "type": "prepared_meal",
        "kcal": 123,
        "protein": 4,
        "carbs": 5,
        "fat": 6,
        "serving_size": 100,
        "serving_unit": "g",
        "preparation_state": "ready",
        "approximate": True,
    }


def test_import_dry_run_commit_and_repeat_without_duplicates(api):
    record = _catalog_record()
    dry = import_records(api.db, [record], commit=False)
    assert dry["valid"] == 1
    assert dry["imported"] == 0
    assert api.db.query(CatalogItem).filter_by(source_id=record["source_id"]).count() == 0

    written = import_records(api.db, [record], commit=True)
    assert written["imported"] == 1
    assert api.db.query(CatalogItem).filter_by(source_id=record["source_id"]).count() == 1

    repeated = import_records(api.db, [record], commit=True)
    assert repeated["imported"] == 0
    assert repeated["skipped"] == 1
    assert api.db.query(CatalogItem).filter_by(source_id=record["source_id"]).count() == 1


def test_import_skips_duplicate_name_and_invalid_rows(api):
    first = _catalog_record("test:unique-1", "Уникальное блюдо")
    second = _catalog_record("test:unique-2", "Уникальное блюдо")
    invalid = {**_catalog_record("test:invalid", "Плохое блюдо"), "kcal": -5}

    report = import_records(api.db, [first, second, invalid], commit=True)
    assert report["imported"] == 1
    assert report["skipped"] == 1
    assert len(report["errors"]) == 1
    assert api.db.query(CatalogItem).filter_by(name="Уникальное блюдо").count() == 1


def test_fatsecret_commit_is_blocked(api):
    record = {**_catalog_record("fatsecret:123", "Chicken"), "source": "fatsecret"}
    with pytest.raises(ImportValidationError, match="preview-only"):
        import_records(api.db, [record], commit=True)


def test_preview_scales_servings_to_100g_and_translates():
    food = {
        "food_id": "1641",
        "food_name": "Chicken Breast",
        "food_type": "Generic",
        "food_url": "https://example.test/chicken",
        "servings": {
            "serving": [
                {
                    "metric_serving_amount": "200",
                    "metric_serving_unit": "g",
                    "calories": "330",
                    "protein": "62",
                    "carbohydrate": "0",
                    "fat": "7.2",
                }
            ]
        },
    }
    record = preview_from_food(food)
    assert record is not None
    assert record["kcal"] == 165
    assert record["protein"] == 31
    assert record["fat"] == 3.6
    assert record["name_ru"] == "Куриная грудка"
    assert record["name_uk"] == "Куряча грудка"
    assert record["license_status"] == "preview_only"


def test_untranslated_food_is_marked_for_review():
    assert translate_fatsecret_name("Unknown regional dish")["translation_status"] == "manual_required"


def test_fatsecret_preview_contains_expiry_and_license_warning():
    class FakeClient:
        def search(self, query, max_results=10):
            return [{"food_id": "1"}]

        def get_food(self, food_id):
            return {
                "food_id": food_id,
                "food_name": "Chicken Breast",
                "servings": {
                    "serving": {
                        "metric_serving_amount": "100",
                        "metric_serving_unit": "g",
                        "calories": "165",
                        "protein": "31",
                        "carbohydrate": "0",
                        "fat": "3.6",
                    }
                },
            }

    preview = fatsecret_preview(FakeClient(), "chicken", max_results=1)
    assert preview["source"] == "fatsecret"
    assert preview["delete_by"]
    assert "24 hours" in preview["license_warning"] or "24" in preview["license_warning"]
    assert len(preview["records"]) == 1
    assert preview["records"][0]["source_id"] == "1"


def _v2_record(source_id="pilot:test-v2", source="manual"):
    return {
        "source": source,
        "source_id": source_id,
        "type": "prepared_meal",
        "category": "Супы",
        "original_language": "en",
        "name_original": "Borscht",
        "translations": [
            {"language": "en", "name": "Borscht", "translation_source": "manual",
             "translation_status": "verified", "review_status": "approved", "is_primary": True},
            {"language": "ru", "name": "Борщ", "translation_source": "manual",
             "translation_status": "verified", "review_status": "approved"},
            {"language": "uk", "name": "Борщ", "translation_source": "manual",
             "translation_status": "verified", "review_status": "approved"},
        ],
        "aliases": [{"language": "ru", "alias": "борщ", "alias_type": "search"}],
        "nutrients": {
            "reference_amount": 100, "reference_unit": "g",
            "kcal": 60, "protein": 3, "carbs": 7, "fat": 2,
            "fiber": None, "sugar": 0, "sodium": None, "saturated_fat": None,
        },
        "servings": [
            {"amount": 1, "unit": "bowl", "grams": 300, "description": "1 bowl", "is_default": True}
        ],
        "preparation_state": "ready",
        "approximate": True,
    }


def test_normalize_v2_record_supports_multiple_languages_and_nullable_nutrients():
    record = normalize_v2_record(_v2_record())
    assert record["name"] == "Borscht"
    assert record["name_ru"] == "Борщ"
    assert record["name_uk"] == "Борщ"
    assert len(record["_translations"]) == 3
    assert record["_nutrients"]["fiber"] is None
    assert record["_nutrients"]["sugar"] == 0
    assert record["_servings"][0]["grams"] == 300


def test_import_v2_creates_child_records_and_is_idempotent(api):
    record = _v2_record("pilot:test-v2-commit")
    report = import_records(api.db, [record], commit=True)
    assert report["imported"] == 1
    assert report["translations"] == 3
    assert report["aliases"] == 1

    item = api.db.query(CatalogItem).filter_by(source_id=record["source_id"]).one()
    assert api.db.query(FoodTranslation).filter_by(food_id=item.id).count() == 3
    assert api.db.query(FoodAlias).filter_by(food_id=item.id).count() == 1
    assert api.db.query(NutrientProfile).filter_by(food_id=item.id).count() == 1
    assert api.db.query(FoodServing).filter_by(food_id=item.id).count() == 1
    profile = api.db.query(NutrientProfile).filter_by(food_id=item.id).one()
    assert profile.fiber is None
    assert profile.sugar == 0

    repeated = import_records(api.db, [record], commit=True)
    assert repeated["imported"] == 0
    assert repeated["skipped"] == 1
    assert api.db.query(CatalogItem).filter_by(source_id=record["source_id"]).count() == 1


def test_import_v2_rejects_pending_or_blocked_source(api):
    with pytest.raises(ImportValidationError, match="preview-only"):
        import_records(api.db, [_v2_record("pilot:fatsecret", "fatsecret")], commit=True)
    report = import_records(api.db, [_v2_record("pilot:off", "openfoodfacts")], commit=True)
    assert report["imported"] == 0
    assert report["skipped"] == 1
    assert "does not permit" in report["skips"][0]["reason"]


def test_import_v2_rejects_invalid_translations_and_nutrients(api):
    bad_language = _v2_record("pilot:bad-language")
    bad_language["translations"][1]["language"] = "en"
    report = import_records(api.db, [bad_language], commit=True)
    assert report["imported"] == 0
    assert report["errors"]

    bad_nutrient = _v2_record("pilot:bad-nutrient")
    bad_nutrient["nutrients"]["protein"] = None
    report = import_records(api.db, [bad_nutrient], commit=True)
    assert report["imported"] == 0
    assert report["errors"]


def _provider(code, *, storage, translation, approval):
    return FoodSource(
        code=code, name=code, data_types="ingredient", acquisition_method="test",
        license="test licence", attribution_required=False,
        storage_permission=storage, translation_permission=translation,
        redistribution_permission=storage, approval_status=approval,
        limitations="test provider",
    )


def _ensure_provider(session, code, **permissions):
    if session.query(FoodSource).filter_by(code=code).first() is None:
        session.add(_provider(code, **permissions))
        session.commit()


def _named_v2(source_id, source, english, russian=None):
    record = _v2_record(source_id, source)
    record["name_original"] = english
    record["translations"] = [
        {"language": "en", "name": english, "translation_source": "manual",
         "translation_status": "verified", "review_status": "approved", "is_primary": True}
    ]
    if russian:
        record["translations"].append(
            {"language": "ru", "name": russian, "translation_source": "manual",
             "translation_status": "verified", "review_status": "approved"}
        )
    record["aliases"] = []
    return record


def test_import_permissions_come_from_registry_fields_not_source_names(api):
    """A brand-new provider is importable without editing the importer."""
    _ensure_provider(api.db, "reviewed_provider",
                     storage=True, translation=True, approval="approved")
    _ensure_provider(api.db, "pending_provider",
                     storage=True, translation=True, approval="pending")
    _ensure_provider(api.db, "no_storage_provider",
                     storage=False, translation=True, approval="approved")
    _ensure_provider(api.db, "no_translation_provider",
                     storage=True, translation=False, approval="approved")

    approved = import_records(
        api.db,
        [_named_v2("pilot:new-provider", "reviewed_provider",
                   "Registry approved dish", "Одобренное блюдо")],
        commit=True,
    )
    assert approved["imported"] == 1, approved

    pending = import_records(
        api.db,
        [_named_v2("pilot:pending-provider", "pending_provider",
                   "Registry pending dish")],
        commit=True,
    )
    assert pending["imported"] == 0
    assert "approval status is pending" in pending["skips"][0]["reason"]

    no_storage = import_records(
        api.db,
        [_named_v2("pilot:no-storage", "no_storage_provider",
                   "Registry no storage dish")],
        commit=True,
    )
    assert no_storage["imported"] == 0
    assert "does not permit permanent storage" in no_storage["skips"][0]["reason"]

    unknown = import_records(
        api.db,
        [_named_v2("pilot:unknown-provider", "not_in_registry",
                   "Registry unknown dish")],
        commit=True,
    )
    assert unknown["imported"] == 0
    assert "unknown source" in unknown["skips"][0]["reason"]


def test_import_blocks_translated_content_without_translation_permission(api):
    _ensure_provider(api.db, "no_translation_provider",
                     storage=True, translation=False, approval="approved")

    translated = import_records(
        api.db,
        [_named_v2("pilot:blocked-translation", "no_translation_provider",
                   "Registry translated dish", "Переведённое блюдо")],
        commit=True,
    )
    assert translated["imported"] == 0
    assert "does not permit translated content" in translated["skips"][0]["reason"]

    original_only = import_records(
        api.db,
        [_named_v2("pilot:original-only", "no_translation_provider",
                   "Registry original only dish")],
        commit=True,
    )
    assert original_only["imported"] == 1, original_only

