# Global Food Catalog

This document describes the PostgreSQL-backed catalog architecture used by Nutrition. The catalog is the runtime source of truth; external providers are acquisition sources only.

## Canonical records

`catalog_foods` is the canonical `FoodItem` table. It stores stable identity and a fast denormalized display/nutrient fallback:

- `source`, `source_id`, `source_url`;
- `type`: `ingredient`, `prepared_meal`, `branded_product`;
- `category`;
- original and localized display names;
- per-100-g fallback calories/protein/carbs/fat;
- serving metadata, preparation state, approximate flag;
- review/active state and timestamps.

Adding a language does **not** add a column. It inserts a row in `food_translations`.

## Child tables

| Table | Purpose |
|---|---|
| `food_translations` | One row per food + language. Stores localized name, description, translation source, translation status, review status and primary flag. |
| `food_aliases` | Multilingual search terms, transliterations, common names and brand aliases. |
| `nutrient_profiles` | Source-specific nutrient profiles with an explicit reference amount/unit. Optional nutrients are nullable; zero stays zero. |
| `food_servings` | Serving amount/unit, optional gram conversion, description and default flag. |
| `food_barcodes` | Barcode/package variants for canonical branded products. |
| `recipe_ingredients` | Recipe-to-ingredient relationships with amount, unit, grams and position. |
| `food_sources` | Source/license registry used by import validation and displayed provenance. |

`community_foods` remains the user-contributed barcode catalog. It is shared, but API responses never expose `submitted_by`.

## Multilingual model

- Languages are extensible strings, not a database constraint.
- Initial content uses `en`, `ru` and `uk`; the API accepts any valid language tag of 2–16 characters.
- Requested tags are normalized: lowercased, `_` becomes `-`. `ru-RU` is matched exactly first, then by its primary subtag `ru`; `zh-Hans` stays valid. Stored translation codes are matched case-insensitively, so a legacy `UK` row still resolves for `lang=uk`.
- A malformed tag such as `!!` is rejected with `422`.
- API fallback order: requested language → primary subtag → `en` → `ru` → `uk` → primary translation → canonical name.
- Original source names stay in `name_original` and in the original-language translation.
- Machine translations must use `translation_status='machine'` and `review_status='pending'`.
- Translation never changes nutrient values, the canonical `id`, or `source`/`source_id`.

## Nutrient policy

- Profiles are stored per explicit `reference_amount` + `reference_unit` (`g` or `ml`).
- Core values are kcal, protein, carbs and fat.
- Optional fields (fiber, sugar, sodium, saturated fat) are nullable to distinguish missing data from zero.
- Conflicting profiles are separate rows with different `source_code`; they are never silently averaged.
- One profile may be `is_primary`; the API exposes it and keeps provenance fields for audit.
- Approximate composite values must keep `approximate=true`.

## Source and license registry

| Source | Types | Acquisition | License | Attribution | Storage | Translation | Redistribution | Status |
|---|---|---|---|---|---|---|---|---|
| `builtin` | ingredient, prepared meal | internal seed | project-generated content; USDA-derived values are CC0 1.0 | no | yes | yes | yes | approved |
| `manual` | ingredient, prepared meal | documented recipes | project recipe documentation | no | yes | yes | yes | approved |
| `usda` | ingredient, prepared meal, branded | API/bulk | CC0 1.0 | no | yes | yes | yes | approved |
| `openfoodfacts` | branded | API/dump | ODbL 1.0 / DbCL 1.0 | yes | no (bulk pending) | yes | no (bulk pending) | pending |
| `fatsecret` | ingredient, prepared, branded | API | FatSecret Platform API Terms | yes | no | no | no | blocked |
| `fao_infoods` | ingredient, prepared | bulk | varies by dataset | yes | no | no | no | pending |
| `eurofir` | ingredient, prepared | membership | membership/national licence | yes | no | no | no | pending |

`builtin` is a mixed-origin label: records may be project-authored or derived from CC0 USDA values. It never means that restricted third-party content was relabelled. `manual` is always distinguishable from `usda` by both `catalog_foods.source` and `nutrient_profiles.source_code`.

Open Food Facts remains a runtime barcode lookup provider. Its registry row permits runtime lookup but not permanent bulk storage while the ODbL/ShareAlike review is open. The conditions to close that review are:

