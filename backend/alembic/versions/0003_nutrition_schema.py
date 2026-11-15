"""Reconcile the Nutrition schema for every existing installation.

``0001_initial`` builds the schema from the *current* SQLAlchemy metadata, and
older releases also patched the schema from ``app.main`` at import time.  As a
result a database stamped ``0002_add_last_city`` can be in one of several
states:

* a pre-Nutrition database without ``meal_entries``/``saved_recipes``;
* the ``f234e21`` Nutrition schema with ``meal_entries`` and ``saved_recipes``
  but without nutrition targets, base nutrition columns or ``community_foods``;
* the current schema, already created by ``create_all``/startup ``ALTER TABLE``
  but still stamped ``0002``.

This revision reconciles all of those states without dropping or recreating
user tables.  Every operation inspects the live database first, so applying it
to an already updated database is a no-op for the schema.

Before any DDL runs, ``_preflight`` verifies that the database is a state the
application could actually have produced:

* ``users`` must be present with its legacy columns;
* ``meal_entries`` may only contain a *prefix* of the historical base-column
  add order, and every already existing base column must still hold its
  ``ADD COLUMN`` default.  The application cannot write a partial base tuple
  (SQLAlchemy always inserts every mapped column), so any other partial state
  must come from manual/external changes and is refused instead of being
  silently overwritten;
* every index this revision expects to create must either not exist, or exist
  on the target table with exactly the expected columns/uniqueness.  Index
  names are global in SQLite and schema-global in PostgreSQL, so a name owned
  by another table stops the migration.

A refusal leaves the database untouched: no DDL has run yet and
``alembic_version`` keeps pointing at the previous revision.
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_nutrition_schema"
down_revision = "0002_add_last_city"
branch_labels = None
depends_on = None


# Columns that existed in the ``f234e21`` Nutrition schema.  They must already
# be present before the reconcilliation below; if they are not, the database is
# not a recognisable Personal Hub database and we stop instead of guessing.
_MEAL_LEGACY_COLUMNS = {
    "id", "user_id", "day", "meal_type", "name", "calories", "protein",
    "carbs", "fat", "ingredients", "created_at",
}
_RECIPE_LEGACY_COLUMNS = {
    "id", "user_id", "name", "meal_type", "ingredients", "created_at",
}
_COMMUNITY_COLUMNS = {
    "id", "barcode", "barcode_prefix", "name", "brand", "kcal", "protein",
    "carbs", "fat", "source", "submitted_by", "created_at",
}

# Legacy ``users`` columns. Older releases created them through
# ``Base.metadata.create_all``; a database without them cannot serve the
# application, so reconciling its Nutrition tables would only hide the breakage.
_USERS_REQUIRED_COLUMNS = {"telegram_id", "created_at", "last_city"}

# The exact order in which the old import-time DDL added the Nutrition base
# columns, together with the default each ``ALTER TABLE ... ADD COLUMN`` wrote.
# A partial state is only trustworthy if it is a prefix of this tuple and the
# existing columns still hold these defaults.
_BASE_COLUMNS_IN_HISTORICAL_ORDER = (
    ("base_serving_grams", 100),
    ("base_calories", 0),
    ("base_protein", 0),
    ("base_carbs", 0),
    ("base_fat", 0),
)

# Every index this revision must guarantee, per table, in (name, columns,
# unique) form. ``_ensure_indexes`` creates the missing ones and ``_preflight``
# verifies the existing ones before any DDL.
_EXPECTED_INDEXES = {
    "meal_entries": (
        ("ix_meal_entries_day", ("day",), False),
        ("ix_meal_entries_user_id", ("user_id",), False),
        ("ix_meals_user_day", ("user_id", "day"), False),
    ),
    "saved_recipes": (
        ("ix_saved_recipes_user_id", ("user_id",), False),
        ("ix_recipes_user_name", ("user_id", "name"), False),
    ),
    "community_foods": (
        ("ix_community_foods_barcode", ("barcode",), True),
        ("ix_community_foods_barcode_prefix", ("barcode_prefix",), False),
        ("ix_community_foods_prefix", ("barcode_prefix",), False),
        ("ix_community_foods_submitted_by", ("submitted_by",), False),
    ),
}


def _inspector():
    return sa.inspect(op.get_bind())


def _table_names():
    return set(_inspector().get_table_names())


def _columns(table):
    return {column["name"] for column in _inspector().get_columns(table)}


def _index_names(table):
    return {index["name"] for index in _inspector().get_indexes(table)}


def _add_column_if_missing(table, column):
    if column.name not in _columns(table):
        op.add_column(table, column)


def _create_index_if_missing(table, name, columns, unique=False):
    if name not in _index_names(table):
        op.create_index(name, table, columns, unique=unique)


def _index_details(table):
    """Return ``{index name: (columns, unique)}`` for one table."""
    return {
        index["name"]: (tuple(index.get("column_names") or ()),
                        bool(index.get("unique")))
        for index in _inspector().get_indexes(table)
    }


def _index_owners(name):
    """Return the tables that own an index called ``name``.

    SQLite stores index names in a single database-wide namespace, while
    PostgreSQL scopes them to a schema. Both are checked with the dialect's own
    catalog so a collision is detected before ``CREATE INDEX`` fails halfway
    through the migration.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        rows = bind.execute(sa.text(
            "SELECT tbl_name FROM sqlite_master "
            "WHERE type = 'index' AND name = :name"
        ), {"name": name}).fetchall()
    elif dialect in ("postgresql", "postgres"):
        rows = bind.execute(sa.text(
            "SELECT tablename FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = :name"
        ), {"name": name}).fetchall()
    else:
        # Conservative fallback for unknown dialects: inspect every table.
        rows = [(table,) for table in _table_names()
                if name in _index_names(table)]
    return {row[0] for row in rows}


