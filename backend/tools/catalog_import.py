"""Curated catalog import tooling.

This module is intentionally outside the FastAPI runtime.  It is the only place
that talks to FatSecret.  The Nutrition API and the Mini App never call
FatSecret.

FatSecret's current Terms of Use only allow the following values to be stored
indefinitely: ``food_id``, ``serving_id`` and other identifiers.  Names,
nutrition values and descriptions are not storable indefinitely and must be
removed within 24 hours unless the account has a separate written custom-storage
permission.  Consequently this tool provides a FatSecret *preview* command but
refuses to write FatSecret records into PostgreSQL.

The PostgreSQL catalog can be filled from our own ``builtin`` seed, public-domain
USDA data, documented recipes, or manually reviewed JSON.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.models import (
    CatalogItem,
    CommunityFood,
    FoodAlias,
    FoodBarcode,
    FoodSearchTerm,
    FoodServing,
    FoodSource,
    FoodTranslation,
    NutrientProfile,
    RecipeDetails,
    RecipeIngredient,
)
from app.services.food_search import (
    build_search_terms,
    rebuild_search_terms_for_food_id,
    terms_for_food,
)
from tools.recipes import RecipeValidationError, prepare_recipe


FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
FATSECRET_SEARCH_URL = "https://platform.fatsecret.com/rest/foods/search/v1"
FATSECRET_FOOD_URL = "https://platform.fatsecret.com/rest/food/v4"

VALID_TYPES = {"ingredient", "prepared_meal"}
VALID_UNITS = {"g", "ml", "piece"}
# Permission checks read the ``food_sources`` registry rows, not a hardcoded
# list of provider names.  Adding an approved provider is a registry row plus a
# reviewed adapter, never a code change here.
FATSECRET_PREVIEW_TTL = timedelta(hours=24)

# Deliberately small and hand-reviewed.  Unknown names stay untranslated and
# are marked for review instead of being machine-translated.
FATSECRET_TRANSLATIONS: dict[str, dict[str, str]] = {
    "chicken breast": {"ru": "Куриная грудка", "uk": "Куряча грудка"},
    "chicken thigh": {"ru": "Куриное бедро", "uk": "Куряче стегно"},
    "beef": {"ru": "Говядина", "uk": "Яловичина"},
    "pork": {"ru": "Свинина", "uk": "Свинина"},
    "salmon": {"ru": "Лосось", "uk": "Лосось"},
    "tuna": {"ru": "Тунец", "uk": "Тунець"},
    "egg": {"ru": "Яйцо", "uk": "Яйце"},
    "eggs": {"ru": "Яйца", "uk": "Яйця"},
    "milk": {"ru": "Молоко", "uk": "Молоко"},
    "cottage cheese": {"ru": "Творог", "uk": "Сир кисломолочний"},
    "yogurt": {"ru": "Йогурт", "uk": "Йогурт"},
    "cheese": {"ru": "Сыр", "uk": "Сир"},
    "potato": {"ru": "Картофель", "uk": "Картопля"},
    "rice": {"ru": "Рис", "uk": "Рис"},
    "buckwheat": {"ru": "Гречка", "uk": "Гречка"},
    "oats": {"ru": "Овсянка", "uk": "Вівсянка"},
    "pasta": {"ru": "Макароны", "uk": "Макарони"},
    "bread": {"ru": "Хлеб", "uk": "Хліб"},
    "borscht": {"ru": "Борщ", "uk": "Борщ"},
    "omelette": {"ru": "Омлет", "uk": "Омлет"},
    "pancakes": {"ru": "Блины", "uk": "Млинці"},
    "syrupy pancakes": {"ru": "Оладьи", "uk": "Оладки"},
    "dumplings": {"ru": "Вареники", "uk": "Вареники"},
    "meatballs": {"ru": "Тефтели", "uk": "Тефтелі"},
    "cutlet": {"ru": "Котлета", "uk": "Котлета"},
    "salad": {"ru": "Салат", "uk": "Салат"},
    "soup": {"ru": "Суп", "uk": "Суп"},
}


class ImportValidationError(ValueError):
    """A record is missing required data or has unsafe values."""


def normalized_name(value: str) -> str:
    return value.lower().replace("ё", "е").replace("’", "").replace("'", "").strip()


def _optional_number(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ImportValidationError(f"{field} must be a number or null") from None
    if number < 0:
        raise ImportValidationError(f"{field} must be non-negative")
    return number


def normalize_v2_record(
    raw: dict[str, Any],
    *,
    source_default: str = "manual",
) -> dict[str, Any]:
    """Validate a multilingual record with explicit nutrients and servings."""
    translations_raw = raw.get("translations")
    if not isinstance(translations_raw, list) or not translations_raw:
        raise ImportValidationError("translations must be a non-empty array")

    translations = []
    languages = set()
    for entry in translations_raw:
        if not isinstance(entry, dict):
            raise ImportValidationError("each translation must be an object")
        language = str(entry.get("language") or "").strip().lower()
        name = str(entry.get("name") or "").strip()
        if not language or len(language) > 16:
            raise ImportValidationError("translation language must contain 1-16 characters")
        if not name or len(name) > 200:
            raise ImportValidationError("translation name must contain 1-200 characters")
        if language in languages:
            raise ImportValidationError(f"duplicate translation language: {language}")
        languages.add(language)
        translations.append({
            "language": language,
            "name": name,
            "description": (str(entry.get("description")).strip() or None)
            if entry.get("description") is not None else None,
            "translation_source": str(entry.get("translation_source") or "manual")[:32],
            "translation_status": str(entry.get("translation_status") or "draft")[:16],
            "review_status": str(entry.get("review_status") or "pending")[:16],
            "is_primary": bool(entry.get("is_primary", False)),
        })
    if not any(item["is_primary"] for item in translations):
        translations[0]["is_primary"] = True

    primary = next(item for item in translations if item["is_primary"])
    name_ru = next((item["name"] for item in translations if item["language"] == "ru"), None)
    name_uk = next((item["name"] for item in translations if item["language"] == "uk"), None)

    nutrients_raw = raw.get("nutrients") or {}
    if not isinstance(nutrients_raw, dict):
        raise ImportValidationError("nutrients must be an object")
    recipe_raw = raw.get("recipe")
    if recipe_raw is not None and not isinstance(recipe_raw, dict):
        raise ImportValidationError("recipe must be an object")
    if recipe_raw is not None and str(raw.get("type") or "").strip() != "prepared_meal":
        raise ImportValidationError("recipe is only allowed for prepared_meal records")
    reference_amount = nutrients_raw.get("reference_amount", 100)
    reference_unit = str(nutrients_raw.get("reference_unit") or "g").strip().lower()
    try:
        reference_amount = float(reference_amount)
    except (TypeError, ValueError):
        raise ImportValidationError("reference_amount must be a number") from None
    if not (0.1 <= reference_amount <= 100000):
        raise ImportValidationError("reference_amount must be between 0.1 and 100000")
    if reference_unit not in {"g", "ml"}:
        raise ImportValidationError("reference_unit must be g or ml")

    nutrient_values = {
        field: _optional_number(nutrients_raw.get(field), field)
        for field in ("kcal", "protein", "carbs", "fat", "fiber", "sugar", "sodium", "saturated_fat")
    }
    for field in ("kcal", "protein", "carbs", "fat"):
        if nutrient_values[field] is None and recipe_raw is None:
            raise ImportValidationError(f"nutrients.{field} is required")
    if nutrient_values["kcal"] is not None and nutrient_values["kcal"] > 2000:
        raise ImportValidationError("nutrients.kcal must be <= 2000 per reference amount")

    aliases = []
    aliases_raw = raw.get("aliases") or []
    if not isinstance(aliases_raw, list):
        raise ImportValidationError("aliases must be an array")
    for entry in aliases_raw:
        if isinstance(entry, str):
            alias, language, alias_type = entry.strip(), "und", "search"
        elif isinstance(entry, dict):
            alias = str(entry.get("alias") or "").strip()
            language = str(entry.get("language") or "und").strip().lower()[:16]
            alias_type = str(entry.get("alias_type") or "search")[:16]
        else:
            raise ImportValidationError("each alias must be a string or object")
        if not alias or len(alias) > 200:
            raise ImportValidationError("alias must contain 1-200 characters")
        aliases.append({"language": language or "und", "alias": alias, "alias_type": alias_type})

    servings_raw = raw.get("servings") or [{
        "amount": reference_amount, "unit": reference_unit,
        "grams": reference_amount, "description": f"{reference_amount:g} {reference_unit}",
        "is_default": True,
    }]
    if not isinstance(servings_raw, list) or not servings_raw:
        raise ImportValidationError("servings must be a non-empty array")
    servings = []
    for entry in servings_raw:
        if not isinstance(entry, dict):
            raise ImportValidationError("each serving must be an object")
        amount = _optional_number(entry.get("amount"), "serving amount")
        grams = _optional_number(entry.get("grams"), "serving grams")
        unit = str(entry.get("unit") or "g").strip()[:16]
        if amount is None or amount <= 0:
            raise ImportValidationError("serving amount must be positive")
        servings.append({
            "amount": amount, "unit": unit, "grams": grams,
            "description": (str(entry.get("description")).strip()[:120] or None)
            if entry.get("description") is not None else None,
            "is_default": bool(entry.get("is_default", False)),
        })
    if not any(item["is_default"] for item in servings):
        servings[0]["is_default"] = True
    default_serving = next(item for item in servings if item["is_default"])

    barcodes = []
    for value in raw.get("barcodes") or []:
        barcode = str(value).strip()
        if not (8 <= len(barcode) <= 14 and barcode.isdigit()):
            raise ImportValidationError("barcode must contain 8-14 digits")
        barcodes.append(barcode)

    flat = normalize_record({
        **raw,
        "name": primary["name"],
        "name_original": raw.get("name_original") or primary["name"],
        "name_ru": name_ru,
        "name_uk": name_uk,
        "category": raw.get("category"),
        "type": raw.get("type"),
        "kcal": nutrient_values["kcal"] if nutrient_values["kcal"] is not None else 0,
        "protein": nutrient_values["protein"] if nutrient_values["protein"] is not None else 0,
        "carbs": nutrient_values["carbs"] if nutrient_values["carbs"] is not None else 0,
        "fat": nutrient_values["fat"] if nutrient_values["fat"] is not None else 0,
        "serving_size": default_serving["grams"] or default_serving["amount"],
        "serving_unit": "g" if default_serving["grams"] else default_serving["unit"],
    }, source_default=source_default)
    flat["_translations"] = translations
    flat["_aliases"] = aliases
    flat["_nutrients"] = {
        "reference_amount": reference_amount,
        "reference_unit": reference_unit,
        **nutrient_values,
    }
    flat["_servings"] = servings
    flat["_barcodes"] = barcodes
    flat["_recipe"] = recipe_raw
    dataset_version = raw.get("dataset_version")
    flat["_dataset_version"] = (
        str(dataset_version).strip()[:64] or None
    ) if dataset_version else None
    return flat


def normalize_record(
    raw: dict[str, Any],
    *,
    source_default: str = "manual",
) -> dict[str, Any]:
    """Validate one JSON record and return a database-ready mapping."""

    if not isinstance(raw, dict):
        raise ImportValidationError("record must be an object")

    name = str(raw.get("name") or raw.get("name_ru") or raw.get("name_uk") or "").strip()
    category = str(raw.get("category") or "").strip()
    source = str(raw.get("source") or source_default).strip() or source_default
    record_type = str(raw.get("type") or "ingredient").strip()
    serving_unit = str(raw.get("serving_unit") or "g").strip()

    if not name or len(name) > 200:
        raise ImportValidationError("name must contain 1-200 characters")
    if not category or len(category) > 80:
        raise ImportValidationError("category must contain 1-80 characters")
    if source == "fatsecret":
        raise ImportValidationError(
            "FatSecret records are preview-only. Its Terms of Use allow only IDs to be "
            "stored indefinitely; nutrition values and names require written custom-storage "
            "permission. Use `fatsecret-search` and keep the preview for at most 24 hours."
        )
    if not 1 <= len(source) <= 32:
        raise ImportValidationError("source must contain 1-32 characters")
    if record_type not in VALID_TYPES:
        raise ImportValidationError("type must be ingredient or prepared_meal")
    if serving_unit not in VALID_UNITS:
        raise ImportValidationError("serving_unit must be g, ml or piece")

    def number(field: str, minimum: float = 0, maximum: float = 2000) -> float:
        try:
            value = float(raw.get(field))
        except (TypeError, ValueError):
            raise ImportValidationError(f"{field} must be a number") from None
        if not (minimum <= value <= maximum):
            raise ImportValidationError(
                f"{field} must be between {minimum} and {maximum}"
            )
        return value

    kcal = number("kcal", 0, 2000)
    protein = number("protein", 0, 1000)
    carbs = number("carbs", 0, 1000)
    fat = number("fat", 0, 1000)
    serving_size = number("serving_size", 0.1, 100000)

    source_id = raw.get("source_id")
    if source_id is not None:
        source_id = str(source_id).strip()
        if not source_id or len(source_id) > 200:
            raise ImportValidationError("source_id must contain 1-200 characters")

    preparation_state = raw.get("preparation_state")
    if preparation_state is not None:
        preparation_state = str(preparation_state).strip()[:16] or None

    return {
        "source": source,
        "source_id": source_id,
        "name": name,
        "name_original": str(raw.get("name_original") or name)[:200],
        "name_ru": str(raw.get("name_ru") or "")[:200] or None,
        "name_uk": str(raw.get("name_uk") or "")[:200] or None,
        "category": category,
        "type": record_type,
        "kcal": kcal,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
        "serving_size": serving_size,
        "serving_unit": serving_unit,
        "preparation_state": preparation_state,
        "approximate": bool(raw.get("approximate", False)),
        "source_url": (str(raw.get("source_url")).strip()[:500] or None)
        if raw.get("source_url")
        else None,
        "is_active": bool(raw.get("is_active", True)),
    }


def _record_key(record: dict[str, Any]) -> str:
    if record.get("source_id"):
        return f"{record['source']}:{record['source_id']}"
    return f"name:{normalized_name(record['name'])}:{normalized_name(record['category'])}"


def _catalog_index(session: Session) -> dict[str, Any]:
    """Load the existing catalog names once per import run.

    ``find_duplicate`` used to query every catalog row for every record.  The
    index keeps bulk imports linear while preserving identical messages.
    """
    curated = (
        session.query(CatalogItem.id, CatalogItem.source, CatalogItem.source_id,
                      CatalogItem.name)
        .all()
    )
    community = session.query(CommunityFood.id, CommunityFood.name).all()
    by_key: dict[tuple[str, str], int] = {}
    curated_names: list[tuple[int, str, str, set[str]]] = []
    for catalog_id, source, source_id, name in curated:
        if source_id is not None:
            by_key[(source, source_id)] = catalog_id
        curated_names.append((catalog_id, source, name, _name_tokens(name)))
    return {
        "by_key": by_key,
        "curated": curated_names,
        "community": [
            (catalog_id, name, _name_tokens(name)) for catalog_id, name in community
        ],
    }


def find_duplicate(
    session: Session,
    record: dict[str, Any],
    *,
    index: dict[str, Any] | None = None,
) -> str | None:
    index = index if index is not None else _catalog_index(session)

    if record.get("source_id"):
        existing_id = index["by_key"].get((record["source"], record["source_id"]))
        if existing_id is not None:
            return f"source_id already imported (catalog_foods.id={existing_id})"

    name_key = normalized_name(record["name"])
    for catalog_id, _source, name, _tokens in index["curated"]:
        if normalized_name(name) == name_key:
            return f"duplicate curated name (catalog_foods.id={catalog_id})"
    for catalog_id, name, _tokens in index["community"]:
        if normalized_name(name) == name_key:
            return f"duplicate community name (community_foods.id={catalog_id})"
    return None


_TOKEN_PATTERN = re.compile(r"[0-9a-zа-яё]+")
_POSSIBLE_DUPLICATE_THRESHOLD = 0.6


def _name_tokens(value: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(normalized_name(value)))


def possible_duplicates(
    index: dict[str, Any],
    record: dict[str, Any],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Find similar-but-not-identical catalog names for manual review.

    This never merges or skips anything.  A pair is reported when the names
    share at least two tokens and the Jaccard score reaches 0.6, which catches
    cases such as "Apples, raw, with skin" vs "Apples, raw, without skin".
    """
    tokens = _name_tokens(record["name"])
    if len(tokens) < 2:
        return []
    matches: list[dict[str, Any]] = []
    candidates = [
        (catalog_id, source, name, other)
        for catalog_id, source, name, other in index["curated"]
    ] + [
        (catalog_id, "community", name, other)
        for catalog_id, name, other in index["community"]
    ]
    for catalog_id, source, name, other in candidates:
        if len(other) < 2:
            continue
        overlap = tokens & other
        if len(overlap) < 2:
            continue
        score = len(overlap) / len(tokens | other)
        if score >= _POSSIBLE_DUPLICATE_THRESHOLD:
            matches.append({
                "catalog_id": catalog_id, "source": source,
                "name": name, "score": round(score, 2),
            })
    matches.sort(key=lambda item: (-item["score"], item["name"]))
    return matches[:limit]