- attribution to Open Food Facts as the database source wherever its data is displayed;
- ShareAlike for any derived database that we redistribute, which would need a compatible licence decision for our own catalog;
- DbCL 1.0 terms for the individual database contents;
- separate treatment of product images, whose licences are not the ODbL database licence;
- a documented takedown/correction path for changed or removed products.

FatSecret is preview-only: its terms allow indefinite storage only for identifiers, not names or nutrient payloads.

## Barcode persistence

`Scan → external lookup → preview → user confirmation → POST /nutrition/catalog → community_foods → diary entry`

- A lookup result is not a saved product.
- The API response from `/nutrition/catalog/{barcode}` is a preview.
- `POST /nutrition/catalog` persists the confirmed product.
- `/nutrition/catalog-items` reloads curated and community products.
- Repeated scans update the same user row; another user's barcode is `409`.
- On `409` the Mini App reloads the shared catalog and adds the existing shared product, so the user gets the canonical values instead of a silent duplicate preview. If that reload fails, the user sees an explicit message and no diary entry is created.
- Diary entries are immutable nutritional snapshots, scoped to the user.
- External provider failure never deletes existing community or curated products.

## Import workflow

```text
Source adapter → staging JSON/CSV → normalization → license validation
→ deduplication → translation validation → nutrient validation
→ review → dry-run report → approved commit → PostgreSQL
```

`backend/tools/catalog_import.py` is local-only and never imported by the API runtime.

```bash
# validate only
python -m tools.catalog_import import --input data/catalog_pilot.json --dry-run

# write approved records
python -m tools.catalog_import import --input data/catalog_pilot.json --commit

# temporary FatSecret preview; delete within 24 hours
python -m tools.catalog_import fatsecret-search --query "borscht" --out /tmp/preview.json
```

Import guarantees:

- deterministic `source + source_id` identity;
- idempotent re-runs;
- duplicate detection by source ID, canonical name, translated name and barcode;
- similar but non-identical names against the existing `builtin`/`manual`/`usda` catalog are reported as possible duplicates for manual review, never merged;
- refusal of unknown, pending or blocked sources;
- permission checks read the `food_sources` fields (`storage_permission`, `translation_permission`, `approval_status`), not a hardcoded provider list, so a newly approved provider needs a registry row and an adapter, not an importer edit;
- records that carry names in a language other than the original are skipped when `translation_permission` is false;
- a barcode repeated inside one batch is skipped, not committed as a partial batch;
- per-record validation errors and skip reasons;
- no overwrite of existing community foods or diary entries;
- no provider secrets in output.

## USDA FoodData Central importer

`backend/tools/catalog_sources/usda.py` converts official FoodData Central bulk downloads into the reviewed JSON shape. It is offline-only, never imported by the API, and never calls the USDA API, so Nutrition has no runtime USDA dependency.

### Datasets

| Dataset | Release used | Format | Pilot | Notes |
|---|---|---|---|---|
| Foundation Foods | October 2026 (`2026-10-29`) | JSON | yes | Primary ingredient source; analytical samples. |
| SR Legacy | April 2018 | JSON or CSV | supported, not piloted | Final SR release; classic products. |
| FNDDS (Survey) | October 2024 | JSON or CSV | structure verified, not imported | Prepared/survey foods; larger download. |
| Branded Foods | — | — | rejected | Requires a separate quality and licensing review; the adapter refuses it. |

Supported inputs:

- the official JSON archives for Foundation, SR Legacy and FNDDS (streamed, so the 200 MB SR Legacy file is not loaded into memory at once);
- CSV archives that contain `food.csv` + `food_nutrient.csv` (SR Legacy per-type CSV and the "Full Download of All Data Types" archive);
- an unpacked directory or a `.zip`.

The data-type-specific Foundation CSV download published since 2026 contains only `foundation_food.csv` and `sub_sample_result.csv` (per-sample lab results keyed by food-nutrient id, not by food). It has no per-food nutrient table, so the adapter rejects it with an actionable message and points to the Foundation JSON or the full CSV.

### Commands

```bash
# normalize only; no database access
python -m tools.catalog_import usda stage \
  --input FoodData_Central_foundation_food_json_2026-10-29.zip \
  --dataset-version 2026-10-29 --limit 150 \
  --out staging/usda_pilot.json --report staging/usda_report.json

# dry-run the reviewed pipeline
python -m tools.catalog_import import --input staging/usda_pilot.json --dry-run

# commit and keep the full report
python -m tools.catalog_import import --input staging/usda_pilot.json --commit \
  --report staging/usda_commit.json

# stage and import in one step
python -m tools.catalog_import usda import --input <dataset> --limit 150 --commit
```

