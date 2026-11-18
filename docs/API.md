# API reference

The API is mounted under `/api/v1`. Every route in that prefix requires:

```http
Authorization: tma <Telegram WebApp initData>
```

The backend validates the Telegram signature and `auth_date` before resolving the user. Private resources are scoped to that user. `/health` is outside the prefix and is unauthenticated.

All `/api/v1` routers also use the Redis rate-limit dependency. A rejected request returns `429` with `Retry-After`, `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset` headers.

OpenAPI UI is available at `/docs` on the FastAPI container; it is not exposed through the public frontend proxy.

## Auth

| Method | Route | Request / response |
|---|---|---|
| GET | `/auth/me` | Returns the current user: `id`, `telegram_id`, `username`, `first_name`, `last_name`, `last_city`, and calorie/protein/carbs/fat targets. |
| PATCH | `/auth/preferences` | Body: `calorie_target` (500–10000), `protein_target` (0–1000, default 120), `carbs_target` (0–1500, default 250), `fat_target` (0–500, default 70). Returns the updated targets. |

## Weather

| Method | Route | Request / response |
|---|---|---|
| GET | `/weather/search?city=...` | Geocoding results from Open-Meteo. `city` length 2–100. |
| GET | `/weather?city=...` | Current weather and five-day forecast; saves the selected city on the user and returns `location_source: "manual"`. |
| GET | `/weather?latitude=...&longitude=...` | Forecast for coordinates. Latitude and longitude must be supplied together and be in valid ranges. The response includes a reverse-geocoded `city` when available and `location_source: "geolocation"`; a failed lookup falls back to “Моё местоположение”. |
| GET | `/weather` | Uses the user's saved `last_city` when no city or coordinates are supplied and returns `location_source: "saved"`. |

Responses include `city` and `location_source` (`manual`, `saved`, or `geolocation`); coordinate requests do not overwrite the saved city.

Errors: `400` when no location is available, `404` when a city is not found, `422` for invalid coordinates/city, `502` when Open-Meteo is unavailable.

## Notes

| Method | Route | Request / response |
|---|---|---|
| GET | `/notes?q=...` | Lists the current user's notes, newest update first. `q` searches title/content. |
| POST | `/notes` | Body: `title` (1–200), `content` (0–20000, default empty). Returns `201` and the created note. |
| GET | `/notes/{note_id}` | Returns one owned note or `404`. |
| PATCH | `/notes/{note_id}` | Optional `title` and `content`. Omitted fields are unchanged; explicit `null` is `422`. Returns the updated note. |
| DELETE | `/notes/{note_id}` | Deletes an owned note; `204` or `404`. |

## Tasks

| Method | Route | Request / response |
|---|---|---|
| GET | `/tasks?status=...&sort=...` | Lists the current user's tasks. `status` is `all`, `active`, or `completed`; `sort` is `created` or `due`. `sort=due` orders by due date, with null dates last. |
| POST | `/tasks` | Body: `title` (1–200), `description` (0–10000), `completed` (default false), `priority` (`low`, `medium`, `high`; default `medium`), `due_date` (optional datetime). Returns `201`. |
| GET | `/tasks/{task_id}` | Returns one owned task or `404`. |
| PATCH | `/tasks/{task_id}` | Optional fields; `due_date: null` clears the due date. Explicit null for title/description/completed/priority is `422`. Invalid priority is `422`. |
| DELETE | `/tasks/{task_id}` | Deletes an owned task; `204` or `404`. |

## Focus

| Method | Route | Request / response |
|---|---|---|
| POST | `/focus/sessions` | Body: `duration` (1–180 minutes), `mode` (`focus`, `short_break`, `long_break`), `completed` (default true), `started_at` (timezone-aware datetime), `finished_at` (optional timezone-aware datetime), `client_id` (optional UUID). Returns `201` and the session. |
| GET | `/focus/sessions?limit=50` | Recent sessions for the user; `limit` is 1–100. |
| GET | `/focus/stats` | Completed focus-session count, total focus minutes, and recent history. |

Session creation is idempotent when `client_id` is supplied. Reusing the same key with the same payload returns the existing session; reusing it with a different payload returns `409`. New sessions with invalid modes, missing timezones, timestamps older than seven days, future timestamps beyond tolerance, intervals over 24 hours, or inconsistent durations return `422`; an already stored session can still be replayed with its original `client_id`. Legacy requests without `client_id` remain supported.