def load_records(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("records", data.get("items", []))
    if not isinstance(data, list):
        raise ImportValidationError("input must be a JSON array or an object with records/items")
    return data


def _flat_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _synthetic_translations(record: dict[str, Any]) -> list[dict[str, Any]]:
    translations = []
    original = record.get("name_original") or record["name"]
    translations.append({
        "language": "und", "name": original, "description": None,
        "translation_source": record["source"], "translation_status": "verified",
        "review_status": "approved", "is_primary": not (record.get("name_ru") or record.get("name_uk")),
    })
    if record.get("name_ru") and record["name_ru"] != original:
        translations.append({
            "language": "ru", "name": record["name_ru"], "description": None,
            "translation_source": "manual", "translation_status": "verified",
            "review_status": "approved", "is_primary": not record.get("name_uk"),
        })
    if record.get("name_uk") and record["name_uk"] != original:
        translations.append({
            "language": "uk", "name": record["name_uk"], "description": None,
            "translation_source": "manual", "translation_status": "verified",
            "review_status": "approved", "is_primary": True,
        })
    if not any(item["is_primary"] for item in translations):
        translations[0]["is_primary"] = True
    return translations


def _has_translated_content(record: dict[str, Any]) -> bool:
    """True when the record ships names in a language other than the original."""
    original = record.get("name_original") or record["name"]
    for item in record.get("_translations") or []:
        if item["language"] != "und" and item["name"] != original:
            return True
    return any(
        record.get(key) and record[key] != original for key in ("name_ru", "name_uk")
    )


def _license_problem(session: Session, record: dict[str, Any]) -> str | None:
    """Enforce the registry's actual permission fields for one record.

    The check is field-based on purpose: approving a new provider means adding a
    ``food_sources`` row, not editing this module.
    """
    source = session.get(FoodSource, record["source"])
    if source is None:
        return f"unknown source: {record['source']}"
    if not source.storage_permission:
        return f"source {source.code} does not permit permanent storage"
    if source.approval_status != "approved":
        return f"source {source.code} approval status is {source.approval_status}"
    if not source.translation_permission and _has_translated_content(record):
        return f"source {source.code} does not permit translated content"
    return None


def import_records(
    session: Session,
    records: Iterable[dict[str, Any]],
    *,
    source_default: str = "manual",
    commit: bool = False,
) -> dict[str, Any]:
    raw_records = list(records)
    report: dict[str, Any] = {
        "processed": len(raw_records),
        "valid": 0,
        "imported": 0,
        "skipped": 0,
        "errors": [],
        "skips": [],
        "possible_duplicates": [],
        "translations": 0,
        "aliases": 0,
        "nutrient_profiles": 0,
        "servings": 0,
        "barcodes": 0,
        "recipes": 0,
        "recipe_ingredients": 0,
        "dry_run": not commit,
    }

    # Refuse a FatSecret batch before touching the database.
    for raw in raw_records:
        if isinstance(raw, dict) and str(raw.get("source") or "").lower() == "fatsecret":
            raise ImportValidationError(
                "FatSecret batch is preview-only and cannot be committed to PostgreSQL. "
                "Use the `fatsecret-search` command to create a temporary preview, or obtain "
                "written custom-storage permission and add a new reviewed import source."
            )

    seen: set[str] = set()
    seen_names: set[str] = set()
    seen_barcodes: set[str] = set()
    catalog_index = _catalog_index(session)
    pending: list[dict[str, Any]] = []
    for line_number, raw in enumerate(raw_records, start=1):
        try:
            if isinstance(raw, dict) and ("translations" in raw or "nutrients" in raw):
                record = normalize_v2_record(raw, source_default=source_default)
            else:
                record = normalize_record(raw, source_default=source_default)
        except ImportValidationError as error:
            report["errors"].append({"line": line_number, "error": str(error)})
            continue

        license_problem = _license_problem(session, record)
        if license_problem:
            report["skipped"] += 1
            report["skips"].append({"line": line_number, "name": record["name"], "reason": license_problem})
            continue

        names = [record["name"]]
        names.extend(item["name"] for item in record.get("_translations", []))
        normalized_names = {normalized_name(name) for name in names}
        name_key = normalized_name(record["name"])
        key = _record_key(record)
        if key in seen or name_key in seen_names:
            report["skipped"] += 1
            report["skips"].append({"line": line_number, "name": record["name"], "reason": "duplicate in input"})
            continue
        seen.add(key)
        seen_names.add(name_key)

        duplicate = find_duplicate(session, record, index=catalog_index)
        if duplicate:
            report["skipped"] += 1
            report["skips"].append({"line": line_number, "name": record["name"], "reason": duplicate})
            continue
        if any(normalized_name(t.name) in normalized_names
               for t in session.query(FoodTranslation).all()):
            report["skipped"] += 1
            report["skips"].append({"line": line_number, "name": record["name"], "reason": "duplicate translated name"})
            continue

        matches = possible_duplicates(catalog_index, record)
        if matches:
            report["possible_duplicates"].append({
                "line": line_number, "name": record["name"], "matches": matches,
            })

        barcodes = record.get("_barcodes", [])
        barcode_conflict = None
        for barcode in barcodes:
            if barcode in seen_barcodes:
                barcode_conflict = f"barcode repeated in input: {barcode}"
                break
            if session.query(FoodBarcode).filter(FoodBarcode.barcode == barcode).first():
                barcode_conflict = f"barcode already exists in curated catalog: {barcode}"
                break
            if session.query(CommunityFood).filter(CommunityFood.barcode == barcode).first():
                barcode_conflict = f"barcode already exists in community catalog: {barcode}"
                break
        if barcode_conflict:
            report["skipped"] += 1
            report["skips"].append({"line": line_number, "name": record["name"], "reason": barcode_conflict})
            continue
        seen_barcodes.update(barcodes)

        recipe = record.get("_recipe")
        if recipe:
            try:
                prepared = prepare_recipe(
                    session, recipe,
                    dataset_version=record.get("_dataset_version"),
                )
            except RecipeValidationError as error:
                report["errors"].append({
                    "line": line_number, "error": f"recipe: {error}",
                })
                continue
            record["kcal"] = prepared.nutrients["kcal"]
            record["protein"] = prepared.nutrients["protein"]
            record["carbs"] = prepared.nutrients["carbs"]
            record["fat"] = prepared.nutrients["fat"]
            record["approximate"] = prepared.approximate
            record["_nutrients"] = prepared.nutrients
            record["_recipe_ingredients"] = [
                {
                    "ingredient_food_id": line.ingredient_food_id,
                    "ingredient_name": line.ingredient_name,
                    "amount": line.grams,
                    "unit": "g",
                    "grams": line.grams,
                    "preparation_state": line.preparation_state,
                    "edible_portion": line.edible_portion,
                    "nutrient_snapshot": line.snapshot,
                    "position": position,
                }
                for position, line in enumerate(prepared.lines, start=1)
            ]
            record["_recipe_details"] = prepared.details
            report["recipes"] += 1
            report["recipe_ingredients"] += len(prepared.lines)

        report["valid"] += 1
        if commit:
            pending.append(record)

    if commit and pending:
        try:
            for record in pending:
                item = CatalogItem(**_flat_fields(record))
                session.add(item)
                session.flush()
                translations = record.get("_translations") or _synthetic_translations(record)
                for translation in translations:
                    session.add(FoodTranslation(food_id=item.id, **translation))
                for alias in record.get("_aliases", []):
                    session.add(FoodAlias(food_id=item.id, source_code=record["source"], **alias))
                for term in build_search_terms(
                    names=[record["name"], record.get("name_original") or ""],
                    translations=[
                        (row["language"], row["name"], row["review_status"])
                        for row in translations
                    ],
                    aliases=[
                        (alias["language"], alias["alias"])
                        for alias in record.get("_aliases", [])
                    ],
                ):
                    session.add(FoodSearchTerm(
                        food_id=item.id, language=term.language,
                        term_type=term.term_type, term=term.term[:200],
                        normalized=term.normalized[:200],
                    ))
                nutrients = record.get("_nutrients") or {
                    "reference_amount": 100, "reference_unit": "g",
                    "kcal": record["kcal"], "protein": record["protein"],
                    "carbs": record["carbs"], "fat": record["fat"],
                    "fiber": None, "sugar": None, "sodium": None, "saturated_fat": None,
                }
                session.add(NutrientProfile(
                    food_id=item.id, **nutrients,
                    source_code=record["source"], source_record_id=record.get("source_id"),
                    dataset_version=record.get("_dataset_version"),
                    is_primary=True, review_status="approved",
                ))
                servings = record.get("_servings") or [{
                    "amount": 100, "unit": "g", "grams": 100,
                    "description": "100 g", "is_default": True,
                }]
                for serving in servings:
                    session.add(FoodServing(food_id=item.id, source_code=record["source"], **serving))
                for barcode in record.get("_barcodes", []):
                    session.add(FoodBarcode(food_id=item.id, barcode=barcode, source_code=record["source"]))
                for line in record.get("_recipe_ingredients", []):
                    session.add(RecipeIngredient(recipe_food_id=item.id, **line))
                details = record.get("_recipe_details")
                if details:
                    session.add(RecipeDetails(food_id=item.id, **details))
            session.commit()
        except Exception:
            session.rollback()
            raise
        report["imported"] = len(pending)
        report["translations"] = sum(
            len(record.get("_translations") or _synthetic_translations(record))
            for record in pending
        )
        report["aliases"] = sum(len(record.get("_aliases", [])) for record in pending)
        report["nutrient_profiles"] = len(pending)
        report["servings"] = sum(
            len(record.get("_servings") or [1]) for record in pending
        )
        report["barcodes"] = sum(len(record.get("_barcodes", [])) for record in pending)

    return report


TRANSLATION_STATUSES = {"verified", "machine", "draft"}
TRANSLATION_REVIEW_STATUSES = {"pending", "approved", "rejected"}
TRANSLATION_SOURCES = {"manual", "machine", "usda", "imported"}
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,8}(?:[_-][A-Za-z0-9]{1,8})*$")


