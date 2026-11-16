# Architecture

## Request path

Telegram opens the static React app and supplies signed `initData`. The frontend sends the original string in `Authorization: tma <initData>`. FastAPI validates Telegram's documented Web App HMAC construction, checks `auth_date`, and resolves a `User` row. API routes use the `current_user` dependency and scope private rows by `user_id`.

The bot only launches the app. It does not duplicate notes, tasks, or nutrition functionality. PostgreSQL is the Compose default; SQLite is useful for local iteration. Open-Meteo geocoding and forecast requests are performed by the backend, and the selected city is saved on the user; for coordinates the backend reverse-geocodes a place name through Nominatim. Open Food Facts lookups are performed by the backend; nutrition-label OCR and parsing run in the browser.

## Modules

- `app/api/`: HTTP schemas, validation, status codes, and route handlers for auth, notes, tasks, focus, weather, and nutrition.
- `app/dependencies/auth.py`: trusted user resolution from validated Telegram `initData`.
- `app/models/models.py`: SQLAlchemy entities and indexes for users, notes, tasks, focus sessions, meals, recipes, and community foods.
- `app/services/telegram_auth.py`: Telegram signature and expiry validation.
- `app/core/rate_limit.py`: per-user Redis token buckets, category limits, and fail-open/fail-closed behavior.
- `app/core/migration_lock.py`: PostgreSQL advisory lock that serializes migration jobs.
- `frontend/src/App.tsx`: compact Mini App shell, home overview, notes, tasks, weather, profile control, and the shared API request helper.
- `frontend/src/Nutrition.tsx`: compact nutrition log, add-food sheet, targets, recipes, food catalog, barcode/OCR, copy-day, and CSV UI.
- `frontend/src/nutrition-macros.ts`: pure calorie-to-macro calculation and input validation.
- `frontend/src/nutrition-serving.ts`: pure per-serving calorie and macro recalculation.
- `frontend/src/food-catalog.ts`: normalized ingredient and prepared-meal catalog with type, serving and preparation metadata; the list is deduplicated and prepared meals are shown first.
- `frontend/src/focus/`: timer state machine, persistence, delivery queue, completion sound, settings disclosure, and Focus UI.
- `frontend/src/weather-location.ts`: last automatic/manual weather selection persistence.
- `bot/main.py` and `bot/config.py`: aiogram commands and Mini App URL configuration.

## Database schema management

The application does **not** create or patch tables at startup. Importing `app.main` must only register routes and dependencies; it must not run DDL.

Schema changes are applied exclusively by Alembic:

- `backend/alembic/versions/0001_initial.py` builds the baseline from the current SQLAlchemy metadata at migration time.
- `0002_add_last_city.py` adds the saved weather city.
- `0003_nutrition_schema.py` reconciles pre-Nutrition, partially patched, and already-current databases stamped `0002`. Its preflight checks required tables, columns, defaults, and index names/structures before running DDL.
- `0004_focus_session_client_id.py` adds the optional `focus_sessions.client_id` column and the unique `(user_id, client_id)` index used for idempotent session delivery.

The Compose `migrate` service runs `alembic -c /app/alembic.ini upgrade head` once and exits. The backend starts only after it completes successfully. Alembic takes a PostgreSQL advisory lock (`app/core/migration_lock.py`) so concurrent migration jobs serialize. Migrations `0003` and `0004` are forward-only; rollback is a restore from a verified backup, not a downgrade.

## Client state

The Mini App keeps only presentational and device-local preferences in the browser. The Focus timer persists its state under `focus.timer.v1` (storage version 3; v1/v2 are still readable), the completion-sound preference under `focus.sound.enabled`, and the last weather selection under `weather.location.v1`. None of these replace server data: profile targets and Focus sessions remain authoritative in the API. The macro calculator fills the existing nutrition target fields and does not add a second target model. The food catalog is static frontend data; adding or changing catalog entries never rewrites historical `meal_entries`, which keep the name, serving and macros recorded when the meal was logged.

## Security boundaries

The browser-provided identity is never accepted as authority. API access requires signed `initData`. The bot token is only read by backend and bot processes. User-owned API lookups combine the object ID with the authenticated `user_id`, so guessed IDs do not expose another user's records.

All `/api/v1` routers use the rate-limit dependency after authentication. The limiter stores counters in Redis under hashed user keys, with independent categories for general requests, writes, weather, and Open Food Facts. Ordinary CRUD fails open if Redis is unavailable; external-provider endpoints fail closed with `503`. `/health` is not user-rate-limited.

## Failure boundaries

Weather and Open Food Facts failures are returned as provider errors rather than crashing the process. Reverse geocoding is best-effort: a Nominatim failure still returns the forecast with the generic “Моё местоположение” label. The backend health endpoint checks a database `SELECT 1` and returns `503` without exposing driver details when PostgreSQL is unavailable. Focus delivery retries are idempotent and remain in a local pending queue when a save fails. A blocked Web Audio context only suppresses the completion sound; the timer still completes and shows its visual message. The frontend keeps camera/OCR features optional and leaves manual nutrition entry available.
