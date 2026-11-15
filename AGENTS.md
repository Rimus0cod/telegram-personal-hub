# AGENTS.md

## Project shape

- Three separate applications, with no root task runner or JS workspace: FastAPI in `backend/` (`app.main:app`), React/Vite in `frontend/`, and an aiogram polling bot in `bot/main.py`. The bot only launches the Mini App; Compose also runs PostgreSQL, Redis for API rate limiting, a one-shot Alembic `migrate` service, and an Nginx-served frontend.
- The backend container runs only Uvicorn. Compose's `migrate` service applies `alembic upgrade head` before the backend starts; the backend is not started if migration fails. The compose database is PostgreSQL 16.
- Images are named `<COMPOSE_PROJECT_NAME>-backend`, `-bot` and `-frontend`; `COMPOSE_PROJECT_NAME` is required in `.env` so the Compose project name and image names always match. Parallel projects must use a different value; `-p` alone does not rename images. Backend and bot run as UID/GID 10001; frontend Nginx keeps its upstream root master and drops workers to `nginx`. Compose applies memory/CPU limits and json-file log rotation.
- Backend routers are registered in `backend/app/main.py` under `/api/v1`; `/health` is deliberately outside that prefix. Nutrition is split into `app/api/nutrition.py` and `frontend/src/Nutrition.tsx`; the other screens and the shared frontend `request` helper live in `frontend/src/App.tsx`.
- The frontend sends raw Telegram `initData` as `Authorization: tma <initData>`. Resolve users with `app/dependencies/auth.py::current_user` and scope private queries by `user.id`; there is intentionally no mock-auth mode for a plain browser preview.
- Production same-origin proxying is in `frontend/nginx.conf`. Vite has no API proxy: for separate local frontend/backend servers, set `VITE_API_URL` to the backend origin or requests will go to the Vite origin.

## Commands

Run package commands from their directories, not the repository root.

```bash
# backend/
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
python -m pytest
python -m pytest tests/test_telegram_auth.py::test_valid_signed_init_data
alembic upgrade head

# frontend/
npm ci
npm run dev
npm run build       # tsc -b && vite build

# repository root
docker compose up --build
docker compose --profile tunnel up -d ngrok
```

`npm run build` and `npm test` are the configured frontend verification commands; there is no frontend lint script. The backend pytest suite covers auth, migrations, API isolation/CRUD, Focus and rate limiting; PostgreSQL and Redis integration tests run only when their environment variables point at disposable test services.

For local Python processes, configuration comes from process environment variables via `os.getenv`; neither `uvicorn` nor `bot/main.py` auto-loads the repo `.env` files. The backend defaults to `sqlite:///./hub.db`, but authenticated requests still require `BOT_TOKEN`. Docker Compose reads the root `.env` and requires `BOT_TOKEN` for the bot service.

## Schema and runtime gotchas

- Schema changes are applied **only** by Alembic. The one-shot `migrate` service runs `alembic upgrade head` before the backend starts; the backend container runs only Uvicorn. Importing `app.main` must never create or patch tables. `alembic/versions/0001_initial.py` still builds from the current model metadata, while `0003_nutrition_schema.py` reconciles pre-Nutrition, partially patched, and already-current databases stamped `0002`. When changing models, add an explicit revision and, if older installations may lack the change, reconcile their state the same way. `0003_nutrition_schema` is forward-only: never use `alembic downgrade base` as a rollback; restore a verified backup instead.
- Weather and food-catalog endpoints make live calls to Open-Meteo and Open Food Facts. Nutrition OCR loads Tesseract.js from jsDelivr at runtime, and camera scanning relies on the browser's `BarcodeDetector`; these features have dependencies beyond the Compose services.

## Development rules and safety

- Treat the existing repository implementation as the source of truth. Do not replace working modules with simplified rewrites.
- Preserve all existing functionality, including Nutrition, unless the user explicitly requests its removal.
- Before modifying a module, inspect its frontend, backend, database models, API schemas, and related tests.
- Keep the three applications independently runnable: backend, frontend, and bot.
- Do not introduce a root JavaScript workspace or task runner.

### Database and migrations

- SQLAlchemy models define the current application schema.
- Alembic revisions must contain explicit, reproducible schema operations.
- Do not use `Base.metadata.create_all()` as a substitute for production migrations.
- Do not add ad-hoc schema changes to application startup.
- Before changing a model, inspect existing migrations and consider already deployed databases.
- Never drop or recreate user data during routine development or migrations.

### Security

- Telegram `initData` must be validated on the backend.
- Never trust Telegram user information received directly from the frontend.
- Every private database query must be scoped to the authenticated `user.id`.
- Never expose bot tokens, database credentials, or other secrets to the frontend.
- Never log raw Telegram `initData` or authorization headers.
- Validate all client-provided timestamps, durations, identifiers, and enum values.
- Keep authentication mandatory. Do not introduce a mock-auth bypass unless explicitly requested for a controlled test environment.

### Frontend and external services

- Preserve Telegram theme integration and mobile-first responsive behavior.
- Handle loading, empty, error, and offline states.
- External API failures must not crash the application.
- Keep Open-Meteo, Open Food Facts, jsDelivr, and browser camera dependencies documented.
- Camera access must be optional; manual barcode entry must remain available.

### Verification

After each meaningful change:

1. Run relevant backend tests.
2. Run `npm run build` and `npm test` from `frontend/`.
3. Validate database migrations against a test database.
4. Check that user data remains isolated between different Telegram accounts.
5. Report exactly which checks passed and which were not executed.

Never claim that a feature or test works without verifying it.

## Deployment to the remote server

After completing the requested development work and verifying it locally, deploy the updated project to the remote server via SSH.

**Server connection:**
- SSH user: `diff`
- SSH host: `192.168.0.235`
- Connection: `ssh diff@192.168.0.235`
- Authentication: password-based or another method configured by the user. Ask the user to enter the password interactively when prompted. Never ask the user to put the password in source code or configuration files.

**Deployment rules:**
1. First inspect the server environment, existing project location, running services, and deployment method.
2. Do not assume the remote project path. Discover it before uploading.
3. Before modifying remote files, create a backup of the current deployment or ensure there is a reliable rollback method.
4. Upload only the necessary changed files. Do not overwrite `.env`, secrets, database files, user data, or server-specific configuration.
5. Never upload `.git`, `node_modules`, Python virtual environments, caches, or local development artifacts unless explicitly required.
6. Preserve existing database contents. Never run destructive migrations, database resets, or volume deletion commands without explicit user approval.
7. Install or update dependencies only when necessary.
8. Run database migrations carefully and verify their status.
9. Restart only the services required for the changes.
10. Check service status, application logs, backend health endpoint, and frontend availability after deployment.
11. If deployment fails, inspect the error, attempt a safe rollback when possible, and report the exact problem.
12. Never claim deployment succeeded without verifying the running application on the server.

**Important safety requirements:**
- Never store SSH passwords, private keys, tokens, or other secrets in the repository.
- Never print secrets in terminal output or logs.
- Never disable SSH host-key verification as a routine workaround.
- Never execute destructive commands on the server without explicit user approval.
- If SSH authentication, permissions, server paths, or deployment configuration require user input, stop and ask the user.

**Expected workflow:**

`Inspect → Implement → Test locally → Backup remote version → Upload changes → Apply migrations if needed → Restart services → Verify deployment → Report results`

At the end, provide a short deployment report containing:
- Changes made.
- Tests performed.
- Files uploaded.
- Services restarted.
- Deployment verification results.
- Any remaining issues.