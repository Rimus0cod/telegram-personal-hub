# Telegram Personal Hub

#### Video Demo: https://youtu.be/RMdwVAtyoWU

Telegram Personal Hub is a CS50x Final Project and personal portfolio project: a private Telegram Mini App that brings daily weather, notes, tasks, a Focus Timer, and nutrition tracking with macros (КБЖУ) into one place. It is built as three cooperating applications: a React/Vite Mini App, a FastAPI backend, and an aiogram bot that launches the Mini App.

The goal is to reduce the friction of using several separate tools every day. A Telegram user opens the bot, taps one button, and gets a small personal workspace that is already authenticated by Telegram and scoped to their own data.

## Technologies and architecture

- **Frontend:** React 18, TypeScript, Vite, TanStack Query, Telegram WebApp `initData`, Nginx in production.
- **Backend:** FastAPI, SQLAlchemy 2, Pydantic 2, Alembic, PostgreSQL 16, Redis for rate limiting, Open-Meteo and Open Food Facts integrations.
- **Bot:** aiogram 3, `/start` and `/help`, opening the Mini App URL.
- **Infrastructure:** Docker Compose with PostgreSQL, Redis, a one-shot Alembic `migrate` service, the API, the bot, and an Nginx-served frontend.
- **Testing:** pytest for the backend and Vitest for the frontend.

Request path:

```text
Telegram bot
    -> Telegram Mini App (React/Vite)
    -> Nginx same-origin proxy
    -> FastAPI /api/v1
    -> PostgreSQL (SQLAlchemy) and Redis (rate limiting)
```

The frontend sends Telegram's original `initData` as `Authorization: tma <initData>`. The backend validates the HMAC signature and `auth_date`, resolves a database user, and every private query is filtered by that user's database ID. No mock authentication mode is provided for a plain browser preview.

## Features

### Telegram authentication and Mini App

The API verifies Telegram's Web App HMAC construction and rejects invalid, tampered, or expired `initData`. The resolved user is loaded or created server-side. The frontend never supplies an authoritative user ID, and private resources are always queried with the authenticated user's database ID.

### Interface

The Mini App uses a compact mobile-first layout with one primary action per screen and progressive disclosure for secondary settings. The home screen shows a weather summary and four quick destinations; the note composer, profile menu, weather search, Focus settings, nutrition goals, recipes, food search, barcode and OCR tools open only when needed. Shared spacing, typography, buttons, inputs and bottom sheets live in `style.css`; screen-specific styles stay in their own stylesheets.

### Notes and tasks

Notes support create, read, update, delete, search, and Markdown rendering. After a successful create or update the form clears and returns to create mode, while a failed save keeps the entered text. Tasks support create, read, update, delete, completion toggling, priorities, due dates, filtering by status, and sorting by creation or due date. Updates reject explicit `null` values for non-nullable fields instead of turning them into database errors. Notes and tasks are isolated between Telegram accounts.

### Focus Timer

The Focus Timer supports focus, short-break, and long-break modes with their recommended durations. The countdown is driven by an absolute deadline rather than by counting interval callbacks, so switching tabs, sleeping a device, or reloading the page does not silently lose time. The main screen keeps only the mode, clock and Start/Pause/Reset controls; custom duration and sound live in a compact “Настройки” section. Before a session starts, the user can enter a custom duration from 1 to 180 minutes; the recommended value stays visible, changing the mode restores that mode's standard duration, and the duration cannot change while a session is running or paused.

Completed sessions are stored through the API with duration, mode, start/finish timestamps, and a client-generated idempotency key. The frontend keeps unsaved sessions in a local pending queue, lets the user retry them explicitly, and preserves completed sessions across reloads. The backend normalizes timestamps to UTC, validates session windows, detects races with a unique `(user_id, client_id)` index, and returns `409` when the same `client_id` is reused with a different payload. The Focus statistics endpoint reports completed focus sessions and total focus minutes.

