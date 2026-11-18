"""USDA FoodData Central adapter (offline bulk import only).

The adapter turns an official FoodData Central bulk download into the reviewed
JSON shape consumed by :mod:`tools.catalog_import`.  It never calls the USDA
API and is never imported by the FastAPI runtime, so ordinary Nutrition usage
does not depend on an external service.

Supported official downloads
----------------------------
* **JSON** archives: Foundation Foods, SR Legacy and FNDDS (Survey).  Branded
  is rejected on purpose until a separate quality/licensing review.
* **CSV** archives that contain the classic ``food.csv`` + ``food_nutrient.csv``
  tables (the SR Legacy per-type CSV and the "Full Download of All Data Types"
  archive).

  The Foundation-only CSV download published since 2026 ships
  ``foundation_food.csv`` and ``sub_sample_result.csv`` instead of a per-food
  nutrient table, so that archive cannot be imported on its own.  Use the
  Foundation JSON download or the full CSV download.

Nutrient selection
------------------
FoodData Central stores several nutrient variants.  The documented priority
list is in :data:`NUTRIENT_RULES`; the first available nutrient ID wins and
variants are never summed or averaged.  Amounts are converted to one canonical
unit per field (kcal, g, g, g, g, g, mg, g) and must be finite, non-negative and
within a physical limit.  A missing nutrient stays ``None``; a measured zero
stays ``0``.

Nothing in this module writes to PostgreSQL.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import zipfile
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO

SOURCE_CODE = "usda"
SOURCE_NAME = "USDA FoodData Central"
SOURCE_URL_TEMPLATE = "https://fdc.nal.usda.gov/food-details/{fdc_id}/nutrients"

SUPPORTED_DATA_TYPES = ("Foundation", "SR Legacy", "Survey (FNDDS)")

# Top-level JSON keys used by the official downloads.
_JSON_DATA_TYPE_KEYS = {
    "FoundationFoods": "Foundation",
    "SRLegacyFoods": "SR Legacy",
    "SurveyFoods": "Survey (FNDDS)",
}
_JSON_BRANDED_KEY = "BrandedFoods"

# ``food.csv`` uses snake_case data types in the CSV downloads.
_CSV_DATA_TYPES = {
    "foundation_food": "Foundation",
    "sample_food": "Foundation",
    "sr_legacy_food": "SR Legacy",
    "survey_food": "Survey (FNDDS)",
    "branded_food": "Branded",
}


class UsdaAdapterError(ValueError):
    """The dataset, one food record or one nutrient value is not usable."""


@dataclass(frozen=True)
class NutrientRule:
    """One catalog field: nutrient IDs in priority order and its canonical unit."""

    field: str
    nutrient_ids: tuple[int, ...]
    target_unit: str
    maximum: float


# Energy: 1008 "Energy" (kcal) first, then Atwater specific (2048), Atwater
# general (2047) and finally the kJ duplicate (1062), converted.  Values from
# different energy calculations are never added together.
#
# Carbohydrate: 1005 "by difference" is the FDC default; 1050 "by summation" is
# only used when 1005 is absent.
#
# Total sugars: 1063 "Sugars, Total" is the current ID, 2000 "Total Sugars" the
# legacy ID for the same concept.
#
# Fiber: only 1079 "Fiber, total dietary".  AOAC 2011.25 (2033) is a different
# analytical method and is deliberately not substituted.
NUTRIENT_RULES: tuple[NutrientRule, ...] = (
    NutrientRule("kcal", (1008, 2048, 2047, 1062), "kcal", 950.0),
    NutrientRule("protein", (1003,), "g", 100.0),
    NutrientRule("carbs", (1005, 1050), "g", 100.0),
    NutrientRule("fat", (1004,), "g", 100.0),
    NutrientRule("fiber", (1079,), "g", 100.0),
    NutrientRule("sugar", (1063, 2000), "g", 100.0),
    NutrientRule("sodium", (1093,), "mg", 40_000.0),
    NutrientRule("saturated_fat", (1258,), "g", 100.0),
)
REQUIRED_FIELDS = ("kcal", "protein", "carbs", "fat")
OPTIONAL_FIELDS = ("fiber", "sugar", "sodium", "saturated_fat")

_KNOWN_NUTRIENT_IDS = {nid for rule in NUTRIENT_RULES for nid in rule.nutrient_ids}

# FDC food categories mapped to the existing catalog categories.  Unmapped
# categories fall back to "Другое"; the original USDA category is kept in the
# staging report for review.
CATEGORY_MAP = {
    "Dairy and Egg Products": "Молочное",
    "Spices and Herbs": "Специи",
    "Fats and Oils": "Масла",
    "Poultry Products": "Мясо и рыба",
    "Soups, Sauces, and Gravies": "Соусы",
    "Sausages and Luncheon Meats": "Мясо и рыба",
    "Breakfast Cereals": "Крупы и хлеб",
    "Fruits and Fruit Juices": "Фрукты",
    "Pork Products": "Мясо и рыба",
    "Vegetables and Vegetable Products": "Овощи",
    "Nut and Seed Products": "Орехи и семена",
    "Beef Products": "Мясо и рыба",
    "Beverages": "Напитки",
    "Finfish and Shellfish Products": "Мясо и рыба",
    "Legumes and Legume Products": "Бобовые",
    "Lamb, Veal, and Game Products": "Мясо и рыба",
    "Baked Products": "Хлеб и выпечка",
    "Sweets": "Сладости",
    "Cereal Grains and Pasta": "Крупы и хлеб",
    "Fast Foods": "Готовые блюда",
    "Mixed Dishes": "Готовые блюда",
    "Restaurant Foods": "Готовые блюда",
    "Prepared Meals": "Готовые блюда",
    "Snacks": "Снеки",
    "Alcoholic Beverages": "Напитки",
}

# Only these SR Legacy categories are treated as prepared meals.  Foundation
# foods are analytical ingredient samples, so they stay ingredients.
_PREPARED_CATEGORIES = {
    "Baked Products",
    "Fast Foods",
    "Mixed Dishes",
    "Restaurant Foods",
    "Prepared Meals",
    "Sweets",
}

_PREPARATION_TOKENS = (
    ("raw", "raw"),
    ("uncooked", "raw"),
    ("cooked", "cooked"),
    ("boiled", "boiled"),
    ("roasted", "roasted"),
    ("baked", "baked"),
    ("fried", "fried"),
    ("grilled", "grilled"),
    ("steamed", "steamed"),
    ("canned", "canned"),
    ("frozen", "frozen"),
    ("dried", "dried"),
    ("dehydrated", "dried"),
)

_UNIT_ALIASES = {
    "kcal": "kcal",
    "kj": "kJ",
    "g": "g",
    "mg": "mg",
    "µg": "µg",
    "μg": "µg",
    "ug": "µg",
}


def normalize_data_type(value: Any) -> str:
    """Return the canonical data-type label or an empty string."""
    text = str(value or "").strip()
    if text in SUPPORTED_DATA_TYPES or text == "Branded":
        return text
    return _CSV_DATA_TYPES.get(text.lower(), text)


def collapse_whitespace(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def normalize_food_name(value: Any) -> str:
    """Clean a USDA description without translating or reordering it."""
    text = collapse_whitespace(value)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return text[:200]


def detect_preparation_state(description: str) -> str | None:
    text = f" {description.lower()} "
    for token, state in _PREPARATION_TOKENS:
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", text):
            return state
    return None


def classify_type(data_type: str, usda_category: str) -> str:
    if data_type == "SR Legacy" and usda_category in _PREPARED_CATEGORIES:
        return "prepared_meal"
    return "ingredient"


def convert_amount(value: float, unit: str | None, target_unit: str) -> float:
    """Convert one nutrient amount to the canonical unit for its field."""
    normalized = _UNIT_ALIASES.get(str(unit or "").strip().lower())
    if normalized is None:
        raise UsdaAdapterError(f"unsupported unit {unit!r} for {target_unit}")
    if target_unit == "kcal":
        if normalized == "kcal":
            return value
        if normalized == "kJ":
            return value / 4.184
    elif target_unit == "g":
        if normalized == "g":
            return value
        if normalized == "mg":
            return value / 1_000
        if normalized == "µg":
            return value / 1_000_000
    elif target_unit == "mg":
        if normalized == "mg":
            return value
        if normalized == "g":
            return value * 1_000
        if normalized == "µg":
            return value / 1_000
    raise UsdaAdapterError(f"cannot convert {unit!r} to {target_unit}")


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_nutrient_entries(
    food_nutrients: Iterable[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Keep one entry per nutrient ID: the one with the most data points."""
    best: dict[int, dict[str, Any]] = {}
    points_by_id: dict[int, int] = {}
    for entry in food_nutrients or []:
        if not isinstance(entry, dict):
            continue
        nutrient = entry.get("nutrient") or {}
        nutrient_id = nutrient.get("id")
        if not isinstance(nutrient_id, int):
            continue
        if entry.get("amount") is None:
            continue
        try:
            points = int(entry.get("dataPoints") or 0)
        except (TypeError, ValueError):
            points = 0
        if nutrient_id not in best or points > points_by_id[nutrient_id]:
            best[nutrient_id] = entry
            points_by_id[nutrient_id] = points
    return best


