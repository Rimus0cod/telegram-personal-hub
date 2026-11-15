# Telegram Personal Hub

#### Video Demo: <URL>

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

### Notes and tasks

Notes support create, read, update, delete, search, and Markdown rendering. Tasks support create, read, update, delete, completion toggling, priorities, due dates, filtering by status, and sorting by creation or due date. Updates reject explicit `null` values for non-nullable fields instead of turning them into database errors. Notes and tasks are isolated between Telegram accounts.

### Focus Timer

The Focus Timer supports focus, short-break, and long-break modes. The countdown is driven by an absolute deadline rather than by counting interval callbacks, so switching tabs, sleeping a device, or reloading the page does not silently lose time.

Completed sessions are stored through the API with duration, mode, start/finish timestamps, and a client-generated idempotency key. The frontend keeps unsaved sessions in a local pending queue, lets the user retry them explicitly, and preserves completed sessions across reloads. The backend normalizes timestamps to UTC, validates session windows, detects races with a unique `(user_id, client_id)` index, and returns `409` when the same `client_id` is reused with a different payload. The Focus statistics endpoint reports completed focus sessions and total focus minutes.

### Weather

Weather uses Open-Meteo for city geocoding and a five-day forecast. Users can search for a city or use browser geolocation; the last chosen city is saved on the user record. The backend merges multilingual geocoding results, validates coordinates, and returns clear `400`, `404`, `422`, or `502` errors when input or the external provider fails.

### Nutrition and КБЖУ

Nutrition tracks meals by day, meal type, calories, protein, carbohydrates, and fat (КБЖУ). Each meal stores its current serving size together with the original base serving and base macros. Changing the serving recalculates calories and macros from the base values, so repeatedly resizing a meal does not compound rounding errors or destroy the original nutrition data.

The Nutrition screen also supports daily calorie and macro targets, copying a day's meals to another day, CSV export, saved recipes, a shared community food catalog, barcode lookup, and optional OCR of nutrition labels. Camera and OCR features are optional; manual entry remains available.

### Recipes and food catalog

Saved recipes are private to the authenticated user and can be reused on a selected day. The community food catalog supports barcode lookup and user-contributed foods. It protects existing entries from being overwritten by another user, handles race conditions on duplicate barcodes, and supports weighted EAN-13 prefixes used by variable-measure products. Open Food Facts is used as an external fallback when a barcode is not in the local catalog.

### Telegram bot

The aiogram bot provides `/start` and `/help` and opens the Mini App. It intentionally does not duplicate notes, tasks, or nutrition logic; the Mini App and API remain the single implementation of the product features.

## Important files and directories

| Path | What it does |
|---|---|
| `backend/app/main.py` | Creates the FastAPI app, registers `/api/v1` routers, applies the rate-limit dependency, and exposes `/health`. |
| `backend/app/api/` | HTTP routes and request/response schemas for auth, notes, tasks, focus, weather, and nutrition. |
| `backend/app/models/models.py` | SQLAlchemy models and indexes for users, notes, tasks, focus sessions, meals, recipes, and community foods. |
| `backend/app/services/telegram_auth.py` | Telegram `initData` signature and expiry validation. |
| `backend/app/dependencies/auth.py` | Resolves the authenticated database user and applies profile validation. |
| `backend/app/core/rate_limit.py` | Per-user Redis token buckets, category limits, and failure policy. |
| `backend/app/core/migration_lock.py` | PostgreSQL advisory lock for serialized migrations. |
| `backend/alembic/` | Forward-only schema migrations, including Nutrition reconciliation and Focus idempotency. |
| `backend/tests/` | Auth, user isolation, CRUD, Focus, rate-limit, migration, and backup/restore tests. |
| `frontend/src/App.tsx` | Main Mini App shell, notes, tasks, weather, home screen, and API helper. |
| `frontend/src/Nutrition.tsx` | Nutrition UI, meals, targets, recipes, catalog, barcode, OCR, copy-day, and CSV export. |
| `frontend/src/focus/` | Timer state machine, persistence, delivery queue, hooks, modes, and UI tests. |
| `frontend/src/food-catalog.ts`, `food-search.ts` | Food normalization and remote catalog search helpers. |
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
- **Use forward-only Alembic migrations.** Schema changes are applied by Alembic, not at application startup. `0003_nutrition_schema` reconciles older and partially patched Nutrition schemas without dropping user tables; `0004_focus_session_client_id` adds the Focus idempotency key. A PostgreSQL advisory lock and one-shot migration service keep deployment migrations serialized.
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

The backend suite covers Telegram authentication, user isolation, CRUD validation, Focus sessions and races, Redis rate limiting, migration reconciliation, and backup/restore. PostgreSQL integration tests run only when `TEST_POSTGRES_URL` points at a disposable test database whose name contains `test`; backup/restore integration tests use the additional environment described in `backend/tests/test_backup_restore.py`. Redis integration tests require a disposable Redis service.

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

Remaining work is limited to the CS50x submission process: recording the video demo, adding its URL above, and a final submission review. The tracked repository is intentionally self-contained and does not include deployment secrets or user data.

## AI assistance

OpenCode with KIMI K3 was used for initial project analysis and auditing; it was not used to generate the application code. DeepSeek assisted with documentation, debugging, and implementation of selected functions in the Focus Timer, Weather, and Nutrition/КБЖУ areas. The specific assisted implementations are marked with concise comments in the source. AI use is disclosed in accordance with CS50's final-project policy.