def normalize_language(value: Any) -> str:
    tag = str(value or "").strip().replace("_", "-").lower()
    if not _LANGUAGE_TAG.match(tag):
        raise ImportValidationError("language must be a tag such as ru, uk or en-US")
    return tag


def export_untranslated(
    session: Session, language: str, *, source: str | None = None,
) -> list[dict[str, Any]]:
    """List foods that have no translation in ``language``.

    The output is a review worksheet: the reviewer fills ``name`` and keeps the
    status fields.  Nothing is machine-translated automatically.
    """
    language = normalize_language(language)
    rows: list[dict[str, Any]] = []
    for item in session.query(CatalogItem).order_by(CatalogItem.source, CatalogItem.name):
        if source and item.source != source:
            continue
        languages = {translation.language.lower() for translation in item.translations}
        if language in languages:
            continue
        english = next(
            (translation.name for translation in item.translations
             if translation.language.lower() == "en"),
            None,
        )
        rows.append({
            "source": item.source,
            "source_id": item.source_id,
            "language": language,
            "name_original": item.name_original or item.name,
            "name_en": english,
            "category": item.category,
            "type": item.type,
            "name": "",
            "description": None,
            "translation_source": "manual",
            "translation_status": "verified",
            "review_status": "approved",
        })
    return rows