def extract_nutrients(
    food_nutrients: Iterable[dict[str, Any]],
    unknown_ids: Counter | None = None,
) -> dict[str, float | None]:
    """Map FDC nutrients to catalog fields using :data:`NUTRIENT_RULES`."""
    entries = _best_nutrient_entries(food_nutrients)
    known = entries.keys() & _KNOWN_NUTRIENT_IDS
    if unknown_ids is not None:
        for nutrient_id in entries.keys() - known:
            unknown_ids[nutrient_id] += 1

    values: dict[str, float | None] = {}
    for rule in NUTRIENT_RULES:
        value: float | None = None
        for nutrient_id in rule.nutrient_ids:
            entry = entries.get(nutrient_id)
            if entry is None:
                continue
            try:
                amount = float(entry["amount"])
            except (TypeError, ValueError):
                raise UsdaAdapterError(
                    f"nutrient {nutrient_id} amount is not a number"
                ) from None
            if not math.isfinite(amount):
                raise UsdaAdapterError(f"nutrient {nutrient_id} amount is not finite")
            if amount < 0:
                raise UsdaAdapterError(f"nutrient {nutrient_id} amount is negative")
            unit = (entry.get("nutrient") or {}).get("unitName")
            converted = convert_amount(amount, unit, rule.target_unit)
            if converted > rule.maximum:
                raise UsdaAdapterError(
                    f"{rule.field} {converted:g} {rule.target_unit} per 100 g exceeds "
                    f"the limit {rule.maximum:g}"
                )
            value = round(converted, 4)
            break
        values[rule.field] = value

    missing = [field for field in REQUIRED_FIELDS if values[field] is None]
    if missing:
        raise UsdaAdapterError("missing required nutrient(s): " + ", ".join(missing))
    return values


