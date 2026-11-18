import uuid

from app.models.models import (
    CatalogItem,
    CommunityFood,
    FoodAlias,
    FoodBarcode,
    FoodServing,
    FoodSource,
    FoodTranslation,
    NutrientProfile,
)

PREFIX = "/api/v1"


def _barcode():
    return str(700_000_000_000 + uuid.uuid4().int % 100_000_000_000)


def _catalog_item(name="Curated test food", source_id=None):
    return CatalogItem(
        source="builtin",
        source_id=source_id or f"test:{uuid.uuid4().hex}",
        name=name,
        name_original=name,
        name_ru=name,
        name_uk=name,
        category="Тест",
        type="prepared_meal",
        kcal=123,
        protein=4,
        carbs=5,
        fat=6,
        serving_size=100,
        serving_unit="g",
        preparation_state="ready",
        approximate=True,
        is_active=True,
    )


def test_catalog_items_merges_curated_and_community_without_user_ids(api):
    curated = _catalog_item("Curated API food")
    api.db.add(curated)
    api.db.commit()

    barcode = _barcode()
    created = api.client.post(
        f"{PREFIX}/nutrition/catalog",
        json={
            "barcode": barcode,
            "name": "Community API food",
            "brand": "Brand",
            "kcal": 200,
            "protein": 10,
            "carbs": 20,
            "fat": 5,
        },
    )
    assert created.status_code == 201, created.text

    response = api.client.get(f"{PREFIX}/nutrition/catalog-items")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == len(body["items"])
    names = {item["name"] for item in body["items"]}
    assert "Curated API food" in names
    assert "Community API food" in names
    for item in body["items"]:
        assert "submitted_by" not in item
    community = next(item for item in body["items"] if item["name"] == "Community API food")
    assert community["source"] == "community"
    assert community["barcode"] == barcode
    assert community["type"] == "ingredient"


def test_barcode_product_persists_across_catalog_reads_and_repeat_scan(api):
    barcode = _barcode()
    payload = {
        "barcode": barcode,
        "name": "Persistent QR food",
        "brand": "Brand",
        "kcal": 150,
        "protein": 8,
        "carbs": 12,
        "fat": 4,
    }

    first = api.client.post(f"{PREFIX}/nutrition/catalog", json=payload)
    assert first.status_code == 201, first.text

    # A fresh lookup is the same path used after reopening the Mini App.
    lookup = api.client.get(f"{PREFIX}/nutrition/catalog/{barcode}")
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["found"] is True
    assert lookup.json()["product"]["product_name"] == "Persistent QR food"

    listed = api.client.get(f"{PREFIX}/nutrition/catalog-items").json()
    assert any(item["barcode"] == barcode for item in listed["items"])

    repeat = api.client.post(f"{PREFIX}/nutrition/catalog", json={**payload, "name": "Updated QR food"})
    assert repeat.status_code == 201, repeat.text
    api.db.expire_all()
    assert api.db.query(CommunityFood).filter_by(barcode=barcode).count() == 1

    # Another account can read the shared product but cannot overwrite it.
    api.holder["user"] = api.user2
    other = api.client.post(f"{PREFIX}/nutrition/catalog", json=payload)
    assert other.status_code == 409
    lookup2 = api.client.get(f"{PREFIX}/nutrition/catalog/{barcode}")
    assert lookup2.status_code == 200
    assert lookup2.json()["found"] is True


def test_barcode_missing_product_is_reported_not_cached(api, monkeypatch):
    barcode = _barcode()

    class MissingResponse:
        is_error = False

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": 0}

    import app.api.nutrition as nutrition_api

    monkeypatch.setattr(nutrition_api.httpx, "get", lambda *args, **kwargs: MissingResponse())
    response = api.client.get(f"{PREFIX}/nutrition/catalog/{barcode}")
    assert response.status_code == 200, response.text
    assert response.json()["found"] is False
    assert api.db.query(CommunityFood).filter_by(barcode=barcode).count() == 0