def import_translations(
    session: Session, records: Iterable[dict[str, Any]], *, commit: bool = False,
) -> dict[str, Any]:
    """Upsert reviewed translations and rebuild their search terms.

    A machine or draft translation may be stored, but it is not searchable until
    it is approved.  An already approved translation is never downgraded by a
    later machine record.
    """
    raw_records = list(records)
    report: dict[str, Any] = {
        "processed": len(raw_records),
        "valid": 0,
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "skips": [],
        "terms": 0,
        "dry_run": not commit,
    }
    try:
        for index, raw in enumerate(raw_records, start=1):
            try:
                if not isinstance(raw, dict):
                    raise ImportValidationError("translation record must be an object")
                source = str(raw.get("source") or "").strip()
                source_id = str(raw.get("source_id") or "").strip()
                if not source or not source_id:
                    raise ImportValidationError("source and source_id are required")
                language = normalize_language(raw.get("language"))
                name = str(raw.get("name") or "").strip()
                if not name or len(name) > 200:
                    raise ImportValidationError("translation name must contain 1-200 characters")
                status = str(raw.get("translation_status") or "verified").strip().lower()
                if status not in TRANSLATION_STATUSES:
                    raise ImportValidationError(
                        f"translation_status must be one of {sorted(TRANSLATION_STATUSES)}"
                    )
                review = str(
                    raw.get("review_status")
                    or ("approved" if status == "verified" else "pending")
                ).strip().lower()
                if review not in TRANSLATION_REVIEW_STATUSES:
                    raise ImportValidationError(
                        f"review_status must be one of {sorted(TRANSLATION_REVIEW_STATUSES)}"
                    )
                if status == "verified" and review != "approved":
                    raise ImportValidationError(
                        "verified translations require review_status=approved"
                    )
                translation_source = str(
                    raw.get("translation_source")
                    or ("machine" if status == "machine" else "manual")
                ).strip().lower()
                if translation_source not in TRANSLATION_SOURCES:
                    raise ImportValidationError(
                        f"translation_source must be one of {sorted(TRANSLATION_SOURCES)}"
                    )
                description = raw.get("description")
                if description is not None:
                    description = str(description).strip() or None
            except ImportValidationError as error:
                report["errors"].append({"line": index, "error": str(error)})
                continue

            item = (
                session.query(CatalogItem)
                .filter_by(source=source, source_id=source_id)
                .first()
            )
            if item is None:
                report["errors"].append({
                    "line": index,
                    "error": f"unknown catalog food: {source}:{source_id}",
                })
                continue

            existing = (
                session.query(FoodTranslation)
                .filter_by(food_id=item.id, language=language)
                .first()
            )
            if existing is not None:
                if existing.review_status == "approved" and status != "verified":
                    report["skipped"] += 1
                    report["skips"].append({
                        "line": index, "name": existing.name,
                        "reason": "approved translation is not downgraded",
                    })
                    continue
                if commit:
                    existing.name = name
                    existing.description = description
                    existing.translation_source = translation_source
                    existing.translation_status = status
                    existing.review_status = review
                    if raw.get("is_primary") is not None:
                        existing.is_primary = bool(raw["is_primary"])
                    session.flush()
                report["updated"] += 1
            else:
                if commit:
                    session.add(FoodTranslation(
                        food_id=item.id, language=language, name=name,
                        description=description,
                        translation_source=translation_source,
                        translation_status=status, review_status=review,
                        is_primary=bool(raw.get("is_primary", False)),
                    ))
                    session.flush()
                report["imported"] += 1

            report["valid"] += 1
            if commit:
                report["terms"] += rebuild_search_terms_for_food_id(session, item.id)

        if commit:
            session.commit()
    except Exception:
        session.rollback()
        raise
    return report