`--limit` selects records round-robin across categories, so a small pilot keeps the full category spread instead of one alphabetical block. `--report` writes every rejected record, skip reason and unknown nutrient ID.

### Nutrient mapping

One canonical unit per field; amounts are converted, never guessed.

| Catalog field | USDA IDs (priority order) | USDA unit | Stored unit | Conversion | Limit / 100 g | Rule |
|---|---|---|---|---|---|---|
| `kcal` | 1008 → 2048 → 2047 → 1062 | kcal or kJ | kcal | kJ ÷ 4.184 | ≤ 950 | 1008 "Energy" first, then Atwater specific, Atwater general, kJ duplicate. Never summed. |
| `protein` | 1003 | g | g | — | ≤ 100 | exact ID |
| `carbs` | 1005 → 1050 | g | g | — | ≤ 100 | "by difference" first, "by summation" only if absent |
| `fat` | 1004 | g | g | — | ≤ 100 | exact ID |
| `fiber` | 1079 | g | g | — | ≤ 100 | AOAC 2011.25 (2033) is a different method and is not substituted |
| `sugar` | 1063 → 2000 | g | g | — | ≤ 100 | current "Sugars, Total", legacy "Total Sugars" |
| `sodium` | 1093 | mg | mg | g ×1000, µg ÷1000 | ≤ 40 000 | canonical unit is mg |
| `saturated_fat` | 1258 | g | g | — | ≤ 100 | exact ID |

Additional rules:

- when the same nutrient ID appears more than once for a food, the entry with the most `dataPoints` wins; ties keep the first entry;
- `kcal`, `protein`, `carbs` and `fat` are required — a food missing any of them is rejected, not filled with zeros;
- optional nutrients stay `NULL` when absent; a measured `0` stays `0`;
- negative, non-finite, unknown-unit or over-limit values reject the whole food (they are never clamped);
- unknown nutrient IDs are counted in the staging report for coverage review and are not stored;
- `reference_amount`/`reference_unit` are always 100 g.

### Record shaping

- `name_original` keeps the USDA description with whitespace/NBSP cleanup only; no translation and no reordering.
- `food_translations` receives exactly one `en` row (`translation_source='usda'`). No `ru`/`uk` rows are invented; machine translation is a later, reviewed step.
- Category comes from a documented map of the 25 FDC categories to the existing catalog categories; unmapped categories become `Другое` (the original USDA category stays in the staging report).
- Foundation records are `ingredient`. SR Legacy records in Baked Products, Fast Foods, Mixed Dishes, Restaurant Foods, Prepared Meals or Sweets are `prepared_meal`; all others are `ingredient`.
- FNDDS (Survey) records are as-consumed dishes, so they are `prepared_meal` with category `Готовые блюда`; the detailed WWEIA category is kept in the staging report and is not forced into the ingredient category map.
- `preparation_state` is derived from explicit words in the description (`raw`, `cooked`, `boiled`, `roasted`, `baked`, `fried`, `grilled`, `steamed`, `canned`, `frozen`, `dried`); absence stays `NULL`.
- Servings come from `foodPortions` (max five, gram weights must be positive). Numbered household measures come first and the first one is the default; descriptions such as `RACC` and `Quantity not specified` follow. FNDDS "Guideline amount per ..." rows are nutrient guidance, not portions, and are skipped. Numeric FNDDS `modifier` codes are never used as text; `Quantity not specified` becomes a `portion` serving. A food without usable portions gets a single `100 g` serving.
- `approximate=false` for USDA records: those values are measured/calculated by USDA, not estimated by this project.
- Provenance: `source='usda'`, `source_id=fdcId`, `source_url=https://fdc.nal.usda.gov/food-details/<fdcId>/nutrients`, `nutrient_profiles.dataset_version=<release>`, `nutrient_profiles.source_record_id=fdcId`.

### Duplicates

- `source + source_id` is unique, so re-running the same release never duplicates rows.
- An existing canonical name (normalized) is a hard skip and is reported.
- Similar names are reported with a Jaccard score (≥ 0.6, at least two shared tokens) against `catalog_foods` and `community_foods`. They are **not** merged: the decision stays manual.
- A record that conflicts with an existing row keeps the existing nutrient profile untouched.

