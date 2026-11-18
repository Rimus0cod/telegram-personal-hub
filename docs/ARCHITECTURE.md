# Architecture

## Request path

Telegram opens the static React app and supplies signed `initData`. The frontend sends the original string in `Authorization: tma <initData>`. FastAPI validates Telegram's documented Web App HMAC construction, checks `auth_date`, and resolves a `User` row. API routes use the `current_user` dependency and scope private rows by `user_id`.

The bot only launches the app. It does not duplicate notes, tasks, or nutrition functionality. PostgreSQL is the Compose default; SQLite is useful for local iteration. Open-Meteo geocoding and forecast requests are performed by the backend, and the selected city is saved on the user; for coordinates the backend reverse-geocodes a place name through Nominatim. Open Food Facts lookups are performed by the backend; nutrition-label OCR and parsing run in the browser.

## Modules

- `app/api/`: HTTP schemas, validation, status codes, and route handlers for auth, notes, tasks, focus, weather, and nutrition.
- `app/dependencies/auth.py`: trusted user resolution from validated Telegram `initData`.
- `app/models/models.py`: SQLAlchemy entities and indexes for users, notes, tasks, focus sessions, meals, recipes, community foods, canonical `catalog_foods`, translations, aliases, nutrient profiles, servings, barcodes, recipe ingredients and source registry.
- `app/services/telegram_auth.py`: Telegram signature and expiry validation.
- `app/services/food_search.py`: query normalization, Russian/Ukrainian transliteration, derived search terms and the PostgreSQL ranking used by `/nutrition/catalog/search`.
- `tools/recipes.py`: recipe validation and per-100-g nutrient calculation from ingredient profiles and cooked yield.
- `app/core/rate_limit.py`: per-user Redis token buckets, category limits, and fail-open/fail-closed behavior.
- `app/core/migration_lock.py`: PostgreSQL advisory lock that serializes migration jobs.
- `frontend/src/App.tsx`: compact Mini App shell, home overview, notes, tasks, weather, profile control, and the shared API request helper.
- `frontend/src/Nutrition.tsx`: compact nutrition log, add-food sheet, targets, recipes, food catalog, barcode/OCR, copy-day, and CSV UI.
- `frontend/src/nutrition-macros.ts`: pure calorie-to-macro calculation and input validation.
- `frontend/src/nutrition-serving.ts`: pure per-serving calorie and macro recalculation.
- `frontend/src/food-catalog.ts`: normalized ingredient and prepared-meal catalog with type, serving and preparation metadata; the list is deduplicated and prepared meals are shown first.
- `frontend/src/focus/`: timer state machine, persistence, delivery queue, completion sound, settings disclosure, and Focus UI.
- `frontend/src/weather-location.ts`: last automatic/manual weather selection persistence.
- `backend/tools/catalog_import.py`: local-only validation, deduplication, seed import, FatSecret preview tooling, and the `usda stage/import` CLI; never imported by the API runtime.
- `backend/tools/catalog_sources/usda.py`: offline USDA FoodData Central adapter (bulk JSON/CSV, nutrient mapping, unit conversion, provenance); never imported by the API runtime.
- `backend/data/catalog_pilot.json`: approved 60-record pilot with English/Russian/Ukrainian translations.
- `docs/CATALOG.md`: canonical model, source/licence matrix, import workflow and extension guide.
- `bot/main.py` and `bot/config.py`: aiogram commands and Mini App URL configuration.

## Database schema management

The application does **not** create or patch tables at startup. Importing `app.main` must only register routes and dependencies; it must not run DDL.

Schema changes are applied exclusively by Alembic:

- `backend/alembic/versions/0001_initial.py` builds the baseline from the current SQLAlchemy metadata at migration time.
- `0002_add_last_city.py` adds the saved weather city.
- `0003_nutrition_schema.py` reconciles pre-Nutrition, partially patched, and already-current databases stamped `0002`. Its preflight checks required tables, columns, defaults, and index names/structures before running DDL.
- `0004_focus_session_client_id.py` adds the optional `focus_sessions.client_id` column and the unique `(user_id, client_id)` index used for idempotent session delivery.
- `0005_catalog_foods.py` adds the shared curated `catalog_foods` table and its source/type/category indexes. It does not alter `community_foods`, `meal_entries`, or existing rows.
- `0006_global_catalog.py` adds `food_sources`, `food_translations`, `food_aliases`, `nutrient_profiles`, `food_servings`, `food_barcodes` and `recipe_ingredients`, and seeds the source/licence registry. It does not alter existing catalog or diary rows.
- `0007_catalog_cleanup.py` drops the three duplicate `food_id` indexes created twice by `0006` and replaces the misleading `builtin` licence label with an origin-aware one. It changes no catalog, diary or source row.
- `0008_multilingual_search.py` adds the derived `food_search_terms` table and `community_foods.normalized_name`, backfills both from existing rows, and adds a `pg_trgm` GIN index when the extension is available. It changes no catalog, diary, translation or alias row.
- `0009_recipe_composition.py` adds `recipe_ingredients.preparation_state`, `edible_portion` and `nutrient_snapshot`, plus the `recipe_details` table for cooked yield and quality. Existing recipes, nutrients and diary rows are untouched.

