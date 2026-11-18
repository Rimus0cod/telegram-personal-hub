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
Supported Alembic revisions: 0002_add_last_city, 0003_nutrition_schema,
0004_focus_session_client_id, 0005_catalog_foods, 0006_global_catalog,
0007_catalog_cleanup, 0008_multilingual_search, 0009_recipe_composition.
The verification is schema-aware and never runs migrations.
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

head_rows=""
if ! head_rows="$(psql --dbname="$target" --tuples-only --no-align \
    --command="SELECT version_num FROM alembic_version")"; then
    echo "Restore verification failed: alembic_version is missing or unreadable" >&2
    exit 1
fi
head_rows="$(printf '%s\n' "$head_rows" | tr -d '\r' | sed '/^[[:space:]]*$/d')"
head_count="$(printf '%s\n' "$head_rows" | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')"
if [ "$head_count" -eq 0 ]; then
    echo "Restore verification failed: Alembic revision is missing from alembic_version" >&2
    exit 1
fi
if [ "$head_count" -ne 1 ]; then
    echo "Restore verification failed: expected exactly one Alembic revision, found $head_count" >&2
    exit 1
fi
head="$head_rows"
if [ -n "${EXPECTED_ALEMBIC_HEAD:-}" ] && [ "$head" != "$EXPECTED_ALEMBIC_HEAD" ]; then
    echo "Restore verification failed: Alembic head is $head, expected $EXPECTED_ALEMBIC_HEAD" >&2
    exit 1
fi