### Licensing

USDA FoodData Central is CC0 1.0: no attribution required, permanent storage, translation and redistribution are allowed. The `food_sources.usda` row records `license='CC0 1.0'`, `storage_permission=true`, `translation_permission=true`, `redistribution_permission=true`, `approval_status='approved'`. Branded Foods is excluded by importer policy even though the licence would allow storage, because its data quality and duplication need a separate review.

### Limitations

- Branded Foods is refused until a separate quality/licensing review; `food_sources.usda.data_types` describes the provider's capability, not import permission.
- FNDDS is parsed and structurally verified but not imported; its WWEIA categories are kept only in the staging report.
- The importer never downloads anything: obtain the archive from the official page and pass a local path.
- SR Legacy is frozen at its April 2018 release; later changes arrive only through Foundation Foods or the full download.
- New records get an English translation only. Localization is a separate, reviewed step; machine output must never be marked verified.
- Foundation and SR Legacy carry no GTIN, so their records have no barcode and are found by name/alias only.
- A food with a negative or impossible core nutrient is rejected rather than clamped, and a food missing kcal/protein/carbs/fat is rejected rather than zero-filled.
- Similar names are reported, never merged; merging stays a manual decision.
- Serving labels are derived from USDA portion text and may be coarse for unusual measures.

## Multilingual search

`GET /nutrition/catalog/search` is the single search path used by the Mini App. It runs on PostgreSQL and never calls an external provider:

```text
query → normalize → query variants (ru/uk transliteration) → indexed match
→ rank → serialize with the requested display language
```

### Stored search terms

`food_search_terms` is a derived index, not a source of truth:

| Column | Meaning |
|---|---|
| `food_id` | canonical food (`ON DELETE CASCADE`) |
| `language` | translation/alias language or `und` for the canonical name |
| `term_type` | `canonical`, `translation`, `alias`, `transliteration` |
| `term` / `normalized` | display value and its search form |

Rows are rebuilt from `catalog_foods`, `food_translations` and `food_aliases` by imports, the translation workflow and `search reindex`. Only translations with `review_status='approved'` are indexed; pending machine translations stay in `food_translations` but are not searchable until reviewed. Transliterations are stored here, never in `food_aliases`.

`community_foods.normalized_name` is the same idea for user barcode products: it is written when the product is saved and backfilled by migration `0008`.

### Normalization

`app/services/food_search.py::normalize_text`:

- Unicode NFKC, then casefold;
- `ё` → `е`;
- apostrophes removed (`м'ясо` → `мясо`);
- dashes and other punctuation become spaces (`по-деревенски` → `по деревенски`);
- diacritics stripped through NFKD (`café` → `cafe`). This also folds decomposable Cyrillic letters (`ё→е`, `й→и`, `ї→і`, `є→е`) in the search form only; originals stay intact and rare minimal pairs may collide on purpose.
- whitespace collapsed and trimmed.

Original database values are never modified.

### Transliteration

Search-oriented tables (not official standards) for Russian and Ukrainian, applied only to Cyrillic text:

- Russian: `картофель` → `kartofel`, `картошка` → `kartoshka`;
- Ukrainian: `картопля` → `kartoplya` and `kartoplia`; `хліб` → `khlib` and `hlib`.

Each term yields up to three variants: the primary table form, a soft-vowel form (`ya/yu/ye/yi` → `ia/iu/ie/i`), and a `kh` → `h` form. The query gets the same variants, so `kartoplia` matches `картопля` without storing a wrong alias.

### Ranking

| Priority | Match | Score |
|---|---|---|
| 1 | exact barcode (`food_barcodes` or `community_foods`) | 1000 |
| 2 | exact canonical name | 100 |
| 3 | exact translation | 90 |
| 4 | exact alias | 80 |
| 5 | exact transliteration | 70 |
| 6 | canonical prefix | 60 |
| 7 | translation prefix | 56 |
| 8 | alias prefix | 52 |
| 9 | transliteration prefix | 45 |
| 10 | partial canonical / translation / alias / transliteration | 30 / 27 / 24 / 20 |

- `lang` adds `+3` when the matched term is in that language; it never filters results.
- Ties break on the shorter matched term, then on the stable `catalog_foods.id`.
- Source type (branded/community/curated) adds no score; there is no popularity input.
- Canonical `id` and nutrient profiles are only read, never written by search.

