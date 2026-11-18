"""Multilingual catalog search and translation workflow tests."""

import uuid

import pytest

from app.models.models import (
    CatalogItem, CommunityFood, FoodAlias, FoodBarcode, FoodSearchTerm,
    FoodTranslation, NutrientProfile,
)
from app.services.food_search import (
    build_search_terms,
    normalize_text,
    query_variants,
    rebuild_search_terms_for_food_id,
    score_term,
    transliteration_variants,
)
from tools.catalog_import import (
    export_untranslated,
    import_translations,
    reindex_search,
)

PREFIX = "/api/v1"


def _item(source_id, name, *, category="Тест", type_="ingredient"):
    return CatalogItem(
        source="test", source_id=source_id, name=name, name_original=name,
        category=category, type=type_, kcal=100, protein=5, carbs=10, fat=3,
        serving_size=100, serving_unit="g", is_active=True,
    )


def _add_translation(db, item, language, name, *, status="verified", review="approved"):
    db.add(FoodTranslation(
        food_id=item.id, language=language, name=name,
        translation_source="manual", translation_status=status,
        review_status=review, is_primary=False,
    ))


def _add_alias(db, item, language, alias):
    db.add(FoodAlias(
        food_id=item.id, language=language, alias=alias, alias_type="search",
    ))


@pytest.fixture
def search_db(api):
    """API fixture with a clean 'test' curated slice and its search terms."""
    def reset():
        for item in api.db.query(CatalogItem).filter(CatalogItem.source == "test").all():
            api.db.delete(item)
        for row in api.db.query(CommunityFood).filter(
            CommunityFood.name.like("Search test%")
        ).all():
            api.db.delete(row)
        api.db.commit()

    reset()
    try:
        yield api
    finally:
        reset()


def _seed(search_db):
    db = search_db.db
    potato = _item("search:potato", "Potato")
    bread = _item("search:potato-bread", "Potato bread")
    cafe = _item("search:cafe", "Café latte")
    salad = _item("search:salad", "Картофельный салат")
    db.add_all([potato, bread, cafe, salad])
    db.commit()
    for item in (potato, bread, cafe, salad):
        db.refresh(item)
    _add_translation(db, potato, "en", "Potato")
    _add_translation(db, potato, "ru", "Картофель")
    _add_translation(db, potato, "uk", "Картопля")
    _add_alias(db, potato, "ru", "картошка")
    _add_translation(db, bread, "ru", "Картофельный хлеб")
    _add_translation(db, salad, "ru", "Картофельный салат")
    db.add(FoodBarcode(food_id=potato.id, barcode="4000000000017", source_code="manual"))
    db.add(NutrientProfile(
        food_id=potato.id, reference_amount=100, reference_unit="g",
        kcal=77, protein=2, carbs=17, fat=0, fiber=None, sugar=0,
        source_code="test", is_primary=True, review_status="approved",
    ))
    db.commit()
    for item in (potato, bread, cafe, salad):
        rebuild_search_terms_for_food_id(db, item.id)
    db.commit()
    return {"potato": potato, "bread": bread, "cafe": cafe, "salad": salad}


