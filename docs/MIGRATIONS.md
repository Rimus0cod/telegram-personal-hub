# Migration governance

## Current chain

```
<base> -> 0001_initial -> 0002_add_last_city -> 0003_nutrition_schema -> 0004_focus_session_client_id (head)
```

There is exactly one head (`0004_focus_session_client_id`) and no branches or
cycles. The chain is covered by `backend/tests/test_migration_governance.py`.

## Immutability of published revisions

Revisions `0001`–`0004` are **published and immutable**:

- never edit an existing revision file, its `revision` or `down_revision`;
- never rename a revision;
- never change a published migration to match a newer model;
- any schema change is a new, forward-only revision with explicit DDL.

The test suite stores the SHA-256 of the four published files (with line endings
normalized) and fails if a published migration changes. If an existing
migration genuinely must change, that requires explicit approval, a new
revision where possible, and a documented recovery plan for databases that
already ran the old file.

## `0001_initial` is frozen

`0001_initial` calls `Base.metadata.create_all()` / `drop_all()`. It is kept as a
historical baseline and must not be rewritten:

- on a fresh database it creates the **current** model schema at the moment the
  code runs, not a historical snapshot;
- every later migration therefore has to inspect the live database (as `0002`,
  `0003` and `0004` do) instead of assuming which columns exist;
- new revisions must never depend on `Base.metadata.create_all()` for their
  schema changes;
- `0001.downgrade()` would drop all user tables and is only reachable outside
  the normal chain; it is not a supported rollback.

A future "frozen historical baseline" would be a separate, carefully reviewed
project after a verified backup. It is not part of the current policy.

## Required workflow for a schema change

This section is for routine schema development. For the ordering of a first or
upgrade deployment (audit, backup, build, migration, switch, health checks,
rollback), follow `docs/DEPLOYMENT.md`; migrations are one step in that sequence,
never the first action on a live installation.

1. Take a verified PostgreSQL backup (`docs/BACKUP_RESTORE.md`).
2. Create a new revision:

   ```bash
   cd backend
   alembic revision -m "describe the change"
   ```

3. Write explicit `op.*` operations. Inspect existing tables/columns/indexes
   first, exactly like `0002`–`0004`, so both fresh and upgraded databases work.
4. Run the migration tests and a fresh/legacy upgrade against PostgreSQL 16.
5. Apply it through the one-shot migration service, then verify the head:

   ```bash
   docker compose run --rm migrate
   docker compose exec backend alembic current
   ```

6. Never use `alembic stamp` to skip a schema change unless the database has
   been manually verified and the decision is documented.

## Running migrations

### Compose

`docker-compose.yml` has a one-shot `migrate` service:

- it uses the backend image and runs `alembic -c /app/alembic.ini upgrade head`;
- it exits with code 0 on success and `restart: "no"`;
- `backend` starts only after `migrate` completes successfully
  (`service_completed_successfully`);
- the backend container itself runs only Uvicorn and never invokes Alembic;
- `bot` and `frontend` wait for a healthy backend.

To apply pending migrations without starting the rest of the stack:

```bash
docker compose run --rm migrate
```

Compose reads the deployment's `COMPOSE_PROJECT_NAME` from `.env`, so the same
command selects the same project, images, volumes and database as the rest of
the stack. `-p` alone does not rename images; do not mix a `-p` value with a
different `COMPOSE_PROJECT_NAME`. The `migrate` service reuses the backend image
(`<COMPOSE_PROJECT_NAME>-backend`), so a migration can never run against a
different application build than the backend it precedes. The full deployment
ordering is in `docs/DEPLOYMENT.md`.

### Local development

```bash
cd backend
alembic current
alembic upgrade head
alembic heads
```

`alembic upgrade head` is idempotent: a database already at the head performs no
schema work.

## Concurrency and multiple backend instances

Alembic takes a PostgreSQL session advisory lock
(`app/core/migration_lock.py`, ID `0x4855424D494752`) in `alembic/env.py`.
Migration jobs serialize; the lock is released on success, on failure, and
automatically when the connection closes. This makes multiple migration
containers safe. The lock is a no-op on SQLite.

The advisory lock and the one-shot `migrate` service are complementary:

- the service prevents every backend replica from running migrations;
- the advisory lock protects against manually started concurrent migration
  commands and multiple Compose projects.

## Rollback policy

- `alembic downgrade base` is forbidden.
- `0003` and `0004` are forward-only and raise on downgrade.
- Destructive changes (dropping tables/columns, data rewrites) require explicit
  approval and a verified backup.
- Rollback is a **restore from backup** into a new database and a manual
  switch, not a schema downgrade. See `docs/BACKUP_RESTORE.md`.

## Failed or interrupted migration

Alembic runs the revision inside a transaction. A failure rolls back the DDL and
leaves `alembic_version` at the previous revision; the advisory lock is released.
The `migrate` service exits non-zero, so Compose does not start the backend.

Do not "fix" a failed migration by editing it. Inspect logs, fix forward with a
new revision, or restore a verified backup.

## Verification

Before deploying:

- `alembic heads` shows exactly one head;
- `alembic current` matches that head;
- `backend/tests/test_migrations.py` passes on PostgreSQL 16;
- `backend/tests/test_migration_governance.py` passes (frozen files, single head,
  one-shot service, advisory lock);
- a fresh database and a legacy database both reach the head;
- `docs/BACKUP_RESTORE.md` has a current, verified backup;
- the deployment follows the sequence in `docs/DEPLOYMENT.md`.

PostgreSQL 16 is the supported compose version; CI should run migration tests on
16 rather than relying only on SQLite.
