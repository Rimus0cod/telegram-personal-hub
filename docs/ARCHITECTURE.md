# Architecture

## Request path

Telegram opens the static React app and supplies signed `initData`. The frontend sends the original string in `Authorization: tma <initData>`. FastAPI validates Telegram's documented Web App HMAC construction, checks `auth_date`, and resolves a `User` row. Routes use the `current_user` dependency and scope private rows by `user_id`.

The bot only launches the app. It does not duplicate notes/tasks functionality. PostgreSQL is the compose default; SQLite is useful for local iteration. Open-Meteo geocoding and forecast requests are performed by the backend, and the selected city is saved on the user.

## Modules

- `app/api/`: HTTP schemas, validation, status codes, and route handlers.
- `app/dependencies/auth.py`: trusted user resolution.
- `app/models/`: SQLAlchemy entities and indexes.
- `app/services/telegram_auth.py`: Telegram signature and expiry validation.
- `frontend/src/App.tsx`: compact Mini App screens and TanStack Query data access.

The initial schema is created at startup. Before production schema evolution, replace that shortcut with versioned Alembic migrations and a deployment migration step.

## Security boundaries

The browser-provided identity is never accepted as authority. API access requires signed initData. The bot token is only read by backend and bot processes. User-owned API lookups combine the object ID with authenticated `user_id`, so guessed IDs do not expose another user's records. Configure CORS narrowly when hosting frontend and API on separate origins.