# Every supported revision has an explicit expected schema. 0002 is the
# pre-Nutrition baseline; 0003 reconciles Nutrition; 0004 adds the Focus
# idempotency index. Extra tables/indexes are allowed, so a database created by
# an older create_all() with a richer schema still verifies at 0002.
case "$head" in
    0002_add_last_city)
        required_tables="users notes tasks focus_sessions alembic_version"
        required_indexes="users:ix_users_telegram_id notes:ix_notes_user_updated tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished"
        ;;
    0003_nutrition_schema)
        required_tables="users notes tasks focus_sessions meal_entries saved_recipes community_foods alembic_version"
        required_indexes="users:ix_users_telegram_id notes:ix_notes_user_updated tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished meal_entries:ix_meals_user_day saved_recipes:ix_recipes_user_name community_foods:ix_community_foods_barcode"
        ;;
    0004_focus_session_client_id)
        required_tables="users notes tasks focus_sessions meal_entries saved_recipes community_foods alembic_version"
        required_indexes="users:ix_users_telegram_id notes:ix_notes_user_updated tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished meal_entries:ix_meals_user_day saved_recipes:ix_recipes_user_name community_foods:ix_community_foods_barcode focus_sessions:uq_focus_sessions_user_client"
        ;;
    0005_catalog_foods)
        required_tables="users notes tasks focus_sessions meal_entries saved_recipes community_foods catalog_foods alembic_version"
        required_indexes="users:ix_users_telegram_id notes:ix_notes_user_updated tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished meal_entries:ix_meals_user_day saved_recipes:ix_recipes_user_name community_foods:ix_community_foods_barcode focus_sessions:uq_focus_sessions_user_client catalog_foods:uq_catalog_foods_source_source_id"
        ;;
    0006_global_catalog)
        required_tables="users notes tasks focus_sessions meal_entries saved_recipes community_foods catalog_foods food_sources food_translations food_aliases nutrient_profiles food_servings food_barcodes recipe_ingredients alembic_version"
        required_indexes="users:ix_users_telegram_id notes:ix_notes_user_updated tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished meal_entries:ix_meals_user_day saved_recipes:ix_recipes_user_name community_foods:ix_community_foods_barcode focus_sessions:uq_focus_sessions_user_client catalog_foods:uq_catalog_foods_source_source_id food_translations:uq_food_translations_food_language nutrient_profiles:ix_nutrient_profiles_primary food_barcodes:uq_food_barcodes_barcode"
        ;;
    0007_catalog_cleanup)
        required_tables="users notes tasks focus_sessions meal_entries saved_recipes community_foods catalog_foods food_sources food_translations food_aliases nutrient_profiles food_servings food_barcodes recipe_ingredients alembic_version"
        required_indexes="users:ix_users_telegram_id notes:ix_notes_user_updated tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished meal_entries:ix_meals_user_day saved_recipes:ix_recipes_user_name community_foods:ix_community_foods_barcode focus_sessions:uq_focus_sessions_user_client catalog_foods:uq_catalog_foods_source_source_id food_translations:uq_food_translations_food_language nutrient_profiles:ix_nutrient_profiles_primary food_barcodes:uq_food_barcodes_barcode"
        ;;
    0008_multilingual_search)
        required_tables="users notes tasks focus_sessions meal_entries saved_recipes community_foods catalog_foods food_sources food_translations food_aliases nutrient_profiles food_servings food_barcodes recipe_ingredients food_search_terms alembic_version"
        required_indexes="users:ix_users_telegram_id notes:ix_notes_user_updated tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished meal_entries:ix_meals_user_day saved_recipes:ix_recipes_user_name community_foods:ix_community_foods_barcode community_foods:ix_community_foods_normalized focus_sessions:uq_focus_sessions_user_client catalog_foods:uq_catalog_foods_source_source_id food_translations:uq_food_translations_food_language nutrient_profiles:ix_nutrient_profiles_primary food_barcodes:uq_food_barcodes_barcode food_search_terms:uq_food_search_terms food_search_terms:ix_food_search_terms_normalized"
        ;;
    0009_recipe_composition)
        required_tables="users notes tasks focus_sessions meal_entries saved_recipes community_foods catalog_foods food_sources food_translations food_aliases nutrient_profiles food_servings food_barcodes recipe_ingredients recipe_details food_search_terms alembic_version"
        required_indexes="users:ix_users_telegram_id notes:ix_notes_user_updated tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished meal_entries:ix_meals_user_day saved_recipes:ix_recipes_user_name community_foods:ix_community_foods_barcode community_foods:ix_community_foods_normalized focus_sessions:uq_focus_sessions_user_client catalog_foods:uq_catalog_foods_source_source_id food_translations:uq_food_translations_food_language nutrient_profiles:ix_nutrient_profiles_primary food_barcodes:uq_food_barcodes_barcode food_search_terms:uq_food_search_terms food_search_terms:ix_food_search_terms_normalized"
        ;;
    *)
        echo "Restore verification failed: unsupported Alembic revision '$head' (supported: 0002_add_last_city, 0003_nutrition_schema, 0004_focus_session_client_id, 0005_catalog_foods, 0006_global_catalog, 0007_catalog_cleanup, 0008_multilingual_search, 0009_recipe_composition)" >&2
        exit 1
        ;;
esac

missing_tables=""
for table in $required_tables; do
    present="$(psql --dbname="$target" --tuples-only --no-align \
        --command="SELECT to_regclass('public.$table') IS NOT NULL" \
        | tr -d '\r' | tr -d '[:space:]')"
    if [ "$present" != "t" ]; then
        missing_tables="$missing_tables $table"
    fi
done
if [ -n "$missing_tables" ]; then
    echo "Restore verification failed for $head: missing required table(s):$missing_tables" >&2
    exit 1
fi

missing_indexes=""
for pair in $required_indexes; do
    table="${pair%%:*}"
    index="${pair#*:}"
    present="$(psql --dbname="$target" --tuples-only --no-align \
        --command="SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='$table' AND indexname='$index'" \
        | tr -d '\r' | tr -d '[:space:]')"
    if [ "$present" != "1" ]; then
        missing_indexes="$missing_indexes $table:$index"
    fi
done
if [ -n "$missing_indexes" ]; then
    echo "Restore verification failed for $head: missing required index(es):$missing_indexes" >&2
    exit 1
fi

echo "Restored into '$target' (Alembic head: $head)."
echo "Source database was not modified. Verify the data, then perform the documented manual switch."
