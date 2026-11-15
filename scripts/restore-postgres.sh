#!/usr/bin/env bash
# Restore a custom-format backup into a NEW database.
#
# This script never drops, truncates or renames a database. It refuses to
# touch existing targets and refuses production-looking names by default.
# Switching the application to the restored database is a separate, manual
# operation (see docs/BACKUP_RESTORE.md).
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: RESTORE_CONFIRM=<new_db> [EXPECTED_ALEMBIC_HEAD=<rev>] \
       [PGMAINTENANCE_DB=postgres] \
       scripts/restore-postgres.sh <backup.dump> <new_db>

The target database must not exist. Connection parameters use libpq PG* env.
Set ALLOW_PRODUCTION_RESTORE=YES only after an explicit change approval.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
    exit 0
fi

if [ "$#" -ne 2 ]; then
    usage
    exit 2
fi

backup="$1"
target="$2"

case "$target" in
    *[!A-Za-z0-9_]*) echo "Refusing unsafe target database name" >&2; exit 1 ;;
esac
case "$target" in
    hub|prod|production)
        if [ "${ALLOW_PRODUCTION_RESTORE:-}" != "YES" ]; then
            echo "Refusing production-like target '$target' without ALLOW_PRODUCTION_RESTORE=YES" >&2
            exit 1
        fi
        ;;
esac

if [ ! -f "$backup" ] || [ ! -r "$backup" ]; then
    echo "Backup file is missing or unreadable: $backup" >&2
    exit 1
fi

if [ "${RESTORE_CONFIRM:-}" != "$target" ]; then
    echo "Set RESTORE_CONFIRM=$target to confirm the destination explicitly" >&2
    exit 1
fi

for tool in psql createdb pg_restore; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool is not in PATH" >&2; exit 1; }
done

if ! pg_restore --list "$backup" >/dev/null; then
    echo "Backup is not readable by pg_restore: $backup" >&2
    exit 1
fi

maintenance="${PGMAINTENANCE_DB:-postgres}"
case "$maintenance" in
    *[!A-Za-z0-9_]*) echo "PGMAINTENANCE_DB contains unsafe characters" >&2; exit 1 ;;
esac

exists="$(psql --dbname="$maintenance" --tuples-only --no-align \
    --command="SELECT 1 FROM pg_database WHERE datname = '$target'" | tr -d '\r' | tr -d '[:space:]')"
if [ -n "$exists" ]; then
    echo "Target database already exists; refusing to modify it: $target" >&2
    exit 1
fi

createdb --encoding=UTF8 --template=template0 "$target"

if ! pg_restore --exit-on-error --no-owner --no-privileges --dbname="$target" "$backup"; then
    echo "Restore failed. Target '$target' was left for inspection; no database was dropped." >&2
    exit 1
fi

tables="$(psql --dbname="$target" --tuples-only --no-align \
    --command="SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','notes','tasks','focus_sessions','meal_entries','saved_recipes','community_foods','alembic_version')" \
    | tr -d '\r' | tr -d '[:space:]')"
if [ "$tables" != "8" ]; then
    echo "Restore verification failed: expected 8 core tables, found $tables" >&2
    exit 1
fi

indexes="$(psql --dbname="$target" --tuples-only --no-align \
    --command="SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname IN ('ix_users_telegram_id','ix_notes_user_updated','ix_tasks_user_completed','ix_focus_user_finished','uq_focus_sessions_user_client','ix_meals_user_day','ix_recipes_user_name','ix_community_foods_barcode')" \
    | tr -d '\r' | tr -d '[:space:]')"
if [ "$indexes" != "8" ]; then
    echo "Restore verification failed: expected 8 core indexes, found $indexes" >&2
    exit 1
fi

head="$(psql --dbname="$target" --tuples-only --no-align \
    --command="SELECT version_num FROM alembic_version" | tr -d '\r')"
head_lines="$(printf '%s\n' "$head" | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')"
head="$(printf '%s\n' "$head" | sed '/^[[:space:]]*$/d')"
if [ "$head_lines" != "1" ] || [ -z "$head" ]; then
    echo "Restore verification failed: expected exactly one Alembic head" >&2
    exit 1
fi
if [ -n "${EXPECTED_ALEMBIC_HEAD:-}" ] && [ "$head" != "$EXPECTED_ALEMBIC_HEAD" ]; then
    echo "Restore verification failed: Alembic head is $head, expected $EXPECTED_ALEMBIC_HEAD" >&2
    exit 1
fi

echo "Restored into '$target' (Alembic head: $head)."
echo "Source database was not modified. Verify the data, then perform the documented manual switch."