def _check_partial_base_columns():
    """Refuse partial base-column states that the application cannot produce."""
    existing = _columns("meal_entries")
    present = [name for name, _ in _BASE_COLUMNS_IN_HISTORICAL_ORDER
               if name in existing]
    if len(present) == len(_BASE_COLUMNS_IN_HISTORICAL_ORDER):
        return

    missing = [name for name, _ in _BASE_COLUMNS_IN_HISTORICAL_ORDER
               if name not in existing]
    columns = {column["name"]: column
               for column in _inspector().get_columns("meal_entries")}

    # Check the values of the columns that do exist first: they are the most
    # concrete evidence that the partial state is not the one the interrupted
    # startup DDL leaves behind.
    for name, default in _BASE_COLUMNS_IN_HISTORICAL_ORDER:
        if name not in existing:
            continue
        if columns[name]["nullable"]:
            raise RuntimeError(
                "Cannot reconcile meal_entries: the partial base column "
                f"{name} is nullable, but the historical startup DDL created "
                "it as NOT NULL. A nullable column cannot come from that DDL, "
                "so its values are not trustworthy. Resolve the column "
                "manually or restore a backup."
            )
        # The identifier comes from the fixed tuple above, never from input.
        count, lowest, highest = op.get_bind().execute(sa.text(
            f"SELECT COUNT(*), MIN({name}), MAX({name}) FROM meal_entries "
            f"WHERE {name} IS NULL OR {name} <> :default"
        ), {"default": default}).one()
        if count:
            raise RuntimeError(
                "Cannot reconcile meal_entries: the base Nutrition columns "
                f"are only partially present ({', '.join(present)}; missing "
                f"{', '.join(missing)}), but the existing column {name} "
                f"already holds non-default values in {count} row(s) "
                f"(range {lowest}..{highest}, expected default {default}). "
                "This state cannot be produced by the interrupted startup "
                "DDL, so those base values may be real. The migration refuses "
                "to overwrite them; resolve them manually or restore a backup."
            )

    expected_prefix = [name for name, _ in
                       _BASE_COLUMNS_IN_HISTORICAL_ORDER[:len(present)]]
    if present != expected_prefix:
        raise RuntimeError(
            "Cannot reconcile meal_entries: the existing base Nutrition "
            f"columns ({', '.join(present) or 'none'}) are not a prefix of the "
            "historical add-column order "
            f"({', '.join(name for name, _ in _BASE_COLUMNS_IN_HISTORICAL_ORDER)}). "
            f"Missing columns: {', '.join(missing)}. The application never "
            "creates a partial base tuple, so this schema was changed "
            "externally. Resolve it manually or restore a backup; the "
            "migration will not overwrite these values."
        )


def _check_index_namespace(tables):
    """Verify index names and structures before any DDL."""
    for table, expected in _EXPECTED_INDEXES.items():
        details = _index_details(table) if table in tables else {}
        for name, columns, unique in expected:
            owners = _index_owners(name)
            foreign = owners - {table}
            if foreign:
                raise RuntimeError(
                    f"Cannot create the index {name} for {table}: that name is "
                    f"already used by another table ({', '.join(sorted(foreign))}). "
                    "Index names must be unique in SQLite and within a "
                    "PostgreSQL schema. Remove or rename the conflicting index "
                    "manually; the migration will not touch unknown indexes."
                )
            if name in details:
                actual_columns, actual_unique = details[name]
                if actual_columns != tuple(columns) or actual_unique != bool(unique):
                    raise RuntimeError(
                        f"Cannot reuse the existing index {name} on {table}: it "
                        f"has columns ({', '.join(actual_columns) or 'unknown'}) "
                        f"and unique={actual_unique}, but the schema expects "
                        f"({', '.join(columns)}) and unique={bool(unique)}. "
                        "Resolve the index manually; the migration will not "
                        "delete or rebuild unknown indexes."
                    )