### Performance

Measured on PostgreSQL 16.15:

| Case | Rows | Plan | Time |
|---|---|---|---|
| real catalog exact | 759 | B-tree index | 0.068 ms |
| real catalog prefix | 759 | B-tree range | 0.113 ms |
| real catalog substring | 759 | seq scan | 0.203 ms |
| synthetic exact | 200 000 | B-tree index | 0.063 ms |
| synthetic prefix | 200 000 | B-tree range | 0.080 ms |
| synthetic substring, no trigram | 200 000 | seq scan | 43 ms |
| synthetic substring, `pg_trgm` GIN | 200 000 | bitmap index | 0.4 ms |

Migration `0008` creates the trigram index (`ix_food_search_terms_normalized_trgm`) only on PostgreSQL and only when `pg_trgm` can be installed; otherwise search stays correct and substring predicates use a sequential scan. Example index size at 200k terms: 6.6 MB for the GIN index.

### Commands

```bash
# rebuild the derived index after normalization changes or manual SQL edits
python -m tools.catalog_import search reindex --dry-run
python -m tools.catalog_import search reindex --commit
```

### Adding a language

1. Insert or import `food_translations` rows (`language` is a free string tag).
2. Run `search reindex` (or import through the workflow, which rebuilds affected foods).
3. Add a transliteration table only when the new script needs one; Latin-script languages need none.
4. No schema migration is required.

## Controlled translation workflow

Machine output is never presented as verified. The workflow is export → human review → import:

```bash
# one worksheet per target language; name is empty for the reviewer to fill
python -m tools.catalog_import translations export --language ru --out ru-review.json

# validate without writing
python -m tools.catalog_import translations import --input ru-review.json --dry-run

# write reviewed translations and rebuild search terms
python -m tools.catalog_import translations import --input ru-review.json --commit \
  --report ru-import.json
```

Rules enforced by `import_translations`:

- `translation_status='verified'` requires `review_status='approved'`;
- `machine`/`draft` default to `review_status='pending'` and are not searchable until approved;
- an approved translation is never downgraded by a later machine record;
- only known `(source, source_id)` foods are accepted;
- every successful write rebuilds that food's search terms.

## Prepared meals catalog

Prepared meals are canonical foods of type `prepared_meal` with a real composition, not a name with guessed calories. `backend/data/prepared_meals.json` is the project-authored pilot (source `manual`, provenance recorded as project recipes — never USDA).

### Composition model

| Table | Holds |
|---|---|
| `catalog_foods` | canonical dish, per-100-g fallback values, `approximate` flag |
| `recipe_ingredients` | one row per ingredient: linked food, grams, unit, `position`, `preparation_state` (raw/cooked), `edible_portion`, `nutrient_snapshot` |
| `recipe_details` | cooked weight, uncooked edible mass, portion count, `quality` (measured/estimated), calculation method, dataset version, notes |
| `nutrient_profiles` | calculated per-100-g profile with `source_code='manual'` and `dataset_version` |

`nutrient_snapshot` keeps the ingredient's per-100-g values used at calculation time, so a recipe's provenance survives later ingredient-profile changes. The model is an extension of the existing recipe tables, not a parallel recipe system (migration `0009_recipe_composition`).

### Calculation

```text
total_nutrient = Σ ingredient_nutrient_per_100g × grams × edible_portion / 100
nutrient_per_100g = total_nutrient / cooked_weight_grams × 100
```

Rules:

- ingredient references must exist in the catalog and have a primary profile with kcal, protein, carbs and fat;
- cooked weight is required and must give a yield ratio between 0.3 and 3.0 of the edible ingredient mass (water uptake and cooking loss are legitimate);
- optional nutrients (fiber, sugar, sodium, saturated fat) are aggregated only when **every** ingredient has a value, otherwise they stay `NULL`;
- kcal/100 g must be ≤ 950, each macro ≤ 100 g, sodium ≤ 40 000 mg — impossible results reject the recipe;
- a recipe is `approximate=true` when its yield quality is `estimated` or any ingredient is approximate;
- ingredient amounts are grams; `edible_portion` defaults to 1.0 (peeled potato 0.85, carrot 0.85, apple 0.9, etc.);
- salt and cooking water are not included, so sodium values are lower bounds; butter/oil/salt entries in the Foundation download lack a complete macro profile and were not imported or fabricated.

