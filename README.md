# Telegram Personal Hub

A Telegram Mini App with personal weather, notes, tasks, and a focus timer. The React frontend talks to a FastAPI service; PostgreSQL stores user-scoped data; an aiogram bot opens the Mini App.

## Architecture

- `frontend/`: React, TypeScript, Vite, TanStack Query, Telegram WebApp initData.
- `backend/`: FastAPI, SQLAlchemy 2, Pydantic, Open-Meteo integration.
- `bot/`: aiogram 3 `/start` and `/help` commands.
- `docker-compose.yml`: PostgreSQL, Redis, API, bot, and Nginx-served frontend.

The API verifies Telegram's HMAC signature and `auth_date` before creating/loading a user. Every private resource query filters by that database user's ID. `BOT_TOKEN` stays in server containers.

## Rate limiting

Authenticated `/api/v1` routes use per-user Redis token buckets: 120 requests/min generally, 60 writes/min, 30 weather/min, and shared limits for Open Food Facts (30/min plus 20 searches/min or 10 barcode lookups/min and 100/hour). Limits are checked atomically in Redis after authentication; `/health` is not user-limited.

Compose starts an internal Redis service and configures `REDIS_URL`. For a backend run outside Compose, set `REDIS_URL` (for example `redis://localhost:6379/0`); when it is unset, rate limiting is disabled for local development. If Redis is configured but unavailable after startup, ordinary CRUD keeps working (fail-open), while Weather and Open Food Facts endpoints return `503` (fail-closed). Rate-limit counters are ephemeral and may be lost on restart.

## Requirements

Docker Compose v2 is recommended. For local development: Python 3.12+, Node.js 22+, PostgreSQL (or SQLite for a local API), and a Telegram bot token from [@BotFather](https://t.me/BotFather).

## Configure and run with Docker

1. Copy `.env.example` to `.env` and set `BOT_TOKEN`, a strong `POSTGRES_PASSWORD`, and the public HTTPS `WEBAPP_URL`.
2. Start everything with `docker compose up --build`.
3. Open `http://localhost:8080` for local preview, or configure an HTTPS reverse proxy and set that public URL as the bot's Mini App URL with BotFather.
4. Send `/start` to your bot and tap **Open Personal Hub**.

The API is served on the same origin under `/api/v1`; `/health` is the health endpoint and `/docs` is available on the backend container at port 8000 (publish it locally if needed).

To host the ngrok tunnel inside Docker as well, add your ngrok account authtoken to `NGROK_AUTHTOKEN` in `.env`, then start the tunnel profile with `docker compose --profile tunnel up -d ngrok`. The tunnel targets the frontend service on the Compose network.

## Local development

Backend, from `backend/`:

```bash
python -m venv .venv
# activate the environment, then:
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Runtime dependencies are locked with hashes in `requirements.txt`; `requirements-dev.txt` adds the test tooling and is the file to use for local development. Regenerate either lock from its `.in` file with the same command, for example:

```bash
uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes --output-file requirements.txt
uv pip compile requirements-dev.in --universal --python-version 3.12 --generate-hashes --output-file requirements-dev.txt
```

The bot has its own lock, `bot/requirements.txt`, generated from `bot/requirements.in` with the same options.

Set `DATABASE_URL=sqlite:///./hub.db`, `BOT_TOKEN`, and optionally `REDIS_URL=redis://localhost:6379/0` in the environment for local use. Without `REDIS_URL`, rate limiting is disabled. Frontend, from `frontend/`:

```bash
npm install
npm run dev
```

The browser preview is intentionally not authenticated. A valid Telegram launch provides signed `initData`; no mock user mode is provided. For local Mini App testing, expose the frontend through an HTTPS tunnel and configure that URL in BotFather. The frontend calls the API from its own origin, so use a development proxy or set `VITE_API_URL` to a reachable backend URL.

Bot, from `bot/`:

```bash
pip install -r requirements.txt
BOT_TOKEN=... WEBAPP_URL=https://your-domain.example python main.py
```

## Telegram and HTTPS setup

Create a bot with `/newbot` in BotFather and keep its token private. Configure `WEBAPP_URL` to the HTTPS URL where the frontend is hosted, then use BotFather's Mini App settings to set the same URL. Telegram requires HTTPS for a remotely hosted Mini App. For a server deployment, point a domain's DNS at the host and configure Caddy, Nginx, or another TLS reverse proxy to forward public HTTPS traffic to port 8080. Keep PostgreSQL and the API port private; the compose frontend proxy forwards `/api/` internally to FastAPI.

## Database migrations

The application no longer creates or patches tables at import time. The backend container runs `alembic upgrade head` before Uvicorn; for a local backend, run it manually from `backend/` before starting the API. Revision `0003_nutrition_schema` reconciles fresh databases, databases still at `0002_add_last_city`, and databases that were partially patched by older startup DDL, without dropping user tables. It is forward-only: use a verified PostgreSQL backup rather than `alembic downgrade` for rollback. Back up PostgreSQL before production upgrades.

## Tests and build

```bash
cd backend
pip install -r requirements-dev.txt
pytest
cd ../frontend
npm ci
npm test
npm run build
```

`docker compose up --build` builds all three application images.

## Common issues

- **401 in Mini App:** open from the bot; verify server `BOT_TOKEN` matches the bot that launched it and device time is correct.
- **initData expired:** launch the app again; max age is controlled by `INIT_DATA_MAX_AGE` (default 24 hours).
- **Bot cannot start:** set `BOT_TOKEN` and make sure another polling process is not already using the same bot.
- **Telegram refuses the Mini App URL:** configure a publicly reachable HTTPS URL in BotFather.
- **Weather errors:** check outbound access to Open-Meteo; it does not require a key.

## Current scope

The automated suite covers Telegram initData signature/expiry validation and forward SQLite migration scenarios (fresh, legacy `0002`, partially patched schema, repeated runs, and no import-time DDL). PostgreSQL migration coverage runs only when `TEST_POSTGRES_URL` points at a disposable test database whose name contains `test`. Expand it with API isolation, CRUD, focus statistics, and mocked weather provider cases before production. Timer state is client-side; completed focus sessions are persisted when a timer reaches zero.
