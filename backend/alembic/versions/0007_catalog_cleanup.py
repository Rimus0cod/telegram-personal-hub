"""Catalog cleanup: precise ``builtin`` licensing and duplicate index removal.

Two independent fixes, both forward-only:

1. ``food_sources.builtin`` described data *origin* in its ``license`` field.
   Origin and licence are different things, so the row now states that the
   content is project-generated or CC0-derived instead of implying a single
   licence for mixed records.
2. ``0006_global_catalog`` created some ``food_id`` indexes twice: once through
   ``index=True`` (``ix_food_*_food_id``) and once explicitly
   (``ix_food_*_food``).  The duplicate definitions are identical single-column
   btree indexes and back no constraint, so the explicit copies are dropped and
   the auto-named ones stay.  Verified on PostgreSQL 16 and 18 before writing
   this revision.

No catalog, diary or source row is deleted.
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0007_catalog_cleanup"
down_revision = "0006_global_catalog"
branch_labels = None
depends_on = None

# (table, redundant index) - each is an exact duplicate of ``ix_<table>_food_id``.
_DUPLICATE_INDEXES = (
    ("food_barcodes", "ix_food_barcodes_food"),
    ("food_servings", "ix_food_servings_food"),
    ("nutrient_profiles", "ix_nutrient_profiles_food"),
)

_BUILTIN_LICENSE = "Project-generated content; USDA-derived values are CC0 1.0"
_BUILTIN_LIMITATIONS = (
    "Mixed origin: project-authored recipes or CC0 USDA values. Never relabelled "
    "restricted third-party content."
)


def _inspector():
    return sa.inspect(op.get_bind())


def _index_names(table: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(table)}


def _drop_duplicate_indexes() -> None:
    tables = set(_inspector().get_table_names())
    for table, index_name in _DUPLICATE_INDEXES:
        if table not in tables:
            continue
        if index_name not in _index_names(table):
            continue
        op.drop_index(index_name, table_name=table)


def _clarify_builtin_license() -> None:
    tables = set(_inspector().get_table_names())
    if "food_sources" not in tables:
        return
    op.get_bind().execute(
        sa.text(
            "UPDATE food_sources SET license = :license, limitations = :limitations, "
            "updated_at = :now WHERE code = 'builtin'"
        ),
        {
            "license": _BUILTIN_LICENSE,
            "limitations": _BUILTIN_LIMITATIONS,
            "now": datetime.now(timezone.utc),
        },
    )


def upgrade() -> None:
    _drop_duplicate_indexes()
    _clarify_builtin_license()


def downgrade() -> None:
    raise RuntimeError(
        "0007_catalog_cleanup is a forward-only, data-preserving migration. "
        "Recreating duplicate indexes or an inaccurate licence label is not a "
        "rollback; restore a verified database backup instead."
    )