def test_provider_failure_does_not_remove_saved_products(api, monkeypatch):
    barcode = _barcode()
    created = api.client.post(
        f"{PREFIX}/nutrition/catalog",
        json={
            "barcode": barcode, "name": "Offline-safe food", "brand": "Brand",
            "kcal": 90, "protein": 3, "carbs": 9, "fat": 2,
        },
    )
    assert created.status_code == 201, created.text

    import app.api.nutrition as nutrition_api

    def boom(*args, **kwargs):
        raise nutrition_api.httpx.ConnectError("provider offline")

    monkeypatch.setattr(nutrition_api.httpx, "get", boom)
    unknown = api.client.get(f"{PREFIX}/nutrition/catalog/{_barcode()}")
    assert unknown.status_code == 502, unknown.text

    api.db.expire_all()
    assert api.db.query(CommunityFood).filter_by(barcode=barcode).count() == 1
    listed = api.client.get(f"{PREFIX}/nutrition/catalog-items").json()
    assert any(item["barcode"] == barcode for item in listed["items"])
    lookup = api.client.get(f"{PREFIX}/nutrition/catalog/{barcode}")
    assert lookup.status_code == 200
    assert lookup.json()["product"]["product_name"] == "Offline-safe food"


def test_catalog_review_status_reflects_pending_provenance(api):
    item = _catalog_item("Pending review probe")
    api.db.add(item)
    api.db.commit()
    api.db.refresh(item)
    api.db.add_all([
        FoodTranslation(food_id=item.id, language="de", name="Ausstehend",
                        translation_source="machine", translation_status="machine",
                        review_status="pending", is_primary=True),
        NutrientProfile(food_id=item.id, reference_amount=100, reference_unit="g",
                        kcal=50, protein=1, carbs=2, fat=3,
                        source_code="usda", is_primary=True, review_status="pending"),
    ])
    api.db.commit()
    api.db.expire_all()

    response = api.client.get(f"{PREFIX}/nutrition/catalog-items", params={"lang": "de"})
    found = next(
        row for row in response.json()["items"] if row["source_id"] == item.source_id
    )
    assert found["name"] == "Ausstehend"
    assert found["review_status"] == "pending"

    profile = api.db.query(NutrientProfile).filter_by(food_id=item.id).one()
    translation = api.db.query(FoodTranslation).filter_by(food_id=item.id).one()
    profile.review_status = "approved"
    translation.review_status = "approved"
    api.db.commit()
    api.db.expire_all()

    response = api.client.get(f"{PREFIX}/nutrition/catalog-items", params={"lang": "de"})
    found = next(
        row for row in response.json()["items"] if row["source_id"] == item.source_id
    )
    assert found["review_status"] == "approved"


