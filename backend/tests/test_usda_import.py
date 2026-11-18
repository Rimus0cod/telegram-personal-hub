"""USDA FoodData Central adapter and import pipeline tests.

All datasets are tiny local fixtures; nothing downloads from USDA and no test
calls an external API.
"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.models.models import (
    CatalogItem,
    FoodSource,
    FoodSearchTerm,
    FoodServing,
    FoodTranslation,
    NutrientProfile,
)
from tools import catalog_import
from tools.catalog_import import import_records, normalize_v2_record
from tools.catalog_sources import usda

FIXTURES = Path(__file__).parent / "fixtures" / "usda"
FOUNDATION_JSON = FIXTURES / "foundation.json"
BRANDED_JSON = FIXTURES / "branded.json"
FNDDS_JSON = FIXTURES / "fndds.json"
SR_LEGACY_CSV = FIXTURES / "sr_legacy_csv"
VERSION = "2026-10-29"


def _by_id(records):
    return {record["source_id"]: record for record in records}


@pytest.fixture
def catalog_db(api):
    """API fixture with a clean USDA slice before and after each test."""
    def reset():
        # ORM delete so SQLite also cascades to translations/profiles/servings.
        for item in api.db.query(CatalogItem).filter(CatalogItem.source == "usda").all():
            api.db.delete(item)
        api.db.commit()

    reset()
    try:
        yield api
    finally:
        reset()


# ---------------------------------------------------------------------------
# Parsing and normalization
# ---------------------------------------------------------------------------


def test_stage_foundation_json_maps_nutrients_and_servings():
    records, report = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    indexed = _by_id(records)

    assert report["processed"] == 8
    assert report["valid"] == 5
    assert report["selected"] == 5
    assert report["valid_not_selected"] == 0
    assert report["error_count"] == 2
    assert report["duplicate_count"] == 1
    assert report["dataset_version"] == VERSION
    assert report["source"] == "usda"
    assert report["types"] == {"ingredient": 5}

    chicken = indexed["1001"]
    assert chicken["source"] == "usda"
    assert chicken["name_original"] == "Chicken, breast, raw"
    assert chicken["translations"][0]["language"] == "en"
    assert chicken["category"] == "Мясо и рыба"
    assert chicken["nutrients"] == {
        "reference_amount": 100.0,
        "reference_unit": "g",
        "kcal": 120.0,
        "protein": 23.1,  # higher dataPoints wins over the 22.0 duplicate
        "carbs": 0.0,
        "fat": 1.5,
        "fiber": 0.0,
        "sugar": None,
        "sodium": 45.0,
        "saturated_fat": 0.5,
    }
    assert chicken["preparation_state"] == "raw"
    assert chicken["approximate"] is False
    assert chicken["source_url"] == "https://fdc.nal.usda.gov/food-details/1001/nutrients"
    assert [serving["grams"] for serving in chicken["servings"]] == [140.0, 85.0]
    assert chicken["servings"][0]["is_default"] is True
    assert chicken["servings"][1]["is_default"] is False


def test_kj_energy_and_mass_unit_conversions():
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    indexed = _by_id(records)

    rice = indexed["1002"]["nutrients"]
    assert rice["kcal"] == pytest.approx(109.9426, abs=1e-4)  # 460 kJ
    assert rice["sodium"] == pytest.approx(1.0)  # 0.001 g -> 1 mg

    tomato = indexed["1006"]["nutrients"]
    assert tomato["sodium"] == pytest.approx(5.0)  # 5000 µg -> 5 mg


def test_missing_values_stay_null_and_measured_zero_stays_zero():
    records, report = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    indexed = _by_id(records)

    assert indexed["1001"]["nutrients"]["fiber"] == 0.0
    assert indexed["1001"]["nutrients"]["sugar"] is None
    assert indexed["1002"]["nutrients"]["fiber"] is None
    assert indexed["1005"]["nutrients"]["sugar"] == 0.0
    assert report["missing_optional"]["fiber"] == 3
    assert report["missing_optional"]["sugar"] == 4


def test_invalid_records_are_reported_with_reasons():
    _, report = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    errors = {item["fdc_id"]: item["error"] for item in report["errors"]}

    assert "missing required nutrient(s): fat" in errors[1003]
    assert "exceeds the limit" in errors[1004]


def test_unknown_nutrient_ids_are_counted_and_ignored():
    records, report = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    assert report["unknown_nutrients"] == {"9999": 1}
    assert "9999" not in json.dumps(records)


def test_names_are_cleaned_but_not_translated():
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    oats = _by_id(records)["1007"]
    assert oats["name_original"] == "Oats, dry"
    assert oats["translations"][0]["name"] == "Oats, dry"
    languages = {item["language"] for item in oats["translations"]}
    assert languages == {"en"}


def test_category_type_and_preparation_mapping():
    records, _ = usda.stage_dataset(SR_LEGACY_CSV, dataset_version=VERSION)
    indexed = _by_id(records)

    butter = indexed["2001"]
    assert butter["category"] == "Молочное"
    assert butter["type"] == "ingredient"
    assert butter["nutrients"]["fiber"] == 0.0
    assert butter["nutrients"]["saturated_fat"] == pytest.approx(51.368)

    cake = indexed["2002"]
    assert cake["category"] == "Хлеб и выпечка"
    assert cake["type"] == "prepared_meal"
    assert cake["nutrients"]["sugar"] is None
    # The 0 g cup portion is rejected; the 95 g piece becomes the default.
    assert [(s["unit"], s["grams"], s["is_default"]) for s in cake["servings"]] == [
        ("piece", 95.0, True)
    ]

    milk = indexed["2003"]
    assert milk["servings"][0]["unit"] == "cup"
    assert milk["servings"][0]["grams"] == 244.0


def test_csv_staging_from_directory_and_zip(tmp_path):
    from_dir, report_dir = usda.stage_dataset(SR_LEGACY_CSV, dataset_version=VERSION)
    assert report_dir["processed"] == 5
    assert report_dir["valid"] == 3
    assert report_dir["error_count"] == 1
    assert {item["fdc_id"] for item in report_dir["errors"]} == {2005}

    archive = tmp_path / "sr_legacy.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for path in SR_LEGACY_CSV.glob("*.csv"):
            handle.write(path, f"sr_legacy_folder/{path.name}")

    from_zip, report_zip = usda.stage_dataset(archive, dataset_version=VERSION)
    assert from_zip == from_dir
    assert report_zip["valid"] == report_dir["valid"]


def test_json_dataset_inside_a_zip(tmp_path):
    archive = tmp_path / "foundation.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(FOUNDATION_JSON, "FoodData_Central_foundation_food_json/foundation.json")

    from_zip, report = usda.stage_dataset(archive, dataset_version=VERSION)
    from_file, file_report = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)

    assert from_zip == from_file
    assert report["valid"] == file_report["valid"]
    assert report["processed"] == file_report["processed"]


def test_fndds_survey_food_is_a_prepared_meal_with_wweia_category():
    records, report = usda.stage_dataset(
        FNDDS_JSON, data_types=["Survey (FNDDS)"], dataset_version="2024-10-31"
    )
    soup = _by_id(records)["5001"]

    assert soup["type"] == "prepared_meal"
    assert soup["category"] == "Готовые блюда"
    assert soup["usda"]["category"] == "Soups, broth-based"
    assert soup["nutrients"]["sodium"] == 342.0
    # FNDDS stores numeric modifier codes; "Guideline amount" rows are not
    # portions, and the numbered household measure becomes the default.
    assert [(s["unit"], s["description"], s["grams"]) for s in soup["servings"]] == [
        ("cup", "1 cup", 245.0),
        ("oz", "1 fl oz", 30.5),
        ("portion", "Quantity not specified", 245.0),
        ("RACC", "1 RACC", 245.0),
    ]
    assert soup["servings"][0]["is_default"] is True
    assert soup["servings"][-1]["is_default"] is False
    assert report["types"] == {"prepared_meal": 1}


def test_data_type_filter_excludes_other_datasets():
    records, report = usda.stage_dataset(
        FOUNDATION_JSON, data_types=["Survey (FNDDS)"], dataset_version=VERSION
    )
    assert records == []
    assert report["valid"] == 0
    assert report["processed"] == 8

    with pytest.raises(usda.UsdaAdapterError, match="unsupported data type"):
        usda.stage_dataset(FOUNDATION_JSON, data_types=["Branded"])


def test_branded_json_is_rejected():
    with pytest.raises(usda.UsdaAdapterError, match="Branded Foods bulk import is not enabled"):
        usda.stage_dataset(BRANDED_JSON)


def test_foundation_only_csv_gets_actionable_error(tmp_path):
    (tmp_path / "foundation_food.csv").write_text('"fdc_id","NDB_number"\n', encoding="utf-8")
    with pytest.raises(usda.UsdaAdapterError, match="cannot be imported on its own"):
        usda.stage_dataset(tmp_path)


def test_json_streaming_survives_chunk_boundaries():
    payload = '{"FoundationFoods": [{"fdcId": 1}, {"fdcId": 2}, {"fdcId": 3}]}'
    handle = io.StringIO(payload)
    assert list(usda.iter_json_array(handle, chunk_size=5)) == [
        {"fdcId": 1}, {"fdcId": 2}, {"fdcId": 3},
    ]


def test_dataset_version_is_detected_from_the_file_name():
    assert usda.detect_dataset_version(
        "FoodData_Central_foundation_food_json_2026-10-29.zip"
    ) == "2026-10-29"
    assert usda.detect_dataset_version("foundation.json") is None


def test_select_balanced_spreads_categories():
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    selected = usda.select_balanced(records, 3)
    assert len(selected) == 3
    assert len({record["category"] for record in selected}) == 3

    limited, report = usda.stage_dataset(
        FOUNDATION_JSON, dataset_version=VERSION, limit=2
    )
    assert len(limited) == 2
    assert report["selected"] == 2
    assert report["valid"] == 5
    assert report["valid_not_selected"] == 3


def test_unit_conversion_rejects_unknown_units():
    with pytest.raises(usda.UsdaAdapterError, match="unsupported unit"):
        usda.convert_amount(1.0, "iu", "g")
    with pytest.raises(usda.UsdaAdapterError, match="cannot convert"):
        usda.convert_amount(1.0, "mg", "kJ")


def test_racc_portion_is_not_preferred_as_default():
    servings = usda.extract_servings([
        {"gramWeight": 30, "amount": 1, "measureUnit": {"name": "RACC"}},
        {"gramWeight": 140, "amount": 1, "measureUnit": {"name": "cup"}},
    ])
    assert [serving["unit"] for serving in servings] == ["cup", "RACC"]
    assert servings[0]["is_default"] is True
    assert servings[1]["is_default"] is False


def test_usda_cli_rejects_staging_json_with_guidance(tmp_path, capsys):
    staging = tmp_path / "staging.json"
    staging.write_text(json.dumps([{"source": "usda", "source_id": "1"}]), encoding="utf-8")

    assert catalog_import._looks_like_staging_json(staging) is True
    assert catalog_import._looks_like_staging_json(FOUNDATION_JSON) is False

    code = catalog_import.main(["usda", "import", "--input", str(staging)])
    assert code == 1
    assert "already a staging JSON array" in capsys.readouterr().err


def test_staging_record_is_import_compatible():
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    normalized = normalize_v2_record(records[0])
    assert normalized["source"] == "usda"
    assert normalized["_dataset_version"] == VERSION
    assert normalized["_nutrients"]["sodium"] == 45.0
    assert normalized["_servings"]


# ---------------------------------------------------------------------------
# Import pipeline
# ---------------------------------------------------------------------------


def test_dry_run_does_not_touch_the_database(catalog_db):
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    report = import_records(catalog_db.db, records, commit=False)

    assert report["valid"] == 5
    assert report["imported"] == 0
    assert report["dry_run"] is True
    assert catalog_db.db.query(CatalogItem).filter(CatalogItem.source == "usda").count() == 0


def test_commit_repeat_and_english_only_translations(catalog_db):
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)

    first = import_records(catalog_db.db, records, commit=True)
    assert first["imported"] == 5
    assert first["translations"] == 5
    assert first["nutrient_profiles"] == 5
    assert first["servings"] == 6

    chicken = (
        catalog_db.db.query(CatalogItem)
        .filter(CatalogItem.source == "usda", CatalogItem.source_id == "1001")
        .one()
    )
    assert chicken.name == "Chicken, breast, raw"
    assert chicken.kcal == 120.0
    assert chicken.protein == 23.1
    assert chicken.category == "Мясо и рыба"
    assert chicken.preparation_state == "raw"
    assert chicken.approximate is False
    assert chicken.source_url.endswith("/1001/nutrients")

    translations = {
        row.language: row
        for row in catalog_db.db.query(FoodTranslation).filter_by(food_id=chicken.id)
    }
    assert set(translations) == {"en"}
    assert translations["en"].translation_source == "usda"
    assert translations["en"].is_primary is True

    profile = (
        catalog_db.db.query(NutrientProfile)
        .filter_by(food_id=chicken.id, is_primary=True)
        .one()
    )
    assert profile.sugar is None
    assert profile.fiber == 0.0
    assert profile.dataset_version == VERSION
    assert profile.source_record_id == "1001"

    servings = catalog_db.db.query(FoodServing).filter_by(food_id=chicken.id).all()
    assert len(servings) == 2

    again = import_records(catalog_db.db, records, commit=True)
    assert again["imported"] == 0
    assert again["skipped"] == 5
    assert all("source_id already imported" in item["reason"] for item in again["skips"])
    assert catalog_db.db.query(CatalogItem).filter(CatalogItem.source == "usda").count() == 5


def test_partial_failure_keeps_valid_rows_and_reports_errors(catalog_db):
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    broken = json.loads(json.dumps(records[0]))
    broken["source_id"] = "9999"
    broken["name_original"] = "Broken record"
    broken["translations"][0]["name"] = "Broken record"
    broken["nutrients"]["protein"] = None

    report = import_records(catalog_db.db, [records[0], broken, records[1]], commit=True)

    assert report["imported"] == 2
    assert len(report["errors"]) == 1
    assert "protein is required" in report["errors"][0]["error"]
    assert catalog_db.db.query(CatalogItem).filter(CatalogItem.source == "usda").count() == 2


def test_import_is_transactional(catalog_db, monkeypatch):
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    original = catalog_import.NutrientProfile
    state = {"count": 0}

    class FailingNutrientProfile:
        def __new__(cls, **kwargs):
            state["count"] += 1
            if state["count"] == 2:
                raise RuntimeError("simulated database failure")
            return original(**kwargs)

    monkeypatch.setattr(catalog_import, "NutrientProfile", FailingNutrientProfile)

    with pytest.raises(RuntimeError, match="simulated database failure"):
        import_records(catalog_db.db, records[:2], commit=True)

    assert catalog_db.db.query(CatalogItem).filter(CatalogItem.source == "usda").count() == 0
    assert catalog_db.db.query(FoodTranslation).count() == 0


def test_license_permission_is_enforced_from_the_registry(catalog_db):
    source = catalog_db.db.query(FoodSource).filter_by(code="usda").one()
    original = source.storage_permission
    source.storage_permission = False
    catalog_db.db.commit()
    try:
        records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
        report = import_records(catalog_db.db, records, commit=True)
        assert report["imported"] == 0
        assert report["skipped"] == 5
        assert "does not permit permanent storage" in report["skips"][0]["reason"]
    finally:
        source.storage_permission = original
        catalog_db.db.commit()


def test_exact_canonical_name_conflict_is_skipped(catalog_db):
    catalog_db.db.add(CatalogItem(
        source="manual", source_id="manual:chicken", name="Chicken, breast, raw",
        name_original="Chicken, breast, raw", category="Мясо и рыба",
        type="ingredient", kcal=100, protein=1, carbs=1, fat=1,
        serving_size=100, serving_unit="g", is_active=True,
    ))
    catalog_db.db.commit()
    try:
        records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
        chicken = _by_id(records)["1001"]
        report = import_records(catalog_db.db, [chicken], commit=True)
        assert report["imported"] == 0
        assert "duplicate curated name" in report["skips"][0]["reason"]
    finally:
        catalog_db.db.query(CatalogItem).filter(
            CatalogItem.source == "manual"
        ).delete()
        catalog_db.db.commit()


def test_similar_names_are_reported_for_review_without_merging(catalog_db):
    catalog_db.db.add(CatalogItem(
        source="manual", source_id="manual:chicken-breast", name="Chicken breast raw",
        name_original="Chicken breast raw", category="Мясо и рыба",
        type="ingredient", kcal=100, protein=1, carbs=1, fat=1,
        serving_size=100, serving_unit="g", is_active=True,
    ))
    catalog_db.db.commit()
    try:
        records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
        chicken = _by_id(records)["1001"]
        report = import_records(catalog_db.db, [chicken], commit=True)

        assert report["imported"] == 1
        assert report["possible_duplicates"]
        match = report["possible_duplicates"][0]["matches"][0]
        assert match["name"] == "Chicken breast raw"
        assert match["source"] == "manual"
        assert match["score"] == 1.0
        # The USDA record is a separate canonical row, not a merge.
        assert catalog_db.db.query(CatalogItem).filter(
            CatalogItem.source == "manual"
        ).count() == 1
    finally:
        catalog_db.db.query(CatalogItem).filter(
            CatalogItem.source == "manual"
        ).delete()
        catalog_db.db.commit()


def test_import_builds_search_terms_and_is_searchable(catalog_db):
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    import_records(catalog_db.db, records, commit=True)

    chicken = (
        catalog_db.db.query(CatalogItem)
        .filter(CatalogItem.source == "usda", CatalogItem.source_id == "1001")
        .one()
    )
    terms = catalog_db.db.query(FoodSearchTerm).filter_by(food_id=chicken.id).all()
    normalized = {(term.term_type, term.normalized) for term in terms}
    assert ("canonical", "chicken breast raw") in normalized
    assert ("translation", "chicken breast raw") in normalized

    response = catalog_db.client.get(
        "/api/v1/nutrition/catalog/search",
        params={"q": "chicken breast raw", "lang": "en"},
    )
    assert response.status_code == 200, response.text
    first = response.json()["items"][0]
    assert first["source_id"] == "1001"
    assert first["nutrients"]["protein"] == 23.1

    # A repeated import must not duplicate the derived rows.
    before = catalog_db.db.query(FoodSearchTerm).count()
    import_records(catalog_db.db, records, commit=True)
    assert catalog_db.db.query(FoodSearchTerm).count() == before


def test_api_serves_imported_usda_food_without_runtime_provider(catalog_db):
    records, _ = usda.stage_dataset(FOUNDATION_JSON, dataset_version=VERSION)
    import_records(catalog_db.db, records, commit=True)

    response = catalog_db.client.get(
        "/api/v1/nutrition/catalog-items", params={"lang": "ru"}
    )
    assert response.status_code == 200
    items = {row["source_id"]: row for row in response.json()["items"] if row["source"] == "usda"}
    chicken = items["1001"]
    assert chicken["name"] == "Chicken, breast, raw"  # canonical fallback, no fake translation
    assert chicken["nutrients"]["protein"] == 23.1
    assert chicken["nutrients"]["sugar"] is None
    assert set(chicken["translations"]) == {"en"}
    assert chicken["barcode"] is None
    assert chicken["review_status"] == "approved"