def _preflight():
    """Fail before any DDL if the database cannot be safely reconciled."""
    tables = _table_names()
    if "users" not in tables:
        raise RuntimeError(
            "Cannot reconcile the Nutrition schema: the users table is "
            "missing. Restore the database from backup or run a clean "
            "`alembic upgrade head` on a new database."
        )

    missing_users = _USERS_REQUIRED_COLUMNS - _columns("users")
    if missing_users:
        raise RuntimeError(
            "Cannot reconcile the users table: legacy columns are missing "
            f"({', '.join(sorted(missing_users))}). The application cannot run "
            "against this schema. Restore the database from backup or run a "
            "clean `alembic upgrade head` on a new database."
        )

    if "meal_entries" in tables:
        missing = _MEAL_LEGACY_COLUMNS - _columns("meal_entries")
        if missing:
            raise RuntimeError(
                "Cannot reconcile meal_entries: expected legacy columns are "
                f"missing ({', '.join(sorted(missing))})."
            )
        _check_partial_base_columns()

    if "saved_recipes" in tables:
        missing = _RECIPE_LEGACY_COLUMNS - _columns("saved_recipes")
        if missing:
            raise RuntimeError(
                "Cannot reconcile saved_recipes: expected legacy columns are "
                f"missing ({', '.join(sorted(missing))})."
            )

    if "community_foods" in tables:
        missing = _COMMUNITY_COLUMNS - _columns("community_foods")
        if missing:
            raise RuntimeError(
                "Cannot reconcile community_foods: columns are missing "
                f"({', '.join(sorted(missing))}). A partially created shared "
                "catalog cannot be repaired automatically without risking "
                "existing rows."
            )
        if "ix_community_foods_barcode" not in _index_names("community_foods"):
            duplicate = op.get_bind().execute(sa.text(
                "SELECT barcode FROM community_foods "
                "GROUP BY barcode HAVING COUNT(*) > 1 LIMIT 1"
            )).first()
            if duplicate is not None:
                raise RuntimeError(
                    "Cannot add the unique community_foods barcode index: "
                    "duplicate barcodes already exist. Resolve them manually "
                    "before running this migration."
                )

    _check_index_namespace(tables)


def _ensure_users():
    # Python-side defaults are not server defaults, so databases upgraded from
    # an older release need explicit defaults for existing rows.
    _add_column_if_missing("users", sa.Column(
        "calorie_target", sa.Integer(), nullable=False, server_default=sa.text("2000")))
    _add_column_if_missing("users", sa.Column(
        "protein_target", sa.Integer(), nullable=False, server_default=sa.text("120")))
    _add_column_if_missing("users", sa.Column(
        "carbs_target", sa.Integer(), nullable=False, server_default=sa.text("250")))
    _add_column_if_missing("users", sa.Column(
        "fat_target", sa.Integer(), nullable=False, server_default=sa.text("70")))

    # Only relevant for a database that was left partially patched with
    # nullable columns; created columns are already NOT NULL with defaults.
    op.execute(sa.text(
        "UPDATE users SET calorie_target = 2000 WHERE calorie_target IS NULL"))
    op.execute(sa.text(
        "UPDATE users SET protein_target = 120 WHERE protein_target IS NULL"))
    op.execute(sa.text(
        "UPDATE users SET carbs_target = 250 WHERE carbs_target IS NULL"))
    op.execute(sa.text(
        "UPDATE users SET fat_target = 70 WHERE fat_target IS NULL"))


