#!/usr/bin/env bash
# Create a verified custom-format PostgreSQL backup.
#
# Connection parameters come from the standard libpq environment:
# PGHOST, PGPORT, PGUSER, PGDATABASE, and PGPASSWORD or PGPASSFILE.
# No password is ever placed on the command line or printed.
set -euo pipefail

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    echo "Usage: PGDATABASE=hub [BACKUP_DIR=./backups] $0"
    exit 0
fi

: "${PGDATABASE:?Set PGDATABASE to the database that should be dumped}"

case "$PGDATABASE" in
    *[!A-Za-z0-9_]*) echo "PGDATABASE contains characters unsafe for a filename" >&2; exit 1 ;;
esac

for tool in pg_dump pg_restore; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool is not in PATH" >&2; exit 1; }
done

backup_dir="${BACKUP_DIR:-./backups}"
mkdir -p -- "$backup_dir"
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$backup_dir/hub-${PGDATABASE}-${stamp}-$$.dump"

if [ -e "$target" ]; then
    echo "Refusing to overwrite existing backup: $target" >&2
    exit 1
fi

created=0
cleanup() {
    if [ "$created" -eq 1 ]; then
        rm -f -- "$target"
    fi
}
trap cleanup ERR

created=1
pg_dump --format=custom --no-owner --no-privileges --dbname="$PGDATABASE" --file="$target"

if [ ! -s "$target" ]; then
    echo "Backup is empty: $target" >&2
    exit 1
fi

if ! pg_restore --list "$target" >/dev/null; then
    echo "Backup is not readable by pg_restore: $target" >&2
    exit 1
fi

created=0
trap - ERR
echo "$target"
