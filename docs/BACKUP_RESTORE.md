# Backup and restore runbook

> **Warning:** restoring a backup returns the database to the moment of that
> backup. Every change made after it is lost unless it is captured separately.
> Never restore over the live database without an explicit incident decision.

## Prerequisites

- PostgreSQL 16 client tools: `pg_dump`, `pg_restore`, `psql`, `createdb`.
- Connection parameters through standard libpq variables:
  `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE`, and either `PGPASSWORD`
  (prefer a protected environment) or `PGPASSFILE`.
- The scripts never accept a password argument and never print `.env` or
  `DATABASE_URL`.
- On Windows, run the bash scripts through Git Bash/Cygwin and put the
  PostgreSQL 16 `bin` directory on `PATH`.
- If the host has no PostgreSQL 16 client tools, run the unmodified scripts in a
  temporary `postgres:16-alpine` client container attached to the Compose
  network, mounting `scripts/` read-only and the backup directory read/write for
  that container only. This was verified during the Docker runtime audit. It is
  a client-container workflow, not a replacement script.

For the deployment sequence that this runbook supports (audit, backup, verify,
build, migrate, switch, health checks, rollback), see `docs/DEPLOYMENT.md`. The
backup is step 2 and verification is step 3; do not build or migrate before the
verified backup exists.

The scripts are in `scripts/` and are invoked with `bash` so they also work
when the executable bit is not set:

- `bash scripts/backup-postgres.sh`
- `bash scripts/restore-postgres.sh`

## 1. Create a backup

```bash
export PGHOST=127.0.0.1
export PGPORT=5432
export PGUSER=hub
export PGDATABASE=hub
export PGPASSFILE=/secure/path/.pgpass      # or use PGPASSWORD
export BACKUP_DIR=/srv/backups/telegram-hub

bash scripts/backup-postgres.sh
```

The script:

- uses custom format (`pg_dump -Fc`);
- writes a unique timestamped file and refuses to overwrite;
- checks the exit code, non-empty output and `pg_restore --list`;
- removes a partial/invalid dump it created;
- prints only the backup path.

Recommended retention: keep daily backups for at least 14 days and a verified
weekly backup off-host, encrypted at rest. A backup that has never been restored
is not verified.

## 2. Verify the backup

```bash
pg_restore --list /srv/backups/telegram-hub/hub-hub-<timestamp>-<pid>.dump >/dev/null
sha256sum /srv/backups/telegram-hub/hub-hub-<timestamp>-<pid>.dump
```

Record the checksum off-host. Test restores periodically.

## 3. Restore into a new temporary database

Never restore into the live database. Choose a new target name and confirm it
explicitly:

```bash
export RESTORE_CONFIRM=hub_restore_20261115
export EXPECTED_ALEMBIC_HEAD=0004_focus_session_client_id
export PGMAINTENANCE_DB=postgres

bash scripts/restore-postgres.sh \
  /srv/backups/telegram-hub/hub-hub-<timestamp>-<pid>.dump \
  hub_restore_20261115
```

The script:

- refuses an existing target database;
- refuses production-like names (`hub`, `prod`, `production`) unless
  `ALLOW_PRODUCTION_RESTORE=YES` is set after an approved incident decision;
- validates the archive before creating anything;
- creates a **new** database with `createdb`;
- restores with `pg_restore --exit-on-error --no-owner --no-privileges`;
- verifies core tables, core indexes and exactly one Alembic head;
- never drops or renames a database.

If verification fails, the new database is left for inspection. No source
database is modified.

## 4. Verify data in the temporary database

```bash
psql --dbname=hub_restore_20261115 -c "SELECT version_num FROM alembic_version;"
psql --dbname=hub_restore_20261115 -c "SELECT count(*) FROM users;"
psql --dbname=hub_restore_20261115 -c "SELECT count(*) FROM notes;"
psql --dbname=hub_restore_20261115 -c "SELECT count(*) FROM meal_entries;"
```

Compare key counts/records with the incident expectations. Verify that the
Alembic head is the version the application expects.

## 5. Manual switch (separate, approved step)

The application database name is fixed in Compose (`hub`). The safe switch is
manual and must be done during a maintenance window. Run Compose from the
deployment directory and keep `COMPOSE_PROJECT_NAME` explicitly set in `.env`
(recommended) so the same project, images and volume are selected as the running
stack; if it is unset, Compose derives a name from the directory. See
`docs/DEPLOYMENT.md`.

1. Stop writers:

   ```bash
   docker compose stop backend bot frontend
   ```

2. Take a final backup of the current live database (even if it is damaged).
3. Confirm the restored database is healthy and has the expected head.
4. Terminate remaining connections and rename databases:

   ```bash
   psql --dbname=postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('hub','hub_restore_20261115') AND pid <> pg_backend_pid();"
   psql --dbname=postgres -c 'ALTER DATABASE "hub" RENAME TO "hub_before_restore_20261115";'
   psql --dbname=postgres -c 'ALTER DATABASE "hub_restore_20261115" RENAME TO "hub";'
   ```

5. Start the stack:

   ```bash
   docker compose up -d
   ```

   The one-shot `migrate` service runs before the backend. Confirm:
   `docker compose ps`, backend `/health`, and
   `docker compose exec backend alembic current`.

6. Keep `hub_before_restore_20261115` until the restored data is verified and
   the incident is closed.

## 6. Rollback to the previous database

If the restored database is wrong:

1. Stop backend/bot/frontend.
2. Rename the restored database away and rename the preserved previous database
   back to `hub`.
3. Start the stack and verify `/health` and the Alembic head.

Do not delete the preserved database until the incident is resolved.

## 7. Data created after the backup

The backup is a point-in-time copy. Any rows created after it are only in the
current database or in PostgreSQL WAL/archives. This project does not currently
implement point-in-time recovery. If those rows are needed, export them before
the switch or restore them from a separate export; do not assume the backup
contains them.

## 8. Failure and interruption

- The restore script never runs `dropdb` or `DROP DATABASE`.
- A failed restore leaves the new target for inspection; clean it up manually
  only after collecting evidence.
- The source database is never modified.
- If the switch is interrupted, stop services, inspect database names, and
  follow the rollback section; do not run destructive SQL blindly.