def _search(search_db, q, **params):
    response = search_db.client.get(
        f"{PREFIX}/nutrition/catalog/search", params={"q": q, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Normalization and transliteration
# ---------------------------------------------------------------------------


def test_normalize_text_handles_case_diacritics_and_punctuation():
    assert normalize_text("  POTATO  ") == "potato"
    assert normalize_text("Café") == "cafe"
    assert normalize_text("Cafe\u0301") == "cafe"
    assert normalize_text("по-деревенски") == "по деревенски"
    assert normalize_text("м'ясо") == "мясо"
    assert normalize_text("Йогурт, 2.5%") == "иогурт 2 5"
    assert normalize_text("Ёлка") == normalize_text("елка")
    assert normalize_text("") == ""


def test_transliteration_variants_cover_user_spellings():
    ru = transliteration_variants("картофель", "ru")
    assert "kartofel" in ru
    assert "kartoshka" in transliteration_variants("картошка", "ru")

    uk = transliteration_variants("картопля", "uk")
    assert "kartoplya" in uk and "kartoplia" in uk

    bread = transliteration_variants("хліб", "uk")
    assert "khlib" in bread and "hlib" in bread

    assert transliteration_variants("potato", "ru") == []
    assert transliteration_variants("картофель", "de") == []


def test_query_variants_include_transliteration_only_for_cyrillic():
    assert query_variants("potato", "ru") == [("potato", "ru")]
    variants = dict(query_variants("картофель", "ru"))
    assert "картофель" in variants
    assert "kartofel" in variants


def test_build_search_terms_dedupes_and_skips_pending_translations():
    terms = build_search_terms(
        names=["Potato", "Potato"],
        translations=[
            ("ru", "Картофель", "approved"),
            ("uk", "Картопля", "pending"),
        ],
        aliases=[("ru", "картошка"), ("ru", "картошка")],
    )
    keys = {(term.language, term.term_type, term.normalized) for term in terms}
    assert ("und", "canonical", "potato") in keys
    assert ("ru", "translation", "картофель") in keys
    assert ("uk", "translation") not in {(k[0], k[1]) for k in keys}
    assert ("ru", "alias", "картошка") in keys
    assert sum(1 for key in keys if key[2] == "картошка") == 1
    assert ("ru", "transliteration", "kartofel") in keys


def test_score_tiers_and_language_bonus():
    assert score_term("canonical", "potato", "potato", "und", "ru") == (100, 6)
    assert score_term("translation", "картофель", "картофель", "ru", "ru")[0] == 93
    assert score_term("translation", "картофель", "картофель", "ru", "en")[0] == 90
    assert score_term("alias", "картошка", "картошка", "ru", "ru")[0] == 83
    assert score_term("transliteration", "kartofel", "kartofel", "ru", "ru")[0] == 70
    assert score_term("canonical", "potato bread", "potato", "und", "ru")[0] == 60
    assert score_term("alias", "potato bread", "potato", "en", "en")[0] == 55
    assert score_term("transliteration", "kartofel", "kart", "ru", "ru")[0] == 45
    assert score_term("canonical", "potato", "zzz", "und", "ru") is None


# ---------------------------------------------------------------------------
# Search endpoint
# ---------------------------------------------------------------------------


def test_search_exact_name_translation_alias_and_case(search_db):
    ids = _seed(search_db)

    assert _search(search_db, "potato")["items"][0]["source_id"] == "search:potato"
    assert _search(search_db, "POTATO")["items"][0]["source_id"] == "search:potato"
    assert _search(search_db, "  potato  ")["items"][0]["source_id"] == "search:potato"
    assert _search(search_db, "Картофель")["items"][0]["source_id"] == "search:potato"
    assert _search(search_db, "картошка")["items"][0]["source_id"] == "search:potato"

    exact = _search(search_db, "potato")
    assert {item["source_id"] for item in exact["items"]} >= {
        "search:potato", "search:potato-bread"
    }
    assert ids["potato"].id == int(exact["items"][0]["id"].split(":")[1])


def test_search_transliteration_and_diacritics(search_db):
    _seed(search_db)

    for query in ("kartofel", "kartoplia", "kartoplya", "kartoshka"):
        response = _search(search_db, query)
        assert response["items"], query
        assert response["items"][0]["source_id"] == "search:potato", query

    assert _search(search_db, "cafe")["items"][0]["source_id"] == "search:cafe"
    assert _search(search_db, "CAFÉ")["items"][0]["source_id"] == "search:cafe"


def test_search_prefix_order_and_limit(search_db):
    _seed(search_db)

    response = _search(search_db, "pot")
    source_ids = [item["source_id"] for item in response["items"]]
    assert source_ids[0] == "search:potato"
    assert "search:potato-bread" in source_ids

    limited = _search(search_db, "pot", limit=1)
    assert limited["count"] == 1


def test_search_language_is_a_preference_not_a_filter(search_db):
    _seed(search_db)

    for language in ("ru", "uk", "en", "de"):
        response = _search(search_db, "potato", lang=language)
        assert response["items"][0]["source_id"] == "search:potato", language
        assert response["language"] == language
        expected = language if language in {"ru", "uk", "en"} else "en"
        assert response["items"][0]["display_language"] == expected, language


def test_search_barcode_has_top_priority(search_db):
    _seed(search_db)

    response = _search(search_db, "4000000000017")
    assert response["items"][0]["source_id"] == "search:potato"


def test_search_community_barcode_has_top_priority(search_db):
    barcode = str(700_000_000_000 + uuid.uuid4().int % 100_000_000_000)
    created = search_db.client.post(
        f"{PREFIX}/nutrition/catalog",
        json={
            "barcode": barcode, "name": "Search test barcode product", "brand": "X",
            "kcal": 60, "protein": 2, "carbs": 3, "fat": 2,
        },
    )
    assert created.status_code == 201, created.text

    response = _search(search_db, barcode)
    assert response["items"][0]["source"] == "community"
    assert response["items"][0]["name"] == "Search test barcode product"


def test_search_includes_community_products(search_db):
    barcode = str(700_000_000_000 + uuid.uuid4().int % 100_000_000_000)
    created = search_db.client.post(
        f"{PREFIX}/nutrition/catalog",
        json={
            "barcode": barcode, "name": "Search test Café", "brand": "X",
            "kcal": 40, "protein": 1, "carbs": 2, "fat": 1,
        },
    )
    assert created.status_code == 201, created.text

    response = _search(search_db, "cafe")
    names = [item["name"] for item in response["items"]]
    assert "Search test Café" in names


def test_search_keeps_canonical_identity_and_nutrients(search_db):
    _seed(search_db)

    searched = _search(search_db, "картофель", lang="en")["items"][0]
    listed = search_db.client.get(
        f"{PREFIX}/nutrition/catalog-items", params={"lang": "en", "limit": 2000}
    ).json()["items"]
    original = next(
        item for item in listed if item["source_id"] == "search:potato"
    )
    assert searched["id"] == original["id"]
    assert searched["nutrients"] == original["nutrients"]
    assert searched["kcal"] == 77
    assert searched["name"] == "Potato"
    assert "submitted_by" not in searched


def test_search_validates_query_and_language(search_db):
    _seed(search_db)

    empty = search_db.client.get(f"{PREFIX}/nutrition/catalog/search", params={"q": ""})
    assert empty.status_code == 422
    invalid_lang = search_db.client.get(
        f"{PREFIX}/nutrition/catalog/search", params={"q": "potato", "lang": "!!"}
    )
    assert invalid_lang.status_code == 422


# ---------------------------------------------------------------------------
# Translation workflow
# ---------------------------------------------------------------------------


def test_translation_export_import_and_searchability(search_db):
    ids = _seed(search_db)
    db = search_db.db

    export = export_untranslated(db, "de")
    exported = {row["source_id"]: row for row in export}
    assert "search:potato" in exported
    assert exported["search:potato"]["name"] == ""
    assert exported["search:potato"]["name_en"] is None

    dry = import_translations(db, [{
        "source": "test", "source_id": "search:potato", "language": "de",
        "name": "Kartoffel",
    }], commit=False)
    assert dry["imported"] == 1 and dry["dry_run"] is True
    assert db.query(FoodTranslation).filter_by(
        food_id=ids["potato"].id, language="de"
    ).count() == 0

    committed = import_translations(db, [{
        "source": "test", "source_id": "search:potato", "language": "de",
        "name": "Kartoffel",
    }], commit=True)
    assert committed["imported"] == 1
    assert committed["terms"] > 0

    response = _search(search_db, "Kartoffel", lang="de")
    assert response["items"][0]["source_id"] == "search:potato"


def test_machine_translation_stays_out_of_search_until_approved(search_db):
    ids = _seed(search_db)
    db = search_db.db

    stored = import_translations(db, [{
        "source": "test", "source_id": "search:potato", "language": "de",
        "name": "Kartoffel", "translation_source": "machine",
        "translation_status": "machine", "review_status": "pending",
    }], commit=True)
    assert stored["imported"] == 1
    assert db.query(FoodSearchTerm).filter_by(
        food_id=ids["potato"].id, normalized="kartoffel"
    ).count() == 0
    assert _search(search_db, "Kartoffel")["items"] == []

    approved = import_translations(db, [{
        "source": "test", "source_id": "search:potato", "language": "de",
        "name": "Kartoffel", "translation_source": "machine",
        "translation_status": "machine", "review_status": "approved",
    }], commit=True)
    assert approved["updated"] == 1
    assert _search(search_db, "Kartoffel")["items"][0]["source_id"] == "search:potato"


def test_verified_translation_requires_approved_review_and_no_downgrade(search_db):
    ids = _seed(search_db)
    db = search_db.db

    invalid = import_translations(db, [{
        "source": "test", "source_id": "search:potato", "language": "de",
        "name": "Kartoffel", "translation_status": "verified",
        "review_status": "pending",
    }], commit=True)
    assert invalid["errors"]
    assert invalid["imported"] == 0

    downgrade = import_translations(db, [{
        "source": "test", "source_id": "search:potato", "language": "ru",
        "name": "Картошка", "translation_status": "machine",
        "review_status": "pending",
    }], commit=True)
    assert downgrade["skipped"] == 1
    existing = db.query(FoodTranslation).filter_by(
        food_id=ids["potato"].id, language="ru"
    ).one()
    assert existing.name == "Картофель"
    assert existing.review_status == "approved"


def test_reindex_rebuilds_all_terms(search_db):
    ids = _seed(search_db)
    db = search_db.db

    db.query(FoodSearchTerm).delete()
    db.commit()
    assert _search(search_db, "potato")["items"] == []

    planned = reindex_search(db, commit=False)
    assert planned["terms"] > 0
    assert db.query(FoodSearchTerm).count() == 0

    rebuilt = reindex_search(db, commit=True)
    assert rebuilt["terms"] > 0
    assert _search(search_db, "картофель")["items"][0]["source_id"] == "search:potato"
    assert db.query(FoodSearchTerm).filter_by(
        food_id=ids["potato"].id, term_type="transliteration"
    ).count() > 0