def _create_meal_entries():
    if "meal_entries" in _table_names():
        return
    op.create_table(
        "meal_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("meal_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("calories", sa.Integer(), nullable=False),
        sa.Column("protein", sa.Double(), nullable=False),
        sa.Column("carbs", sa.Double(), nullable=False),
        sa.Column("fat", sa.Double(), nullable=False),
        sa.Column("ingredients", sa.Text(), nullable=False),
        sa.Column("serving_grams", sa.Integer(), nullable=False,
                  server_default=sa.text("100")),
        sa.Column("base_serving_grams", sa.Integer(), nullable=False,
                  server_default=sa.text("100")),
        sa.Column("base_calories", sa.Double(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("base_protein", sa.Double(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("base_carbs", sa.Double(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("base_fat", sa.Double(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def _create_saved_recipes():
    if "saved_recipes" in _table_names():
        return
    op.create_table(
        "saved_recipes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("meal_type", sa.String(length=16), nullable=False),
        sa.Column("ingredients", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def _create_community_foods():
    if "community_foods" in _table_names():
        return
    op.create_table(
        "community_foods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("barcode", sa.String(length=14), nullable=False),
        sa.Column("barcode_prefix", sa.String(length=7), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=False),
        sa.Column("kcal", sa.Double(), nullable=False),
        sa.Column("protein", sa.Double(), nullable=False),
        sa.Column("carbs", sa.Double(), nullable=False),
        sa.Column("fat", sa.Double(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("submitted_by", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def _ensure_meal_columns():
    """Add missing Nutrition columns and report whether bases were incomplete.

    If any base column was missing, ``_preflight`` has already proven that the
    existing base columns only contain ``ADD COLUMN`` defaults, so the base
    tuple was never usable and may be rebuilt from the current values. If every
    base column already existed, only genuinely uninitialised rows are repaired.
    """
    _add_column_if_missing("meal_entries", sa.Column(
        "serving_grams", sa.Integer(), nullable=False, server_default=sa.text("100")))

    base_columns = (
        ("base_serving_grams", sa.Integer(), "100"),
        ("base_calories", sa.Double(), "0"),
        ("base_protein", sa.Double(), "0"),
        ("base_carbs", sa.Double(), "0"),
        ("base_fat", sa.Double(), "0"),
    )
    existing = _columns("meal_entries")
    bases_incomplete = any(column not in existing for column, _, _ in base_columns)
    for name, column_type, default in base_columns:
        _add_column_if_missing("meal_entries", sa.Column(
            name, column_type, nullable=False, server_default=sa.text(default)))
    return bases_incomplete


def _backfill_meal_bases(reconstruct_all):
    # A missing serving size can only come from a partially patched database.
    op.execute(sa.text(
        "UPDATE meal_entries SET serving_grams = 100 "
        "WHERE serving_grams IS NULL OR serving_grams < 1"))

    if reconstruct_all:
        # Preflight verified that the partial base tuple only holds ADD COLUMN
        # defaults, so the application could never have used it to resize a
        # meal. Rebuild the complete tuple from the current, still
        # authoritative, meal values.
        op.execute(sa.text(
            "UPDATE meal_entries SET "
            "base_serving_grams = serving_grams, "
            "base_calories = COALESCE(calories, 0), "
            "base_protein = COALESCE(protein, 0), "
            "base_carbs = COALESCE(carbs, 0), "
            "base_fat = COALESCE(fat, 0)"))
        return

    # The old startup backfill only handled rows with calories > 0. That left
    # meals with zero calories but non-zero macros (and therefore zero base
    # macros) behind, so resizing such a meal erased its nutrition. An
    # all-zero base tuple is the unambiguous "not initialised" marker, while
    # rows with a partially valid base tuple are left untouched.
    op.execute(sa.text(
        "UPDATE meal_entries SET "
        "base_serving_grams = serving_grams, "
        "base_calories = COALESCE(calories, 0), "
        "base_protein = COALESCE(protein, 0), "
        "base_carbs = COALESCE(carbs, 0), "
        "base_fat = COALESCE(fat, 0) "
        "WHERE COALESCE(base_calories, 0) = 0 "
        "AND COALESCE(base_protein, 0) = 0 "
        "AND COALESCE(base_carbs, 0) = 0 "
        "AND COALESCE(base_fat, 0) = 0 "
        "AND (COALESCE(calories, 0) <> 0 "
        "OR COALESCE(protein, 0) <> 0 "
        "OR COALESCE(carbs, 0) <> 0 "
        "OR COALESCE(fat, 0) <> 0)"))

    # Only fill genuinely invalid base serving sizes for all other rows, so a
    # deliberate resize keeps its original base.
    op.execute(sa.text(
        "UPDATE meal_entries SET base_serving_grams = serving_grams "
        "WHERE base_serving_grams IS NULL OR base_serving_grams < 1"))


def _ensure_indexes():
    for table, expected in _EXPECTED_INDEXES.items():
        for name, columns, unique in expected:
            _create_index_if_missing(table, name, list(columns), unique=unique)


def upgrade():
    _preflight()

    _ensure_users()
    _create_meal_entries()
    _create_saved_recipes()
    _create_community_foods()

    bases_incomplete = _ensure_meal_columns()
    _backfill_meal_bases(bases_incomplete)
    _ensure_indexes()


def downgrade():
    raise RuntimeError(
        "0003_nutrition_schema is a forward-only, data-preserving migration. "
        "Restore a verified database backup instead of downgrading; "
        "`alembic downgrade` cannot safely reconstruct the previous schema "
        "without dropping user tables."
    )