A short Web Audio signal plays once when a session actually completes; pausing or resetting does not play it. The sound can be disabled with a persisted toggle, and a completed-session message remains visible if the browser blocks autoplay. Timer persistence is version 3 (`focus.timer.v1`) and still reads the earlier v1/v2 records; older records use the standard duration for their mode.

### Weather

Weather uses Open-Meteo for city geocoding and a five-day forecast. Users can search for a city or use browser geolocation; the last chosen city is saved on the user record. For coordinates, the backend reverse-geocodes a place name through OpenStreetMap Nominatim and marks the response as `geolocation`, `manual`, or `saved`; if reverse geocoding fails, the forecast still loads with the neutral fallback “Моё местоположение”. The frontend shows the resolved place and the location source, persists the last auto/manual selection in `weather.location.v1`, and restores it when the Mini App reopens instead of silently replacing a manual city. The backend merges multilingual geocoding results, validates coordinates, and returns clear `400`, `404`, `422`, or `502` errors when input or the external provider fails.

### Nutrition and КБЖУ

Nutrition tracks meals by day, meal type, calories, protein, carbohydrates, and fat (КБЖУ). Each meal stores its current serving size together with the original base serving and base macros. Changing the serving recalculates calories and macros from the base values, so repeatedly resizing a meal does not compound rounding errors or destroy the original nutrition data.

The Nutrition screen keeps the day selector, calorie and macro summary, meal log and one “Добавить еду” action in view. Goals, macro percentages, saved recipes, CSV export and copy-day live in compact expandable sections. Adding food opens a bottom sheet with search, “Все / Блюда / Продукты”, category chips, a serving-size field and a live calorie/macro preview before the user confirms. The sheet distinguishes prepared meals from ingredients and keeps barcode, photo/OCR and recipe composition behind secondary tools.

Targets can be entered directly or derived from the calorie goal with a configurable macro split. The calculator uses 4 kcal/g for protein and carbohydrates and 9 kcal/g for fat, defaults to 30% protein / 40% carbohydrates / 30% fat as a starting point rather than a medical recommendation, rounds each macro once to whole grams, and shows the calorie total recalculated from those rounded grams. Editing a macro manually switches the calculator to manual mode so later calorie or percentage changes do not silently overwrite it. The calculator itself is not stored server-side; it only fills the existing target fields, which are saved as before.

### Recipes and food catalog

Saved recipes are private to the authenticated user and can be reused on a selected day. The catalog is loaded from PostgreSQL through `GET /api/v1/nutrition/catalog-items?lang=...`, with the built-in frontend list kept as an offline fallback. `catalog_foods` is the canonical food identity table; `food_translations`, `food_aliases`, `nutrient_profiles`, `food_servings` and `food_barcodes` hold multilingual names, search terms, provenance-aware nutrients, servings and package variants. `recipe_ingredients` stores recipe composition, and `food_sources` is the licence/source registry. Barcode products live in the shared `community_foods` table. All these sources are merged without exposing submitter identities. Prepared meals use documented composite recipes and are explicitly marked approximate; basic cooked values follow USDA FoodData Central. Search uses localized names, aliases and everyday synonyms.

Barcode flow: external lookup is only a preview. When the user confirms a product, the frontend POSTs it to `/nutrition/catalog`; the product is then read back from PostgreSQL on the next app open. Repeating a scan updates the same user's row and returns `409` for another user's unchanged barcode. Diary entries are independent snapshots, so later catalog changes never rewrite historical meals. Open Food Facts remains the external barcode fallback; a missing or failed lookup leaves manual entry available.

Search is server-side: `GET /api/v1/nutrition/catalog/search?q=...&lang=...` matches canonical names, approved translations, aliases and generated Russian/Ukrainian transliterations, then ranks exact barcode/name/translation/alias, prefixes, partial matches and transliterations. The query language is only a ranking preference and a display choice, so `картофель`, `картопля` and `kartofel` all find the same canonical food. Search terms are derived data (`food_search_terms`), rebuilt by imports and by `python -m tools.catalog_import search reindex`. The browser keeps its local matcher as a fallback when the request fails or the server returns no matches.