def reindex_search(session: Session, *, commit: bool = False) -> dict[str, Any]:
    """Rebuild every stored search term from the authoritative catalog rows."""
    items = session.query(CatalogItem).all()
    planned = sum(len(terms_for_food(item)) for item in items)
    report = {"foods": len(items), "terms": planned, "dry_run": not commit}
    if not commit:
        return report
    try:
        session.query(FoodSearchTerm).delete(synchronize_session=False)
        rebuilt = 0
        for item in items:
            rebuilt += rebuild_search_terms_for_food_id(session, item.id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    report["terms"] = rebuilt
    return report


def translate_fatsecret_name(name: str) -> dict[str, str | None]:
    key = normalized_name(name).split(" - ")[0].strip()
    translated = FATSECRET_TRANSLATIONS.get(key)
    if translated is None:
        return {"name_ru": None, "name_uk": None, "translation_status": "manual_required"}
    return {
        "name_ru": translated["ru"],
        "name_uk": translated["uk"],
        "translation_status": "translated",
    }


def _single(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class FatSecretClient:
    """Minimal server-side OAuth2 client. It is never imported by the app."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        scope: str = "basic",
        http: Any | None = None,
    ):
        if not client_id or not client_secret:
            raise ImportValidationError(
                "FATSECRET_CLIENT_ID and FATSECRET_CLIENT_SECRET must be set in the process environment"
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.http = http or httpx.Client(timeout=20)
        self._token: str | None = None

    def _access_token(self) -> str:
        if self._token:
            return self._token
        auth = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        response = self.http.post(
            FATSECRET_TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": self.scope},
            headers={"Authorization": f"Basic {auth}"},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = str(payload["access_token"])
        return self._token

    def search(self, query: str, *, page: int = 0, max_results: int = 20) -> list[dict[str, Any]]:
        response = self.http.get(
            FATSECRET_SEARCH_URL,
            params={
                "search_expression": query,
                "page_number": page,
                "max_results": min(max(max_results, 1), 50),
                "format": "json",
            },
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        response.raise_for_status()
        foods = response.json().get("foods", {}).get("food", [])
        return _single(foods)

    def get_food(self, food_id: str) -> dict[str, Any]:
        response = self.http.get(
            FATSECRET_FOOD_URL,
            params={"food_id": food_id, "format": "json"},
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        response.raise_for_status()
        return response.json().get("food", {})


def preview_from_food(food: dict[str, Any]) -> dict[str, Any] | None:
    servings = _single(food.get("servings", {}).get("serving", []))
    selected = None
    for serving in servings:
        try:
            amount = float(serving.get("metric_serving_amount") or 0)
        except (TypeError, ValueError):
            continue
        unit = str(serving.get("metric_serving_unit") or "").lower()
        if amount > 0 and unit in {"g", "ml"}:
            selected = (serving, amount, unit)
            if abs(amount - 100) < 0.01:
                break
    if selected is None:
        return None

    serving, amount, unit = selected
    factor = 100 / amount

    def value(field: str) -> float:
        try:
            return round(float(serving.get(field) or 0) * factor, 2)
        except (TypeError, ValueError):
            return 0.0

    name = str(food.get("food_name") or "").strip()
    brand = str(food.get("brand_name") or "").strip()
    display = f"{name} · {brand}" if brand else name
    translation = translate_fatsecret_name(name)
    return {
        "source": "fatsecret",
        "source_id": str(food.get("food_id") or ""),
        "name": display,
        "name_original": display,
        "name_ru": translation["name_ru"],
        "name_uk": translation["name_uk"],
        "translation_status": translation["translation_status"],
        "category": "FatSecret preview",
        "type": "ingredient",
        "kcal": value("calories"),
        "protein": value("protein"),
        "carbs": value("carbohydrate"),
        "fat": value("fat"),
        "serving_size": 100,
        "serving_unit": unit,
        "preparation_state": None,
        "approximate": False,
        "source_url": food.get("food_url"),
        "license_status": "preview_only",
    }


def fatsecret_preview(
    client: FatSecretClient,
    query: str,
    *,
    max_results: int = 10,
) -> dict[str, Any]:
    foods = client.search(query, max_results=max_results)
    records = []
    for food in foods:
        food_id = str(food.get("food_id") or "")
        if not food_id:
            continue
        try:
            detail = client.get_food(food_id)
        except (httpx.HTTPError, ValueError):
            continue
        record = preview_from_food(detail)
        if record is not None:
            records.append(record)
    now = datetime.now(timezone.utc)
    return {
        "source": "fatsecret",
        "generated_at": now.isoformat(),
        "delete_by": (now + FATSECRET_PREVIEW_TTL).isoformat(),
        "license_warning": (
            "FatSecret Terms of Use: only food_id/serving_id may be stored indefinitely. "
            "Names and nutrition values must be deleted within 24 hours unless a separate "
            "written custom-storage permission exists. This file is a temporary review artifact."
        ),
        "records": records,
    }


def _session() -> Session:
    return SessionLocal()


def _print_report(report: dict[str, Any]) -> None:
    print(
        f"processed={report.get('processed', report['valid'] + report['skipped'])} "
        f"valid={report['valid']} imported={report['imported']} "
        f"skipped={report['skipped']} errors={len(report['errors'])} "
        f"translations={report.get('translations', 0)} aliases={report.get('aliases', 0)} "
        f"profiles={report.get('nutrient_profiles', 0)} servings={report.get('servings', 0)} "
        f"recipes={report.get('recipes', 0)} recipe_ingredients={report.get('recipe_ingredients', 0)} "
        f"possible_duplicates={len(report.get('possible_duplicates', []))} "
        f"dry_run={report['dry_run']}"
    )
    for item in report["errors"][:20]:
        print(f"  error line {item['line']}: {item['error']}", file=sys.stderr)
    for item in report["skips"][:20]:
        print(f"  skip line {item['line']}: {item['reason']} ({item['name']})")
    for item in report.get("possible_duplicates", [])[:20]:
        match = item["matches"][0]
        print(
            f"  possible duplicate line {item['line']}: {item['name']!r} ~ "
            f"{match['name']!r} ({match['source']}, score {match['score']})"
        )


def _write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _looks_like_staging_json(path: str | Path) -> bool:
    """A staging file is a JSON array; official datasets are JSON objects."""
    target = Path(path)
    if target.suffix.lower() != ".json" or not target.is_file():
        return False
    with target.open("rb") as handle:
        head = handle.read(4096).lstrip(b"\xef\xbb\xbf \t\r\n")
    return head.startswith(b"[")


def _usda_stage(args) -> int:
    from tools.catalog_sources import usda

    if _looks_like_staging_json(args.input):
        print(
            "Input is already a staging JSON array. Use:\n"
            f"  python -m tools.catalog_import import --input {args.input} "
            "--dry-run|--commit",
            file=sys.stderr,
        )
        return 1

    try:
        records, stage_report = usda.stage_dataset(
            args.input,
            data_types=args.data_types,
            limit=args.limit,
            dataset_version=args.dataset_version,
            fdc_ids=args.fdc_ids,
        )
    except usda.UsdaAdapterError as error:
        print(f"USDA staging failed: {error}", file=sys.stderr)
        return 1

    if args.report:
        _write_json(args.report, stage_report)

    if args.usda_command == "stage":
        _write_json(args.out, records)
        print(
            f"staged={len(records)} processed={stage_report['processed']} "
            f"valid={stage_report['valid']} errors={stage_report['error_count']} "
            f"duplicate_ids={stage_report['duplicate_count']} "
            f"dataset_version={stage_report['dataset_version']} out={args.out}"
        )
        if stage_report["errors"]:
            for item in stage_report["errors"][:10]:
                print(
                    f"  rejected fdcId={item['fdc_id']}: {item['error']}",
                    file=sys.stderr,
                )
        return 0 if records else 1

    # ``usda import``: stage in memory, then reuse the reviewed import pipeline.
    staging_path = args.staging
    if staging_path:
        _write_json(staging_path, records)

    commit = args.commit and not args.dry_run
    try:
        with _session() as session:
            import_report = import_records(
                session, records, source_default="usda", commit=commit
            )
    except ImportValidationError as error:
        print(str(error), file=sys.stderr)
        return 1

    _print_report(import_report)
    if args.report:
        _write_json(args.report, {
            "stage": stage_report,
            "import": import_report,
            "staging": staging_path,
        })
    return 1 if import_report["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Curated catalog import tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="validate and import a reviewed JSON file")
    imp.add_argument("--input", required=True)
    imp.add_argument("--source-default", default="manual")
    imp.add_argument("--commit", action="store_true", help="write to PostgreSQL")
    imp.add_argument("--dry-run", action="store_true", help="validate only (default)")
    imp.add_argument("--report", help="write the full JSON report to this path")

    usda = sub.add_parser(
        "usda", help="USDA FoodData Central bulk import (offline, staged)"
    )
    usda_sub = usda.add_subparsers(dest="usda_command", required=True)

    def _usda_arguments(target):
        target.add_argument("--input", required=True, help=".zip, .json or CSV directory")
        target.add_argument("--dataset-version", default=None)
        target.add_argument(
            "--data-type", action="append", dest="data_types",
            choices=["Foundation", "SR Legacy", "Survey (FNDDS)"],
            help="repeatable; defaults to all supported types",
        )
        target.add_argument("--limit", type=int, default=None)
        target.add_argument(
            "--fdc-id", action="append", dest="fdc_ids", default=None,
            help="repeatable; stage only these FoodData Central ids",
        )
        target.add_argument("--report", default=None, help="write the JSON report here")

    stage = usda_sub.add_parser(
        "stage", help="normalize a dataset into staging JSON; no database access"
    )
    _usda_arguments(stage)
    stage.add_argument("--out", required=True, help="staging JSON path")

    usda_import = usda_sub.add_parser(
        "import", help="stage a dataset and run the reviewed import pipeline"
    )
    _usda_arguments(usda_import)
    usda_import.add_argument("--staging", default=None, help="also write staging JSON")
    usda_import.add_argument("--commit", action="store_true", help="write to PostgreSQL")
    usda_import.add_argument("--dry-run", action="store_true", help="validate only (default)")

    fs = sub.add_parser("fatsecret-search", help="temporary preview only; never writes to PostgreSQL")
    fs.add_argument("--query", required=True)
    fs.add_argument("--max-results", type=int, default=10)
    fs.add_argument("--out", required=True)
    fs.add_argument("--scope", default="basic")

    translations = sub.add_parser(
        "translations", help="export untranslated foods and import reviewed translations"
    )
    translations_sub = translations.add_subparsers(
        dest="translations_command", required=True
    )
    tr_export = translations_sub.add_parser(
        "export", help="write a review worksheet of foods missing one language"
    )
    tr_export.add_argument("--language", required=True)
    tr_export.add_argument("--out", required=True)
    tr_export.add_argument("--source", default=None)
    tr_import = translations_sub.add_parser(
        "import", help="upsert reviewed translations and rebuild search terms"
    )
    tr_import.add_argument("--input", required=True)
    tr_import.add_argument("--commit", action="store_true")
    tr_import.add_argument("--dry-run", action="store_true")
    tr_import.add_argument("--report", default=None)

    search = sub.add_parser("search", help="search index maintenance")
    search_sub = search.add_subparsers(dest="search_command", required=True)
    reindex = search_sub.add_parser(
        "reindex", help="rebuild food_search_terms from catalog rows"
    )
    reindex.add_argument("--commit", action="store_true")
    reindex.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "translations":
        if args.translations_command == "export":
            with _session() as session:
                rows = export_untranslated(
                    session, args.language, source=args.source
                )
            _write_json(args.out, rows)
            print(f"exported={len(rows)} language={args.language} out={args.out}")
            return 0

        records = load_records(args.input)
        try:
            with _session() as session:
                report = import_translations(
                    session, records, commit=args.commit and not args.dry_run
                )
        except ImportValidationError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(
            f"processed={report['processed']} valid={report['valid']} "
            f"imported={report['imported']} updated={report['updated']} "
            f"skipped={report['skipped']} errors={len(report['errors'])} "
            f"terms={report['terms']} dry_run={report['dry_run']}"
        )
        for item in report["errors"][:20]:
            print(f"  error line {item['line']}: {item['error']}", file=sys.stderr)
        for item in report["skips"][:20]:
            print(f"  skip line {item['line']}: {item['reason']} ({item['name']})")
        if args.report:
            _write_json(args.report, report)
        return 1 if report["errors"] else 0

    if args.command == "search":
        with _session() as session:
            report = reindex_search(
                session, commit=args.commit and not args.dry_run
            )
        print(
            f"foods={report['foods']} terms={report['terms']} "
            f"dry_run={report['dry_run']}"
        )
        return 0

    if args.command == "usda":
        return _usda_stage(args)

    if args.command == "fatsecret-search":
        try:
            client = FatSecretClient(
                os.environ.get("FATSECRET_CLIENT_ID", ""),
                os.environ.get("FATSECRET_CLIENT_SECRET", ""),
                scope=args.scope,
            )
            preview = fatsecret_preview(client, args.query, max_results=args.max_results)
        except (ImportValidationError, httpx.HTTPError, ValueError) as error:
            print(f"FatSecret preview failed: {error}", file=sys.stderr)
            return 1
        Path(args.out).write_text(json.dumps(preview, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"Wrote {len(preview['records'])} preview records to {args.out}. "
            f"Delete this file by {preview['delete_by']}."
        )
        return 0

    if args.command == "import":
        records = load_records(args.input)
        commit = args.commit and not args.dry_run
        try:
            with _session() as session:
                report = import_records(
                    session,
                    records,
                    source_default=args.source_default,
                    commit=commit,
                )
        except ImportValidationError as error:
            print(str(error), file=sys.stderr)
            return 1
        _print_report(report)
        if args.report:
            _write_json(args.report, report)
        return 1 if report["errors"] else 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
