"""Add the curated PostgreSQL catalog.

The existing ``community_foods`` table is barcode-first and requires a
submitter, so it is not a safe place for imported generic foods.  This revision
introduces a separate shared ``catalog_foods`` table for curated ingredients and
prepared meals.  It is forward-only and idempotent: a fresh database may already
contain the table because ``0001_initial`` builds from current metadata.

No user rows, community foods or diary entries are modified or deleted.
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_catalog_foods"
down_revision = "0004_focus_session_client_id"
branch_labels = None
depends_on = None

_TABLE = "catalog_foods"
_INDEXES = (
    ("uq_catalog_foods_source_source_id", ("source", "source_id"), True),
    ("ix_catalog_foods_type_name", ("type", "name"), False),
    ("ix_catalog_foods_category", ("category",), False),
    ("ix_catalog_foods_active", ("is_active",), False),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _tables():
    return set(_inspector().get_table_names())


def _indexes(table):
    return {index["name"] for index in _inspector().get_indexes(table)}


def _create_table():
    if _TABLE in _tables():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_original", sa.String(length=200), nullable=True),
        sa.Column("name_ru", sa.String(length=200), nullable=True),
        sa.Column("name_uk", sa.String(length=200), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("kcal", sa.Double(), nullable=False),
        sa.Column("protein", sa.Double(), nullable=False),
        sa.Column("carbs", sa.Double(), nullable=False),
        sa.Column("fat", sa.Double(), nullable=False),
        sa.Column("serving_size", sa.Double(), nullable=False),
        sa.Column("serving_unit", sa.String(length=8), nullable=False),
        sa.Column("preparation_state", sa.String(length=16), nullable=True),
        sa.Column("approximate", sa.Boolean(), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _create_indexes():
    existing = _indexes(_TABLE)
    for name, columns, unique in _INDEXES:
        if name not in existing:
            op.create_index(name, _TABLE, list(columns), unique=unique)


def upgrade():
    _create_table()
    if _TABLE not in _tables():
        raise RuntimeError(
            "Cannot create the curated catalog: catalog_foods is missing after "
            "the table creation step. Restore from backup or investigate the "
            "database before retrying."
        )
    _create_indexes()


def downgrade():
    raise RuntimeError(
        "0005_catalog_foods is a forward-only, data-preserving migration. "
        "Dropping the curated catalog would delete imported foods and break "
        "future catalog imports. Restore a verified database backup instead."
    )