### Pilot

| Metric | Value |
|---|---|
| dishes | 56 |
| categories | Готовые блюда 13, Гарниры 9, Мясные блюда 7, Завтраки 7, Салаты 6, Супы 5, Овощи 4, Рыбные блюда 4, Закуски 1 |
| ingredient links | 274 (249 raw, 25 cooked) |
| translations | 168 (56 × en/ru/uk, all `approved`) |
| aliases | 112 |
| servings | 56 (bowl/serving units with realistic grams) |
| profiles | 56, `dataset_version='recipes-2026-11'` |
| kcal/100 g | 26.7–288.7, average 136.8 |
| yield ratio | 0.68–2.67 |
| quality | 56 estimated, 0 measured (all marked approximate) |
| optional nutrients | `NULL` for all 56 dishes because at least one ingredient lacks each optional value |

The pilot includes borscht, mashed potatoes, vareniki, syrniki, buckwheat, rice, pasta, chicken and vegetable dishes. Exact duplicates of the Stage 6.7 approximate meals were not overwritten; the new recipe variants have distinct names (for example “Borscht with beef” next to the existing “Borscht”). One potential duplicate (“Chicken breast baked with vegetables” vs “Baked chicken breast”) is reported for manual review and was not merged.

### Products and variants

Variants with different composition are separate canonical foods and separate nutrient profiles: mashed potatoes with milk, with sour cream, or with butter are different records if their ingredient lists differ. Recipe imports are idempotent by `source + source_id`; a repeated run skips all dishes and never duplicates ingredient rows.

The Ukrainian translation of the existing `Potato` was corrected from `Картофель` to `Картопля` through the translation workflow (`translations import`, verified/approved, manual provenance), and its search terms were rebuilt. `backend/data/catalog_pilot.json` carries the same correction for fresh installs.

## Pilot dataset

`backend/data/catalog_pilot.json` contains 60 reviewed records:

- 20 ingredients and 40 prepared meals;
- 13 categories;
- 180 translations in `en`, `ru`, `uk`;
- 24 aliases;
- 60 primary nutrient profiles and 60 servings;
- sources: 20 USDA, 40 documented recipes;
- optional nutrients are null, while known zero sugar remains zero.

No branded products were imported in the pilot because no branded bulk source is approved yet. Open Food Facts bulk import remains pending ODbL review; FatSecret content is blocked.

Validation on a disposable SQLite database: dry-run reported 60 valid, 0 skipped, 0 errors; commit imported 60 records, 180 translations, 24 aliases, 60 nutrient profiles and 60 servings. A repeated commit skipped all 60 records as already imported.

### USDA pilot

From the official Foundation Foods October 2026 JSON: 424 foods processed, 364 valid, 150 selected round-robin across 14 categories, 60 rejected (50 missing a required nutrient, 10 with a negative carbohydrate value), 0 duplicate IDs.

Staging arithmetic (`valid_not_selected = valid - selected`):

```text
424 processed = 60 rejected + 150 selected + 214 valid but not selected
```

On a disposable PostgreSQL 16.15 database that already held the 60-record Stage 6.7 pilot:

- dry-run: 150 valid, 0 imported, 0 skipped, 0 errors;
- first commit: 150 imported, 150 translations, 150 nutrient profiles, 242 servings;
- repeat commit: 0 imported, 150 skipped as already imported;
- resulting catalog: 210 foods (60 previous + 150 USDA), 330 translations, 210 profiles, 302 servings, 24 aliases, 0 barcodes;
- translation distribution: `en=210`, `ru=60`, `uk=60` (Stage 6.7: 60 per language; Stage 6.8: 150 `en`). Per source: `manual` 40/40/40, `usda` 170/20/20;
- optional nutrients: 89 `fiber IS NULL` / 5 measured zero, 96 `sugar IS NULL` / 9 measured zero, 24 `sodium IS NULL`, 105 `saturated_fat IS NULL`;
- all 150 new profiles carry `dataset_version='2026-10-29'`, `source_code='usda'`, `source_record_id=fdcId` and `reference_amount=100 / 'g'`; the 20 Stage 6.7 USDA-derived records keep their original provenance untouched (`dataset_version IS NULL`);
- every one of the 150 new records has exactly one `en` translation; no `ru`/`uk` rows were invented.