### Curated catalog import, USDA and FatSecret

`backend/tools/catalog_import.py` is a local-only tool, never imported by the FastAPI runtime. It validates reviewed JSON, checks the source/licence registry, detects duplicates, creates canonical records plus translations, aliases, nutrient profiles, servings and barcodes, and supports dry-run or idempotent commit. `backend/data/catalog_pilot.json` is the approved pilot: 60 records, 20 ingredients, 40 prepared meals, 180 translations in English/Russian/Ukrainian, 24 aliases and 60 nutrient profiles. `docs/CATALOG.md` is the full architecture and source-matrix reference.

`backend/tools/catalog_sources/usda.py` is the offline USDA FoodData Central adapter. It reads the official bulk downloads (Foundation Foods, SR Legacy, FNDDS) and maps USDA nutrient IDs to the catalog model with explicit unit conversion, documented variant priority, nullable optional values and provenance (`fdcId`, release version, source URL). Branded Foods is deliberately not importable yet. The adapter never runs in a request, so Nutrition keeps using PostgreSQL only.

```bash
# from backend/
python -m tools.catalog_import import --input data/catalog_pilot.json --dry-run
python -m tools.catalog_import import --input data/catalog_pilot.json --commit

# prepared meals with real composition (ingredients + cooked yield)
python -m tools.catalog_import import --input data/prepared_meals.json --dry-run
python -m tools.catalog_import import --input data/prepared_meals.json --commit

# USDA bulk import (download the official archive once, then stage locally)
python -m tools.catalog_import usda stage \
  --input FoodData_Central_foundation_food_json_2026-10-29.zip \
  --dataset-version 2026-10-29 --limit 150 \
  --out staging/usda_pilot.json --report staging/usda_report.json
python -m tools.catalog_import import --input staging/usda_pilot.json --dry-run
python -m tools.catalog_import import --input staging/usda_pilot.json --commit

python -m tools.catalog_import fatsecret-search --query "borscht" --out /tmp/fatsecret-preview.json
```

Search terms and translations are maintained locally:

```bash
python -m tools.catalog_import search reindex --dry-run
python -m tools.catalog_import search reindex --commit
python -m tools.catalog_import translations export --language ru --out ru-review.json
python -m tools.catalog_import translations import --input ru-review.json --commit
```

`translations import` enforces the review statuses: a `verified` translation must be `approved`, machine or draft records stay `pending` and out of search until reviewed, and an approved translation is never downgraded.

FatSecret is supported only as a temporary preview source: the command writes a review file that must be deleted within 24 hours. FatSecret's Terms of Use allow only identifiers such as `food_id`/`serving_id` to be stored indefinitely; names and nutrition values require a separate written custom-storage permission. The importer therefore refuses to commit FatSecret records. A future catalog expansion should use the project's own recipes, public-domain USDA data, or a documented FatSecret storage agreement.

The seed is idempotent: re-running it skips existing `(source, source_id)` records and duplicate normalized names. Similar names are reported for manual review and never merged automatically. Import does not modify user diary entries or community barcode products.

### Telegram bot

The aiogram bot provides `/start` and `/help` and opens the Mini App. It intentionally does not duplicate notes, tasks, or nutrition logic; the Mini App and API remain the single implementation of the product features.

## Important files and directories

