"""Recipe composition: ingredient state and cooked-yield metadata.

``recipe_ingredients`` already stores recipe-to-ingredient links, grams, units
and order.  This revision adds the three fields the nutrient calculation needs
but could not store before:

* ``preparation_state`` - whether the ingredient amount refers to a raw or
  cooked item;
* ``edible_portion`` - the fraction of the amount that is actually eaten;
* ``nutrient_snapshot`` - the per-100-g values used at calculation time, so an
  old recipe keeps its provenance even if the ingredient profile changes.

``recipe_details`` adds the calculation denominator and quality flag: cooked
weight, optional uncooked weight, portion count, quality (measured/estimated)
and method.

Forward-only.  No existing recipe, catalog, nutrient or diary row is modified.
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_recipe_composition"
down_revision = "0008_multilingual_search"
branch_labels = None
depends_on = None

_INGREDIENT_COLUMNS = (
    ("preparation_state", sa.String(length=16)),
    ("edible_portion", sa.Double()),
    ("nutrient_snapshot", sa.JSON()),
)

_DETAILS_COLUMNS = (
    sa.Column("food_id", sa.Integer(),
              sa.ForeignKey("catalog_foods.id", ondelete="CASCADE"),
              primary_key=True),
    sa.Column("cooked_weight_grams", sa.Double(), nullable=False),
    sa.Column("uncooked_weight_grams", sa.Double(), nullable=True),
    sa.Column("portion_count", sa.Integer(), nullable=True),
    sa.Column("quality", sa.String(length=16), nullable=False),
    sa.Column("calculation_method", sa.String(length=32), nullable=False),
    sa.Column("dataset_version", sa.String(length=64), nullable=True),
    sa.Column("notes", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "recipe_ingredients" in tables:
        existing = _columns("recipe_ingredients")
        for name, column_type in _INGREDIENT_COLUMNS:
            if name not in existing:
                op.add_column("recipe_ingredients", sa.Column(name, column_type, nullable=True))
    if "recipe_details" not in tables:
        op.create_table("recipe_details", *_DETAILS_COLUMNS)


def downgrade() -> None:
    raise RuntimeError(
        "0009_recipe_composition is a forward-only migration. Dropping recipe "
        "yield metadata or ingredient snapshots would remove calculation "
        "provenance. Restore a verified database backup instead."
    )