def test_catalog_translations_aliases_nutrients_and_servings(api):
    item = _catalog_item("Canonical catalog food")
    barcode = "12345678"
    api.db.add(item)
    api.db.commit()
    api.db.refresh(item)
    api.db.add_all([
        FoodTranslation(
            food_id=item.id, language="en", name="Potato",
            translation_source="manual", translation_status="verified",
            review_status="approved", is_primary=True,
        ),
        FoodTranslation(
            food_id=item.id, language="ru", name="Картофель",
            translation_source="manual", translation_status="verified",
            review_status="approved", is_primary=False,
        ),
        FoodTranslation(
            food_id=item.id, language="uk", name="Картопля",
            translation_source="manual", translation_status="verified",
            review_status="approved", is_primary=False,
        ),
        FoodAlias(food_id=item.id, language="en", alias="potato", alias_type="search"),
        FoodAlias(food_id=item.id, language="ru", alias="картошка", alias_type="search"),
        NutrientProfile(
            food_id=item.id, reference_amount=100, reference_unit="g",
            kcal=77, protein=2, carbs=17, fat=0,
            fiber=None, sugar=0, sodium=None, saturated_fat=None,
            source_code="usda", is_primary=True, review_status="approved",
        ),
        NutrientProfile(
            food_id=item.id, reference_amount=100, reference_unit="g",
            kcal=80, protein=2.1, carbs=17.5, fat=0.1,
            source_code="openfoodfacts", is_primary=False, review_status="pending",
        ),
        FoodServing(
            food_id=item.id, amount=1, unit="piece", grams=150,
            description="1 medium potato", is_default=True,
        ),
        FoodBarcode(food_id=item.id, barcode=barcode, source_code="usda"),
    ])
    api.db.commit()
    api.db.expire_all()

    response = api.client.get(f"{PREFIX}/nutrition/catalog-items", params={"lang": "en"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["language"] == "en"
    found = next(row for row in body["items"] if row["name"] == "Potato")
    assert found["name_ru"] == "Картофель"
    assert found["name_uk"] == "Картопля"
    assert found["aliases"] == ["potato", "картошка"]
    assert "Potato" in found["search_terms"]
    assert "картошка" in found["search_terms"]
    assert found["nutrients"]["fiber"] is None
    assert found["nutrients"]["sugar"] == 0
    assert found["nutrients"]["kcal"] == 77
    assert api.db.query(NutrientProfile).filter_by(food_id=item.id).count() == 2
    assert found["serving_size"] == 150
    assert found["serving_unit"] == "piece"
    assert found["barcode"] == barcode
    assert set(found["translations"]) == {"en", "ru", "uk"}

    # Unknown language falls back to English, then Russian/Ukrainian.
    fallback = api.client.get(f"{PREFIX}/nutrition/catalog-items", params={"lang": "de"})
    assert fallback.status_code == 200
    fallback_found = next(
        row for row in fallback.json()["items"] if row["name"] == "Potato"
    )
    assert fallback_found["name"] == "Potato"


def test_catalog_barcode_is_resolved_from_curated_catalog(api):
    item = _catalog_item("Packaged catalog food")
    barcode = "87654321"
    api.db.add(item)
    api.db.commit()
    api.db.refresh(item)
    api.db.add_all([
        FoodTranslation(
            food_id=item.id, language="ru", name="Упакованный продукт",
            translation_source="manual", translation_status="verified",
            review_status="approved", is_primary=True,
        ),
        NutrientProfile(
            food_id=item.id, reference_amount=100, reference_unit="g",
            kcal=210, protein=7, carbs=25, fat=9,
            source_code="manual", is_primary=True, review_status="approved",
        ),
        FoodBarcode(food_id=item.id, barcode=barcode, source_code="manual"),
    ])
    api.db.commit()
    api.db.expire_all()

    response = api.client.get(f"{PREFIX}/nutrition/catalog/{barcode}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["found"] is True
    assert body["source"] == "catalog"
    assert body["product"]["product_name"] == "Упакованный продукт"
    assert body["product"]["nutriments"]["energy-kcal_100g"] == 210


def test_catalog_language_selection_and_identity_are_stable(api):
    item = _catalog_item("Language probe")
    api.db.add(item)
    api.db.commit()
    api.db.refresh(item)
    api.db.add_all([
        FoodTranslation(food_id=item.id, language="en", name="Potato EN",
                        translation_source="manual", translation_status="verified",
                        review_status="approved", is_primary=True),
        FoodTranslation(food_id=item.id, language="ru", name="Картофель RU",
                        translation_source="manual", translation_status="verified",
                        review_status="approved"),
        FoodTranslation(food_id=item.id, language="uk", name="Картопля UK",
                        translation_source="manual", translation_status="verified",
                        review_status="approved"),
        NutrientProfile(food_id=item.id, reference_amount=100, reference_unit="g",
                        kcal=77, protein=2, carbs=17, fat=0, fiber=None, sugar=0,
                        source_code="usda", is_primary=True, review_status="approved"),
    ])
    api.db.commit()
    api.db.expire_all()

    def probe(lang):
        response = api.client.get(f"{PREFIX}/nutrition/catalog-items", params={"lang": lang})
        assert response.status_code == 200, response.text
        body = response.json()
        found = next(row for row in body["items"] if row["source_id"] == item.source_id)
        return body, found

    body_uk, uk = probe("uk")
    assert uk["name"] == "Картопля UK"
    assert uk["name_ru"] == "Картофель RU"
    assert uk["name_uk"] == "Картопля UK"
    assert uk["display_language"] == "uk"
    assert body_uk["language"] == "uk"

    _, ru = probe("ru")
    assert ru["name"] == "Картофель RU"
    assert ru["display_language"] == "ru"
    _, en = probe("en")
    assert en["name"] == "Potato EN"
    assert en["display_language"] == "en"

    # Unsupported and region-tagged languages fall back without losing identity.
    _, de = probe("de")
    assert de["name"] == "Potato EN"
    assert de["display_language"] == "en"
    _, upper = probe("RU")
    assert upper["name"] == "Картофель RU"
    body_region, region = probe("ru-RU")
    assert region["name"] == "Картофель RU"
    assert body_region["language"] == "ru-ru"
    _, underscore = probe("uk_UA")
    assert underscore["name"] == "Картопля UK"
    _, unsupported = probe("xx")
    assert unsupported["name"] == "Potato EN"

    # Language only changes display strings, never identity or nutrients.
    for row in (uk, ru, en, de, upper, region, underscore, unsupported):
        assert row["id"] == f"curated:{item.id}"
        assert row["nutrients"] == en["nutrients"]
        assert row["kcal"] == 77 and row["protein"] == 2
        assert row["nutrients"]["sugar"] == 0
        assert row["nutrients"]["fiber"] is None

    invalid = api.client.get(f"{PREFIX}/nutrition/catalog-items", params={"lang": "!!"})
    assert invalid.status_code == 422


def test_catalog_language_falls_back_to_primary_then_canonical_name(api):
    uk_only = _catalog_item("Ukrainian only probe")
    german_only = _catalog_item("German only probe")
    canonical_only = _catalog_item("Canonical only probe")
    canonical_only.name_ru = "Только русское имя"
    uppercase = _catalog_item("Uppercase tag probe")
    api.db.add_all([uk_only, german_only, canonical_only, uppercase])
    api.db.commit()
    for row in (uk_only, german_only, canonical_only, uppercase):
        api.db.refresh(row)
    api.db.add_all([
        FoodTranslation(food_id=uk_only.id, language="uk", name="Лише українська",
                        translation_source="manual", translation_status="verified",
                        review_status="approved", is_primary=True),
        FoodTranslation(food_id=german_only.id, language="de", name="Nur Deutsch",
                        translation_source="manual", translation_status="verified",
                        review_status="approved", is_primary=True),
        FoodTranslation(food_id=uppercase.id, language="UK", name="Верхній регістр",
                        translation_source="manual", translation_status="verified",
                        review_status="approved", is_primary=True),
    ])
    api.db.commit()
    api.db.expire_all()

    response = api.client.get(f"{PREFIX}/nutrition/catalog-items", params={"lang": "en"})
    items = {row["source_id"]: row for row in response.json()["items"]}
    # en -> ru -> uk fallback reaches the only available translation.
    assert items[uk_only.source_id]["name"] == "Лише українська"
    # A primary translation in an unrelated language is still better than nothing.
    assert items[german_only.source_id]["name"] == "Nur Deutsch"
    # With no translations at all the canonical name is used.
    assert items[canonical_only.source_id]["name"] == "Только русское имя"
    # Stored uppercase tags are matched case-insensitively.
    uppercased = items[uppercase.source_id]
    assert uppercased["name"] == "Верхній регістр"
    assert uppercased["name_uk"] == "Верхній регістр"


def test_catalog_changes_do_not_rewrite_diary_snapshot(api):
    day = "2026-11-17"
    meal = api.client.post(
        f"{PREFIX}/nutrition",
        json={
            "day": day, "meal_type": "lunch", "name": "Snapshot food",
            "calories": 100, "protein": 5, "carbs": 10, "fat": 3,
            "ingredients": "100 г", "serving_grams": 100,
        },
    )
    assert meal.status_code == 201, meal.text

    item = _catalog_item("Snapshot food")
    item.kcal = 500
    api.db.add(item)
    api.db.commit()

    listed = api.client.get(
        f"{PREFIX}/nutrition", params={"from": day, "to": day}
    ).json()
    assert listed[0]["calories"] == 100
    assert listed[0]["protein"] == 5


def test_food_source_registry_is_safe_and_exposes_licensing(api):
    response = api.client.get(f"{PREFIX}/nutrition/food-sources")
    assert response.status_code == 200, response.text
    sources = {item["code"]: item for item in response.json()}
    assert sources["usda"]["storage_permission"] is True
    assert sources["usda"]["license"].startswith("CC0")
    assert sources["fatsecret"]["storage_permission"] is False
    assert sources["fatsecret"]["approval_status"] == "blocked"
    assert sources["openfoodfacts"]["attribution_required"] is True
    assert "client_secret" not in response.text.lower()
    assert "password" not in response.text.lower()