| Path | What it does |
|---|---|
| `backend/app/main.py` | Creates the FastAPI app, registers `/api/v1` routers, applies the rate-limit dependency, and exposes `/health`. |
| `backend/app/api/` | HTTP routes and request/response schemas for auth, notes, tasks, focus, weather, and nutrition. |
| `backend/app/models/models.py` | SQLAlchemy models and indexes for users, notes, tasks, focus sessions, meals, recipes, community foods, canonical `catalog_foods`, translations, aliases, nutrient profiles, servings, barcodes, recipe ingredients and source registry. |
| `backend/app/services/telegram_auth.py` | Telegram `initData` signature and expiry validation. |
| `backend/app/services/food_search.py` | Query normalization, RU/UK transliteration, derived search terms and PostgreSQL ranking. |
| `backend/app/dependencies/auth.py` | Resolves the authenticated database user and applies profile validation. |
| `backend/app/core/rate_limit.py` | Per-user Redis token buckets, category limits, and failure policy. |
| `backend/app/core/migration_lock.py` | PostgreSQL advisory lock for serialized migrations. |
| `backend/alembic/` | Forward-only schema migrations, including Nutrition reconciliation and Focus idempotency. |
| `backend/tests/` | Auth, user isolation, CRUD, Focus, rate-limit, migration, and backup/restore tests. |
| `frontend/src/App.tsx` | Main Mini App shell, notes, tasks, weather, profile control, home screen, and API helper. |
| `frontend/src/Nutrition.tsx` | Compact nutrition screen, add-food sheet, meals, targets, recipes, catalog, barcode, OCR, copy-day, and CSV export. |
| `frontend/src/focus/` | Timer state machine, persistence, delivery queue, hooks, modes, settings UI, and UI tests. |
| `frontend/src/food-catalog.ts`, `food-search.ts` | Offline fallback catalog, normalization metadata, and multilingual/alias search helpers. |
| `frontend/src/nutrition-macros.ts` | Pure calorie-to-macro calculator and validation used by the Nutrition targets. |
| `frontend/src/nutrition-serving.ts` | Pure per-serving calorie and macro recalculation for the food catalog. |
| `backend/tools/catalog_import.py` | Local-only curated import tooling, duplicate detection, validation, and FatSecret preview guard. |
| `backend/tools/catalog_sources/usda.py` | Offline USDA FoodData Central adapter: bulk JSON/CSV parsing, nutrient mapping, unit conversion, provenance. |
| `backend/data/catalog_pilot.json` | Approved 60-record pilot with multilingual translations, aliases, nutrients and servings. |
| `backend/data/prepared_meals.json` | Curated 56-recipe prepared-meals pilot with ingredient composition and en/ru/uk translations. |
| `backend/tools/recipes.py` | Recipe validation and per-100-g nutrient calculation from ingredient profiles and cooked yield. |
| `docs/CATALOG.md` | Catalog architecture, source/licence matrix, import workflow and extension rules. |
| `frontend/src/weather-location.ts` | Persists the last automatic or manual weather selection across reloads. |
| `frontend/src/focus/sound.ts` | Web Audio completion signal and persisted sound preference. |
| `frontend/src/csv.ts` | CSV generation for nutrition export. |
| `frontend/nginx.conf` | Same-origin production proxy from the frontend to the API. |
| `bot/main.py`, `bot/config.py` | aiogram bot commands and Mini App URL configuration. |
| `docker-compose.yml` | PostgreSQL, Redis, one-shot migration, backend, bot, frontend, and optional ngrok tunnel. |
| `docs/` | Architecture, API, deployment, migration, and backup/restore documentation. |
| `scripts/` | PostgreSQL backup and restore verification scripts. |

## Design decisions