## Nutrition

| Method | Route | Request / response |
|---|---|---|
| GET | `/nutrition?from=YYYY-MM-DD&to=YYYY-MM-DD` | Lists meals in the date range, ordered by creation time. The range must be 1–14 days and `to` must not be earlier than `from`; otherwise `400`. |
| POST | `/nutrition` | Body: `day`, `meal_type` (`breakfast`, `lunch`, `dinner`, `snack`), `name` (1–160), `calories` (0–20000), `protein`/`carbs`/`fat` (0–2000), `ingredients` (0–4000), `serving_grams` (1–10000). Returns `201`. |
| PATCH | `/nutrition/{meal_id}` | Body: `serving_grams` (1–10000). Recalculates calories and macros from the stored base values. Returns the updated meal or `404`. |
| DELETE | `/nutrition/{meal_id}` | Deletes an owned meal; `204` or `404`. |
| POST | `/nutrition/copy-day` | Body: `source_day`, `target_day`. Copies all meals to the target day and returns `{"copied": n, "day": "..."}`. Same day is `400`; an empty source day is `404`. |
| GET | `/nutrition/recipes` | Lists the current user's saved recipes. |
| POST | `/nutrition/recipes` | Body: `name` (1–160), `meal_type`, `ingredients` (1–100 items). Upserts by user and name; returns `201`. |
| DELETE | `/nutrition/recipes/{recipe_id}` | Deletes an owned recipe; `204` or `404`. |
| GET | `/nutrition/catalog/{barcode}` | Looks up 8–14 digits in community catalog, canonical `food_barcodes`, then Open Food Facts. Weighted EAN-13 prefix matching is supported. Returns `found` plus `product`/`source`; invalid barcode is `422`, provider failure is `502`. This lookup does not persist an external result. |
| POST | `/nutrition/catalog` | Body: `barcode` (8–14 digits), `name`, `brand`, `kcal`, `protein`, `carbs`, `fat`. Persists a confirmed barcode product in the shared catalog. Adds or updates the current user's entry; another user's duplicate is `409`. Returns `201`. |
| GET | `/nutrition/catalog-items?lang=ru&limit=1000` | Returns canonical `catalog_foods` plus shared `community_foods`. Items include `type`, `translations`, `aliases`, `search_terms`, `nutrients` (nullable optional values), `servings`, `barcode`, preparation state, approximate flag, source and review status. `display_language` states which language the returned `name` actually uses after fallback (`und` for a canonical name without a translation). `submitted_by` and other user identifiers are never returned. `lang` must be a language tag of 2–16 characters; it is lowercased (underscores become dashes) and region tags fall back to their primary subtag, so `ru-RU` resolves `ru`. A malformed tag is `422`. `limit` is 1–2000. |
| GET | `/nutrition/food-sources` | Returns the source/licence registry: license, attribution, storage/translation/redistribution permissions and approval status. No provider credentials are included. |
| GET | `/nutrition/catalog/search?q=potato&lang=ru&limit=50` | Ranked multilingual search over the PostgreSQL catalog. Matches canonical names, approved translations, aliases and generated transliterations; exact barcode matches come first. `lang` is a ranking preference, never a filter, so a Russian query finds an English canonical record and vice versa. Each item reports `display_language` for its returned name. `q` is 1–80 characters, `limit` 1–200. Returns `{items, count, language, query}`; `submitted_by` is never exposed. |
| GET | `/nutrition/catalog-search?q=...` | Searches Open Food Facts; `q` length 2–80. Provider failure is `502`. |

## Health

| Method | Route | Request / response |
|---|---|---|
| GET | `/health` | Unauthenticated. Executes `SELECT 1` against the configured database. Returns `200 {"status":"ok"}` when healthy or `503 {"detail":"Database unavailable"}` without exposing driver details. |

## Common errors

- `401`: missing, invalid, tampered, or expired Telegram `initData`.
- `404`: missing or unowned resource.
- `409`: conflicting Focus `client_id` or duplicate community barcode.
- `422`: invalid request body, enum, timestamp, coordinate, or barcode.
- `429`: rate limit exceeded; inspect `Retry-After` and `RateLimit-*` headers.
- `502`: Open-Meteo or Open Food Facts provider failure.
- `503`: database unavailable, missing bot configuration, or fail-closed external endpoint without Redis.