Energy variant actually used by the 150 pilot records: `1008` for 89, `2048` (Atwater specific) for 52, `2047` (Atwater general) for 9, `1062` (kJ) for 0. The kJ → kcal path is covered by fixtures and unit tests, not by this pilot slice.

The USDA pilot is a verification dataset and is not committed to the repository; regenerate it with the `usda stage` command above.

## Verification status

Verified against disposable PostgreSQL 16.15 and 18.6 databases (all migrations `0001`–`0007`) and on SQLite:

- Alembic head is `0009_recipe_composition`; child tables, nullable nutrient columns, unique indexes and `ON DELETE CASCADE`/`SET NULL` foreign keys match the migrations.
- `0007_catalog_cleanup` drops the three duplicate `food_id` indexes and replaces the misleading `builtin` licence label; no catalog, diary or source row is deleted.
- `0008_multilingual_search` adds `food_search_terms` and `community_foods.normalized_name`, backfills both and creates the `pg_trgm` GIN index when available.
- Stage 6.7 pilot: 60 foods, 180 translations, 24 aliases, 60 profiles, 60 servings; repeated commit skips all 60.
- USDA pilot: 150 foods, 150 `en` translations, 150 profiles, 242 servings; repeated commit skips all 150.
- Data accounting: staging reports `processed`, `valid`, `selected`, `valid_not_selected` and `error_count`, and `processed = rejected + selected + valid_not_selected` (424 = 60 + 150 + 214). The full translation split is `en=210`, `ru=60`, `uk=60`; per-source queries must not be presented as global totals.
- Every one of the 150 new profiles is `source_code='usda'`, `dataset_version='2026-10-29'`, `source_record_id=fdcId`, `100 g` basis; staged and stored nutrients match exactly, including `NULL` vs `0`.
- Partial failures, missing required nutrients, duplicate source IDs, duplicate canonical names, duplicate barcodes, pending approval, disabled storage and disabled translation permissions all produce explicit errors or skip reasons and leave earlier records intact.
- Barcode lookup, confirmation, reload, repeat scan, `409` for another account and immutable diary snapshots are covered on SQLite and PostgreSQL.
- Language selection for `uk`, `ru`, `en`, `de`, unknown tags, region tags and uppercase tags is covered, and nutrients/identity stay identical across languages.
- The API serves the combined 210-record catalog with `lang=en/uk/de`, and USDA records appear with English names, correct nutrients and no fabricated translations.
- The FNDDS October 2024 JSON was staged structurally (5432 foods: 5431 valid, 1 without nutrient data) to verify the survey path; no FNDDS rows were imported.
- `0008_multilingual_search` backfilled 759 terms for the 210-food catalog (canonical 210, translation 330, alias 24, transliteration 195); no food is left without a term, and nutrients/translations are unchanged.
- `0009_recipe_composition` added ingredient state/edible portion/snapshots and `recipe_details`; 56 prepared meals were committed with 274 ingredient links and 56 calculated profiles, then a repeat import skipped all 56.
- Real-data searches were verified for `potato`, `POTATO`, `Картофель`, `картофель`, `kartofel`, `kartoshka`, `kartoplia` and `hummus`, across `lang=ru/uk/en/de`; the same canonical id and nutrient values are returned regardless of language.
- PostgreSQL 16 uses the B-tree exact/prefix indexes; on a 200k-term synthetic set the substring path drops from 43 ms (sequential scan) to 0.4 ms with the `pg_trgm` GIN index. On the current 759-term catalog the planner correctly prefers a 0.2 ms sequential scan.

Not yet verified: a real Telegram client E2E session, Open Food Facts bulk import (pending licensing review), and branded USDA import (deliberately out of scope).

## Adding a provider

1. Add or update a `food_sources` row with license and storage permissions.
2. Write a provider-specific adapter under `backend/tools/catalog_sources/`.
3. Map the provider payload into the reviewed JSON shape (see the USDA adapter for a reference).
4. Keep original names, source IDs, dataset version and source URL.
5. Run `usda stage`-equivalent staging, then dry-run, inspect duplicate reports and license skips.
6. Commit only approved records.
7. Never call the provider from Nutrition runtime.

## Adding a language

1. Insert a `food_translations` row with the language tag.
2. Mark the translation source and status.
3. Add reviewed aliases if needed.
4. No migration or new column is required.
5. Do not mark machine translations as verified.
