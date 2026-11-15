# First deployment

This runbook is for a Linux host with Docker Engine and Docker Compose v2+.
Replace every `<placeholder>`; never commit real passwords, tokens, host names
or IP addresses.

The application runs as three services plus PostgreSQL and Redis:

- one-shot `migrate` (Alembic, exits 0);
- `backend` (Uvicorn, non-root UID 10001);
- `bot` (aiogram, non-root UID 10001);
- `frontend` (Nginx, upstream image master process root, workers `nginx`);
- `db` (PostgreSQL 16) and `redis` (Redis 7, no external port).

Images are named `<COMPOSE_PROJECT_NAME>-backend`, `-bot` and `-frontend`, and
`COMPOSE_PROJECT_NAME` also sets the Compose project name, container names and
volume names. It is required: Compose fails fast if it is missing. Keep the
production value stable (for example `telegram-personal-hub`) and use a
different value plus a different port for a parallel/test stack. `-p` alone does
not rename images, so do not rely on it for isolation. Use lowercase letters,
digits, dashes and underscores only.

## 1. Read-only server audit

```bash
docker --version
docker compose version
docker ps -a
docker volume ls
docker compose ls -a
df -h /var/lib/docker
free -h
ss -ltn
```

Record existing containers, volumes, ports, production project paths and free
resources. Do not read `.env`, container environments with secrets, or database
contents. Do not stop or restart an existing installation during this step.

Decide a project name and a free localhost port. The project name must match
`COMPOSE_PROJECT_NAME` in `.env` for the real deployment:

```bash
export COMPOSE_PROJECT_NAME=telegram-personal-hub
export PORT=18080   # test only; production uses its chosen port
```

For a parallel test stack, use a different `COMPOSE_PROJECT_NAME` and port, and
its own `.env`/environment file. Never reuse the production name for tests.

## 2. Back up the current installation

Use the production `.env` only through the operator's secure environment; do
not paste it into commands or logs. The scripts use libpq `PG*` variables and
never print the password.

```bash
export PGHOST=<db-host>
export PGPORT=5432
export PGUSER=<db-user>
export PGDATABASE=<db-name>
export PGPASSFILE=<secure-pgpass>
export BACKUP_DIR=<secure-backup-directory>

bash scripts/backup-postgres.sh
```

If the host has no PostgreSQL client tools, run the same script in a temporary
PostgreSQL 16 client container on the application network, with the scripts and
backup directory mounted read/write only for that container. The script remains
the same; do not replace it with an ad-hoc dump.

## 3. Verify the backup

```bash
pg_restore --list <backup.dump> >/dev/null
sha256sum <backup.dump>
```

Store the checksum off-host. A backup that has never been restored is not
verified. Do not proceed if the archive is unreadable.

## 4. Prepare the new version

- preserve the existing `.env` and user data; do not upload `.env`, `.git`,
  `.venv`, `node_modules`, databases or caches;
- an `.env` created before this hardening must add
  `COMPOSE_PROJECT_NAME=<stable-name>` (for the current deployment,
  `telegram-personal-hub`); Compose fails fast if it is missing;
- build the three images from the reviewed tree;
- validate Compose interpolation and ports before starting anything.

```bash
docker compose config --quiet
docker compose build backend bot frontend
docker compose images
```

Only the frontend port is published; PostgreSQL, Redis and backend stay on the
Compose network. Check the published ports after start, without printing the
resolved environment:

```bash
docker ps --format '{{.Names}} | {{.Ports}}'
```

## 5. Apply migrations

```bash
docker compose up -d db redis
docker compose run --rm migrate
docker compose run --rm migrate   # idempotent repeat
docker compose run --rm backend alembic -c /app/alembic.ini current
```

Expected head: `0004_focus_session_client_id`. A non-zero migration exit must
stop the deployment; do not start backend manually or edit a published
migration. The backend Compose dependency also refuses to start after a failed
migration. `docker compose up -d` in the next step also runs the one-shot
`migrate` service again; that repeat is idempotent and expected.

## 6. Switch and start

During a maintenance window, stop the previous writers (only the application
project, never unrelated services), take a final backup, then start the new
stack:

```bash
docker compose up -d
docker compose ps
```

The `migrate` service runs once before backend. The bot and frontend wait for a
healthy backend. Do not run `docker compose down -v` on an existing project.

## 7. Health checks

```bash
curl -fsS https://<public-host>/health
curl -fsS https://<public-host>/
docker compose logs --no-color backend migrate | tail -50
docker compose exec -T backend alembic -c /app/alembic.ini current
```

Check:

- `/health` is 200 and reports `{"status":"ok"}`;
- frontend loads through the HTTPS reverse proxy;
- `/api/v1` without authorization is 401, not 500;
- migrations ran once, head is `0004_focus_session_client_id`;
- bot receives `/start` and opens the Mini App;
- PostgreSQL and Redis are healthy, no ports are published;
- logs contain no tokens, passwords or raw initData;
- resource usage is within the configured limits.

## 8. Rollback

If health or data checks fail:

1. stop the new backend/bot/frontend;
2. keep the failed database/volume for inspection;
3. restore the verified backup into a **new** database using
   `docs/BACKUP_RESTORE.md`;
4. perform the documented manual database-name switch (never an automatic
   destructive restore);
5. start the previous image tags or the restored stack;
6. verify `/health`, the Alembic head and key user data;
7. only after resolution remove the preserved failed database/volume.

Never roll back with `alembic downgrade base`. Migrations `0003` and `0004` are
forward-only. See `docs/MIGRATIONS.md` for the migration policy.

## Operational notes

- Backend/bot images run as UID/GID 10001; the frontend Nginx image remains
  upstream-root for its master process and drops workers to `nginx`.
- Compose applies memory/CPU limits and `json-file` log rotation. Values are
  sized for a personal deployment and can be raised only after measuring usage.
- Redis counters are ephemeral; losing them on a restart only briefly resets
  rate limits.
- Do not use global `docker system prune`, `volume prune`, or remove volumes
  belonging to another project.