The Compose `migrate` service runs `alembic -c /app/alembic.ini upgrade head` once and exits. The backend starts only after it completes successfully. Alembic takes a PostgreSQL advisory lock (`app/core/migration_lock.py`) so concurrent migration jobs serialize. Migrations `0003`–`0009` are forward-only; rollback is a restore from a verified backup, not a downgrade.

## Client state

The Mini App keeps only presentational and device-local preferences in the browser. The Focus timer persists its state under `focus.timer.v1` (storage version 3; v1/v2 are still readable), the completion-sound preference under `focus.sound.enabled`, and the last weather selection under `weather.location.v1`. None of these replace server data: profile targets and Focus sessions remain authoritative in the API. The macro calculator fills the existing nutrition target fields and does not add a second target model. `catalog_foods` is the canonical PostgreSQL food identity table; the static frontend catalog remains an offline fallback. Translations, aliases, nutrient profiles, servings, barcodes, recipe ingredients and source provenance live in child tables, so adding a language is an insert, not a migration. Diary entries keep the name, serving and macros recorded when the meal was logged, so catalog updates never rewrite history.

## Catalog and barcode flow

External barcode lookup is a preview only. After the user confirms a product, the frontend writes it to the shared `community_foods` table through the authenticated API. Reopening the app loads `catalog_foods` plus `community_foods` from `/api/v1/nutrition/catalog-items`, so the product remains searchable without rescanning. Repeated scans update the same user's row; another user's barcode is `409`. The response omits `submitted_by` and all user identifiers.

Search is server-side and PostgreSQL-only. `/api/v1/nutrition/catalog/search` normalizes the query, adds Russian/Ukrainian transliteration variants, matches rows in the derived `food_search_terms` index (plus normalized community names), ranks exact barcode/name/translation/alias/prefix/partial/transliteration in that order and returns the same catalog payload. The requested language only adds a ranking bonus and changes display names; it never filters. The Mini App falls back to its local matcher only when the request fails, the user is offline, or the server returns no matches (the local matcher still handles word stems such as `картошку`). `search reindex` rebuilds the derived index after translation changes.

Curated imports are separate from runtime. `backend/tools/catalog_import.py` validates reviewed JSON, checks the `food_sources` licence registry, detects duplicates against canonical/translated/community names and barcodes, reports similar names for manual review, supports dry-run and idempotent commit, and never runs in a request. Prepared meals carry real composition: `recipe_ingredients` links each ingredient food with grams, edible portion and raw/cooked state, while `recipe_details` records cooked yield and quality. `tools/recipes.py` calculates per-100-g nutrients from the referenced ingredient profiles and rejects implausible yields or incomplete ingredients; ingredient values are snapshotted for provenance. Provider-specific adapters live in `backend/tools/catalog_sources/`; `usda.py` reads official FoodData Central bulk downloads, maps documented nutrient IDs with explicit unit conversion, keeps `fdcId`/release provenance and refuses Branded Foods until a separate review. Nutrition never calls USDA: every lookup reads PostgreSQL. FatSecret is used only for temporary previews because its Terms of Use allow indefinite storage only for identifiers; names and nutrition values require written custom-storage permission. The approved pilot is generated from our own data and USDA CC0 values. See `docs/CATALOG.md`.

## Security boundaries

The browser-provided identity is never accepted as authority. API access requires signed `initData`. The bot token is only read by backend and bot processes. User-owned API lookups combine the object ID with the authenticated `user_id`, so guessed IDs do not expose another user's records.

All `/api/v1` routers use the rate-limit dependency after authentication. The limiter stores counters in Redis under hashed user keys, with independent categories for general requests, writes, weather, and Open Food Facts. Ordinary CRUD fails open if Redis is unavailable; external-provider endpoints fail closed with `503`. `/health` is not user-rate-limited.

## Failure boundaries

Weather and Open Food Facts failures are returned as provider errors rather than crashing the process. FatSecret is not a runtime dependency: the Nutrition API and Mini App never call it, so a FatSecret outage or policy change cannot break catalog browsing, barcode lookup or diary writes. Reverse geocoding is best-effort: a Nominatim failure still returns the forecast with the generic “Моё местоположение” label. The backend health endpoint checks a database `SELECT 1` and returns `503` without exposing driver details when PostgreSQL is unavailable. Focus delivery retries are idempotent and remain in a local pending queue when a save fails. A blocked Web Audio context only suppresses the completion sound; the timer still completes and shows its visual message. The frontend keeps camera/OCR features optional and leaves manual nutrition entry available.