- **Authenticate on the server, never in the browser.** The backend validates Telegram's HMAC and `auth_date` before resolving a user. This prevents a modified client from claiming another Telegram identity.
- **Scope every private query by the authenticated user.** Object lookups combine the requested ID with the current user's database ID and return `404` when the resource is not owned. This avoids leaking whether another user's record exists.
- **Make Focus session delivery idempotent.** A client-generated UUID plus a unique `(user_id, client_id)` index makes retries safe. Reusing a key with a different payload is a conflict, not a duplicate.
- **Use atomic Redis token buckets.** A Lua script performs check-and-consume in one Redis operation, so multiple workers share one clock and budget. Ordinary CRUD fails open when Redis is unavailable, while external-provider endpoints fail closed.
- **Use forward-only Alembic migrations.** Schema changes are applied by Alembic, not at application startup. `0003_nutrition_schema` reconciles older and partially patched Nutrition schemas without dropping user tables; `0004_focus_session_client_id` adds the Focus idempotency key; `0005_catalog_foods` adds the canonical catalog; `0006_global_catalog` adds translations, aliases, nutrient profiles, servings, barcodes, recipe ingredients and the source registry; `0007_catalog_cleanup` removes duplicate `food_id` indexes and clarifies the `builtin` licence label; `0008_multilingual_search` adds the derived search-term index and normalized community names; `0009_recipe_composition` adds ingredient state/edible portion/snapshots and cooked-yield metadata. A PostgreSQL advisory lock and one-shot migration service keep deployment migrations serialized.
- **Separate base nutrition values from the current serving.** Storing `base_*` macros and `serving_grams` lets the UI rescale a meal without repeatedly scaling already-scaled numbers.
- **Harden the deployment boundary.** Backend and bot run as non-root, base images are pinned by digest, Compose applies resource and log limits, and backup/restore scripts verify archives before use. These measures protect user data and make recovery predictable.

## Running locally

### Docker Compose

1. Copy `.env.example` to `.env` and set `BOT_TOKEN`, `POSTGRES_PASSWORD`, `WEBAPP_URL`, and an explicit `COMPOSE_PROJECT_NAME`.
2. Start the stack:

   ```bash
   docker compose up --build
   ```

   The one-shot `migrate` service applies Alembic before the backend starts. If migration fails, the backend is not started.

3. Open `http://localhost:8080` for a preview, or configure an HTTPS reverse proxy and set that public URL in BotFather.
4. Send `/start` to the bot and open the Mini App.

Set `COMPOSE_PROJECT_NAME` explicitly for each environment so image, container, and volume names are predictable. If it is omitted, Compose derives a name from the directory, which is not a safe way to manage production.

### Backend

```bash
cd backend
python -m venv .venv
# activate the virtual environment
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

For local development, set `DATABASE_URL` (SQLite or PostgreSQL), `BOT_TOKEN`, and optionally `REDIS_URL`. Without `REDIS_URL`, rate limiting is disabled. Runtime dependencies are hash-locked in `requirements.txt`; `requirements-dev.txt` adds test tooling.

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

The frontend calls the API from its own origin. For separate local servers, set `VITE_API_URL` to a reachable backend URL. A plain browser preview is intentionally unauthenticated; use a Telegram launch or an HTTPS tunnel for the real Mini App flow.

### Bot

```bash
cd bot
pip install -r requirements.txt
BOT_TOKEN=<token> WEBAPP_URL=<https-url> python main.py
```

## Testing

Backend:

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

The backend suite covers Telegram authentication, user isolation, CRUD validation, Focus sessions and races, Redis rate limiting, migration reconciliation, catalog import validation, catalog API merging, and backup/restore. PostgreSQL integration tests run only when `TEST_POSTGRES_URL` points at a disposable test database whose name contains `test`; backup/restore integration tests use the additional environment described in `backend/tests/test_backup_restore.py`. Redis integration tests require a disposable Redis service.

Frontend:

```bash
cd frontend
npm ci
npm test
npm run build
```

Vitest covers timer state transitions, timer persistence and delivery queues, and CSV generation. `npm run build` runs TypeScript checking and a production Vite build.

## Current status

The application is implemented, tested, and running as a private personal deployment. The main features are usable end to end: Telegram authentication, notes, tasks, weather, Focus Timer, nutrition, recipes, food catalog, and the bot entry point.

Remaining work is the official CS50 submission: the video form, `submit50 cs50/problems/2026/x/project`, and the Gradebook/certificate check. The tracked repository is intentionally self-contained and does not include deployment secrets or user data.

## AI assistance

OpenCode with KIMI K3 was used for initial project analysis and auditing; it was not used to generate the application code. DeepSeek assisted with documentation, debugging, and implementation of selected functions in the Focus Timer, Weather, and Nutrition/КБЖУ areas. The specific assisted implementations are marked with concise comments in the source. AI use is disclosed in accordance with CS50's final-project policy.