def extract_servings(portions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert FDC portions into catalog servings (max five, first is default).

    FNDDS adds "Guideline amount per ..." rows that are nutrient guidance, not
    portions, and "Quantity not specified" rows with a default gram weight.
    Those are handled explicitly so the default serving stays meaningful.
    """
    servings: list[dict[str, Any]] = []
    for portion in portions or []:
        if not isinstance(portion, dict):
            continue
        grams = _float_or_none(portion.get("gramWeight"))
        if grams is None or grams <= 0 or grams > 100_000:
            continue
        amount = (
            _float_or_none(portion.get("amount"))
            or _float_or_none(portion.get("value"))
            or 1.0
        )
        if amount <= 0:
            continue
        portion_text = collapse_whitespace(portion.get("portionDescription"))
        modifier_text = collapse_whitespace(portion.get("modifier"))
        # FNDDS stores a numeric modifier code and the human text in
        # portionDescription; Foundation often does the opposite.
        description_text = portion_text or (
            modifier_text if modifier_text and not modifier_text.isdigit() else ""
        )
        lowered = description_text.lower()
        if lowered.startswith("guideline amount"):
            continue
        unspecified = lowered.startswith("quantity not specified")

        measure = portion.get("measureUnit") or {}
        unit = collapse_whitespace(measure.get("name") or measure.get("abbreviation"))
        if unit.lower() in {"", "undetermined", "none"}:
            if unspecified:
                unit = "portion"
            elif description_text:
                unit = description_text.split()[-1]
            else:
                unit = "portion"
        unit = (unit or "portion")[:16]
        description = collapse_whitespace(
            description_text or f"{amount:g} {unit}"
        )[:120]
        servings.append({
            "amount": round(amount, 4),
            "unit": unit,
            "grams": round(grams, 4),
            "description": description or None,
            "is_default": False,
            "_rank": _serving_rank(unit, description_text),
        })
        if len(servings) == 5:
            break

    if not servings:
        return [{
            "amount": 100.0, "unit": "g", "grams": 100.0,
            "description": "100 g", "is_default": True,
        }]
    # Household numbered measures first, then descriptive ones, then RACC and
    # "quantity not specified" (rank 2/3).
    servings.sort(key=lambda serving: serving["_rank"])
    for serving in servings:
        serving.pop("_rank", None)
    servings[0]["is_default"] = True
    return servings


def _serving_rank(unit: str, description: str) -> int:
    text = (description or "").lower()
    if unit.lower() == "racc":
        return 3
    if text.startswith("quantity not specified"):
        return 2
    if re.match(r"^\d", text):
        return 0
    return 1


def build_record(
    food: dict[str, Any],
    *,
    dataset_version: str | None,
    unknown_ids: Counter | None = None,
) -> dict[str, Any]:
    """Turn one FDC food object into a staging record for ``catalog_import``."""
    if not isinstance(food, dict):
        raise UsdaAdapterError("food entry is not an object")
    try:
        fdc_id = int(food.get("fdcId"))
    except (TypeError, ValueError):
        raise UsdaAdapterError("fdcId is missing or not an integer") from None
    if fdc_id <= 0:
        raise UsdaAdapterError("fdcId must be positive")

    data_type = normalize_data_type(food.get("dataType"))
    if data_type == "Branded":
        raise UsdaAdapterError(
            "Branded Foods bulk import is not enabled; its quality and licensing "
            "review is a separate decision"
        )
    if data_type not in SUPPORTED_DATA_TYPES:
        raise UsdaAdapterError(f"unsupported data type: {food.get('dataType')!r}")

    name_original = collapse_whitespace(food.get("description"))[:200]
    if not name_original:
        raise UsdaAdapterError("description is empty")

    nutrients = extract_nutrients(food.get("foodNutrients") or [], unknown_ids)
    usda_category = collapse_whitespace(
        (food.get("foodCategory") or {}).get("description")
    ) or collapse_whitespace(
        (food.get("wweiaFoodCategory") or {}).get("wweiaFoodCategoryDescription")
    )
    if data_type == "Survey (FNDDS)":
        # Survey foods are as-consumed dishes, not raw ingredients.
        category = "Готовые блюда"
        record_type = "prepared_meal"
    else:
        category = CATEGORY_MAP.get(usda_category, "Другое")
        record_type = classify_type(data_type, usda_category)

    return {
        "source": SOURCE_CODE,
        "source_id": str(fdc_id),
        "type": record_type,
        "category": category,
        "original_language": "en",
        "name_original": name_original,
        "translations": [{
            "language": "en",
            "name": normalize_food_name(name_original),
            "translation_source": "usda",
            "translation_status": "verified",
            "review_status": "approved",
            "is_primary": True,
        }],
        "aliases": [],
        "nutrients": {
            "reference_amount": 100.0,
            "reference_unit": "g",
            **nutrients,
        },
        "servings": extract_servings(food.get("foodPortions") or []),
        "preparation_state": detect_preparation_state(name_original),
        "approximate": False,
        "source_url": SOURCE_URL_TEMPLATE.format(fdc_id=fdc_id),
        "dataset_version": dataset_version,
        "usda": {"data_type": data_type, "category": usda_category or None},
    }


# ---------------------------------------------------------------------------
# Dataset readers
# ---------------------------------------------------------------------------


def iter_json_array(handle: TextIO, *, chunk_size: int = 1 << 20) -> Iterator[Any]:
    """Yield items of the first JSON array in ``handle`` without loading it all.

    Used for the 200 MB SR Legacy JSON download.  ``raw_decode`` is retried with
    more data whenever an object straddles a chunk boundary.
    """
    decoder = json.JSONDecoder()
    buffer = handle.read(chunk_size)
    if not buffer:
        raise UsdaAdapterError("dataset is empty")
    while "[" not in buffer:
        chunk = handle.read(chunk_size)
        if not chunk:
            raise UsdaAdapterError("dataset has no JSON array")
        buffer += chunk
    index = buffer.index("[") + 1

    while True:
        while index < len(buffer) and buffer[index] in " \r\n\t,":
            index += 1
        if index >= len(buffer):
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            buffer = buffer[index:] + chunk
            index = 0
            continue
        if buffer[index] == "]":
            return
        try:
            item, end = decoder.raw_decode(buffer, index)
        except json.JSONDecodeError:
            chunk = handle.read(chunk_size)
            if not chunk:
                raise UsdaAdapterError("dataset JSON is truncated") from None
            buffer = buffer[index:] + chunk
            index = 0
            continue
        index = end
        yield item


class UsdaDataset:
    """Read an official USDA download from a ``.zip``, ``.json`` or directory."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise UsdaAdapterError(f"dataset path does not exist: {self.path}")
        self._zip: zipfile.ZipFile | None = None

    def __enter__(self) -> "UsdaDataset":
        if self.path.is_file() and zipfile.is_zipfile(self.path):
            self._zip = zipfile.ZipFile(self.path)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    # -- member helpers ----------------------------------------------------
    def _find_member(self, name: str) -> str | None:
        if self._zip is not None:
            for member in self._zip.namelist():
                if member.split("/")[-1] == name:
                    return member
            return None
        if self.path.is_dir():
            candidate = self.path / name
            return str(candidate) if candidate.is_file() else None
        return None

    def _find_json_member(self) -> str | None:
        if self._zip is not None:
            for member in self._zip.namelist():
                if member.lower().endswith(".json"):
                    return member
            return None
        if self.path.is_dir():
            for candidate in sorted(self.path.glob("*.json")):
                return str(candidate)
            return None
        if self.path.suffix.lower() == ".json":
            return str(self.path)
        return None

    def _open_member(self, member: str) -> TextIO:
        if self._zip is not None:
            raw = self._zip.open(member)
            return io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        return open(member, encoding="utf-8-sig", newline="")

    def _csv_rows(self, name: str) -> Iterator[dict[str, str]]:
        member = self._find_member(name)
        if member is None:
            raise UsdaAdapterError(
                f"CSV archive is missing {name}; use a full FoodData Central CSV "
                "download or the JSON download for this data type"
            )
        with self._open_member(member) as handle:
            yield from csv.DictReader(handle)

    def _has_csv_member(self, name: str) -> bool:
        return self._find_member(name) is not None

    # -- iterators ---------------------------------------------------------
    def iter_foods(self) -> Iterator[dict[str, Any]]:
        json_member = self._find_json_member()
        if json_member is not None:
            yield from self._iter_json(json_member)
            return
        yield from self._iter_csv()

    def _iter_json(self, member: str) -> Iterator[dict[str, Any]]:
        with self._open_member(member) as handle:
            prefix = handle.read(8192)
            handle.seek(0)
            key = next(
                (k for k in (*_JSON_DATA_TYPE_KEYS, _JSON_BRANDED_KEY)
                 if f'"{k}"' in prefix),
                None,
            )
            if key is None:
                raise UsdaAdapterError(
                    "unrecognised FoodData Central JSON dataset; expected "
                    "FoundationFoods, SRLegacyFoods or SurveyFoods"
                )
            if key == _JSON_BRANDED_KEY:
                raise UsdaAdapterError(
                    "Branded Foods bulk import is not enabled; its quality and "
                    "licensing review is a separate decision"
                )
            for food in iter_json_array(handle):
                if isinstance(food, dict):
                    yield food

    def _iter_csv(self) -> Iterator[dict[str, Any]]:
        if not self._has_csv_member("food.csv"):
            raise UsdaAdapterError(
                "CSV archive has no food.csv/food_nutrient.csv table; the "
                "data-type-specific Foundation CSV download cannot be imported "
                "on its own. Use the Foundation JSON download or the full CSV."
            )

        nutrient_meta: dict[int, dict[str, str]] = {}
        for row in self._csv_rows("nutrient.csv"):
            try:
                nutrient_meta[int(row["id"])] = row
            except (KeyError, TypeError, ValueError):
                continue

        categories: dict[int, str] = {}
        if self._has_csv_member("food_category.csv"):
            for row in self._csv_rows("food_category.csv"):
                try:
                    categories[int(row["id"])] = collapse_whitespace(
                        row.get("description")
                    )
                except (KeyError, TypeError, ValueError):
                    continue

        units: dict[int, str] = {}
        if self._has_csv_member("measure_unit.csv"):
            for row in self._csv_rows("measure_unit.csv"):
                try:
                    units[int(row["id"])] = collapse_whitespace(row.get("name"))
                except (KeyError, TypeError, ValueError):
                    continue

        nutrients: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in self._csv_rows("food_nutrient.csv"):
            try:
                fdc_id = int(row["fdc_id"])
                nutrient_id = int(row["nutrient_id"])
            except (KeyError, TypeError, ValueError):
                continue
            meta = nutrient_meta.get(nutrient_id, {})
            nutrients[fdc_id].append({
                "amount": row.get("amount"),
                "dataPoints": row.get("data_points"),
                "nutrient": {
                    "id": nutrient_id,
                    "name": meta.get("name"),
                    "unitName": meta.get("unit_name"),
                },
            })

        portions: dict[int, list[dict[str, Any]]] = defaultdict(list)
        if self._has_csv_member("food_portion.csv"):
            for row in self._csv_rows("food_portion.csv"):
                try:
                    fdc_id = int(row["fdc_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                try:
                    measure_id = int(row.get("measure_unit_id") or 0)
                except (TypeError, ValueError):
                    measure_id = 0
                portions[fdc_id].append({
                    "amount": row.get("amount"),
                    "gramWeight": row.get("gram_weight"),
                    "modifier": row.get("modifier") or row.get("portion_description"),
                    "measureUnit": {
                        "name": units.get(measure_id) or "portion",
                        "abbreviation": units.get(measure_id) or "portion",
                    },
                })

        for row in self._csv_rows("food.csv"):
            raw_type = (row.get("data_type") or "").strip().lower()
            data_type = _CSV_DATA_TYPES.get(raw_type)
            if data_type is None:
                continue
            try:
                fdc_id = int(row["fdc_id"])
            except (KeyError, TypeError, ValueError):
                continue
            category_id = None
            try:
                category_id = int(row.get("food_category_id") or 0) or None
            except (TypeError, ValueError):
                category_id = None
            yield {
                "fdcId": fdc_id,
                "dataType": data_type,
                "description": row.get("description"),
                "foodCategory": {
                    "description": categories.get(category_id) if category_id else None,
                },
                "foodNutrients": nutrients.get(fdc_id, []),
                "foodPortions": portions.get(fdc_id, []),
            }


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def detect_dataset_version(path: str | Path) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", Path(path).name)
    return match.group(1) if match else None


def select_balanced(
    records: list[dict[str, Any]], limit: int | None
) -> list[dict[str, Any]]:
    """Pick up to ``limit`` records round-robin across categories.

    Deterministic and dependency-free: the first food of every category is
    taken before the second food of any category, so a small pilot still covers
    the full category spread instead of one alphabetical block.
    """
    if limit is None or limit >= len(records):
        return records
    if limit <= 0:
        return []
    buckets: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for record in records:
        buckets.setdefault(record["category"], []).append(record)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and buckets:
        for key in list(buckets):
            selected.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
            if len(selected) == limit:
                break
    return selected


def stage_dataset(
    path: str | Path,
    *,
    data_types: Iterable[str] | None = None,
    limit: int | None = None,
    dataset_version: str | None = None,
    fdc_ids: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a dataset and return staging records plus a review report.

    ``fdc_ids`` restricts staging to an explicit ingredient selection, which
    keeps targeted recipe dependencies separate from a bulk catalog import.
    No database connection is opened and nothing is written to disk.
    """
    allowed = tuple(data_types) if data_types else SUPPORTED_DATA_TYPES
    unsupported = [value for value in allowed if value not in SUPPORTED_DATA_TYPES]
    if unsupported:
        raise UsdaAdapterError(
            f"unsupported data type(s): {', '.join(unsupported)}; "
            f"choose from {', '.join(SUPPORTED_DATA_TYPES)}"
        )

    requested = {str(value).strip() for value in (fdc_ids or []) if str(value).strip()}
    seen_requested: set[str] = set()
    skipped_not_requested = 0

    version = dataset_version or detect_dataset_version(path)
    unknown_ids: Counter = Counter()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    processed = 0

    with UsdaDataset(path) as dataset:
        for food in dataset.iter_foods():
            processed += 1
            if requested:
                food_id = str(food.get("fdcId"))
                if food_id not in requested:
                    skipped_not_requested += 1
                    continue
                seen_requested.add(food_id)
            data_type = normalize_data_type(food.get("dataType"))
            if data_type not in allowed:
                continue
            try:
                record = build_record(
                    food, dataset_version=version, unknown_ids=unknown_ids
                )
            except UsdaAdapterError as error:
                errors.append({
                    "fdc_id": food.get("fdcId"),
                    "description": collapse_whitespace(food.get("description"))[:160],
                    "error": str(error),
                })
                continue
            if record["source_id"] in seen_ids:
                duplicates.append({
                    "fdc_id": record["source_id"],
                    "description": record["name_original"],
                    "reason": "duplicate fdcId in dataset",
                })
                continue
            seen_ids.add(record["source_id"])
            records.append(record)

    selected = select_balanced(records, limit)
    report = {
        "source": SOURCE_CODE,
        "source_name": SOURCE_NAME,
        "dataset": Path(path).name,
        "dataset_version": version,
        "data_types": list(allowed),
        "processed": processed,
        "requested_ids": len(requested),
        "missing_requested_ids": sorted(requested - seen_requested),
        "skipped_not_requested": skipped_not_requested,
        "valid": len(records),
        "selected": len(selected),
        "valid_not_selected": len(records) - len(selected),
        "error_count": len(errors),
        "errors": errors[:200],
        "duplicate_count": len(duplicates),
        "duplicates": duplicates[:200],
        "unknown_nutrients": {
            str(nutrient_id): count
            for nutrient_id, count in sorted(unknown_ids.items())
        },
        "categories": dict(Counter(record["category"] for record in selected).most_common()),
        "types": dict(Counter(record["type"] for record in selected).most_common()),
        "preparation_states": dict(
            Counter(
                record["preparation_state"]
                for record in selected
                if record["preparation_state"]
            ).most_common()
        ),
        "missing_optional": {
            field: sum(
                1 for record in selected
                if record["nutrients"].get(field) is None
            )
            for field in OPTIONAL_FIELDS
        },
        "serving_counts": {
            str(count): sum(
                1 for record in selected if len(record["servings"]) == count
            )
            for count in sorted({len(record["servings"]) for record in selected})
        },
    }
    return selected, report
