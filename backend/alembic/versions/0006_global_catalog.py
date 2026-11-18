"""Global multilingual catalog foundation.

Adds child tables for translations, aliases, nutrient profiles, servings,
barcodes, recipe ingredients, and the data-source/license registry.  The
existing ``catalog_foods`` table remains the canonical food identity table, so
no rows are copied, renamed or deleted.  Adding a new language inserts a
``food_translations`` row instead of changing the schema.
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0006_global_catalog"
down_revision = "0005_catalog_foods"
branch_labels = None
depends_on = None

_TABLES = {
    "food_sources": (
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("data_types", sa.String(length=160), nullable=False),
        sa.Column("acquisition_method", sa.String(length=64), nullable=False),
        sa.Column("license", sa.String(length=160), nullable=False),
        sa.Column("attribution_required", sa.Boolean(), nullable=False),
        sa.Column("storage_permission", sa.Boolean(), nullable=False),
        sa.Column("translation_permission", sa.Boolean(), nullable=False),
        sa.Column("redistribution_permission", sa.Boolean(), nullable=False),
        sa.Column("approval_status", sa.String(length=16), nullable=False),
        sa.Column("limitations", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ),
    "food_translations": (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("food_id", sa.Integer(), sa.ForeignKey("catalog_foods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("translation_source", sa.String(length=32), nullable=False),
        sa.Column("translation_status", sa.String(length=16), nullable=False),
        sa.Column("review_status", sa.String(length=16), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ),
    "food_aliases": (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("food_id", sa.Integer(), sa.ForeignKey("catalog_foods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("alias_type", sa.String(length=16), nullable=False),
        sa.Column("source_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ),
    "nutrient_profiles": (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("food_id", sa.Integer(), sa.ForeignKey("catalog_foods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reference_amount", sa.Double(), nullable=False),
        sa.Column("reference_unit", sa.String(length=8), nullable=False),
        sa.Column("kcal", sa.Double(), nullable=True),
        sa.Column("protein", sa.Double(), nullable=True),
        sa.Column("carbs", sa.Double(), nullable=True),
        sa.Column("fat", sa.Double(), nullable=True),
        sa.Column("fiber", sa.Double(), nullable=True),
        sa.Column("sugar", sa.Double(), nullable=True),
        sa.Column("sodium", sa.Double(), nullable=True),
        sa.Column("saturated_fat", sa.Double(), nullable=True),
        sa.Column("source_code", sa.String(length=32), nullable=True),
        sa.Column("source_record_id", sa.String(length=200), nullable=True),
        sa.Column("dataset_version", sa.String(length=64), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("review_status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ),
    "food_servings": (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("food_id", sa.Integer(), sa.ForeignKey("catalog_foods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Double(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("grams", sa.Double(), nullable=True),
        sa.Column("description", sa.String(length=120), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("source_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ),
    "food_barcodes": (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("food_id", sa.Integer(), sa.ForeignKey("catalog_foods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("barcode", sa.String(length=14), nullable=False),
        sa.Column("packaging_variant", sa.String(length=120), nullable=True),
        sa.Column("source_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ),
    "recipe_ingredients": (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipe_food_id", sa.Integer(), sa.ForeignKey("catalog_foods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_food_id", sa.Integer(), sa.ForeignKey("catalog_foods.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ingredient_name", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Double(), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("grams", sa.Double(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ),
}

_INDEXES = {
    "food_translations": (
        ("uq_food_translations_food_language", ("food_id", "language"), True),
        ("ix_food_translations_language_name", ("language", "name"), False),
        ("ix_food_translations_food_id", ("food_id",), False),
    ),
    "food_aliases": (
        ("uq_food_aliases_food_language_alias", ("food_id", "language", "alias"), True),
        ("ix_food_aliases_alias", ("alias",), False),
        ("ix_food_aliases_food_id", ("food_id",), False),
    ),
    "nutrient_profiles": (
        ("ix_nutrient_profiles_food", ("food_id",), False),
        ("ix_nutrient_profiles_primary", ("food_id", "is_primary"), False),
        ("ix_nutrient_profiles_food_id", ("food_id",), False),
    ),
    "food_servings": (
        ("ix_food_servings_food", ("food_id",), False),
        ("ix_food_servings_food_id", ("food_id",), False),
    ),
    "food_barcodes": (
        ("uq_food_barcodes_barcode", ("barcode",), True),
        ("ix_food_barcodes_food", ("food_id",), False),
        ("ix_food_barcodes_food_id", ("food_id",), False),
    ),
    "recipe_ingredients": (
        ("uq_recipe_ingredients_position", ("recipe_food_id", "position"), True),
        ("ix_recipe_ingredients_food", ("recipe_food_id",), False),
        ("ix_recipe_ingredients_ingredient", ("ingredient_food_id",), False),
    ),
}

_SOURCE_ROWS = (
    ("builtin", "Project built-in catalog", "ingredient,prepared_meal", "internal seed",
     "Project-generated / USDA-derived values", False, True, True, True, "approved",
     "Values are approximate references; not medical advice."),
    ("manual", "Curated documented recipes", "ingredient,prepared_meal", "manual review",
     "Project recipe documentation", False, True, True, True, "approved",
     "Composite values are approximate and must remain marked as such."),
    ("usda", "USDA FoodData Central", "ingredient,prepared_meal,branded_product", "API/bulk download",
     "CC0 1.0", False, True, True, True, "approved",
     "Use a stable FDC id where available; preserve dataset release information."),
    ("openfoodfacts", "Open Food Facts", "branded_product", "API/dump",
     "ODbL 1.0 / DbCL 1.0", True, False, True, False, "pending",
     "Runtime barcode lookup is allowed; bulk import is pending an ODbL/ShareAlike review."),
    ("fatsecret", "FatSecret Platform API", "ingredient,prepared_meal,branded_product", "API",
     "FatSecret Platform API Terms", True, False, False, False, "blocked",
     "Only identifiers such as food_id/serving_id are storable indefinitely; nutrition payloads and names require written custom-storage permission."),
    ("fao_infoods", "FAO/INFOODS and national databases", "ingredient,prepared_meal", "bulk download",
     "Varies by dataset", True, False, False, False, "pending",
     "Each national dataset requires an individual licence review."),
    ("eurofir", "EuroFIR", "ingredient,prepared_meal", "membership",
     "Membership / national licence", True, False, False, False, "pending",
     "Access and redistribution depend on membership and national agreements."),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _tables():
    return set(_inspector().get_table_names())


def _index_names(table):
    return {index["name"] for index in _inspector().get_indexes(table)}


def _create_tables():
    existing = _tables()
    for name, columns in _TABLES.items():
        if name not in existing:
            op.create_table(name, *columns)


def _create_indexes():
    existing_tables = _tables()
    for table, indexes in _INDEXES.items():
        if table not in existing_tables:
            continue
        names = _index_names(table)
        for name, columns, unique in indexes:
            if name not in names:
                op.create_index(name, table, list(columns), unique=unique)


def _seed_sources():
    if "food_sources" not in _tables():
        return
    existing = {row[0] for row in op.get_bind().execute(
        sa.text("SELECT code FROM food_sources")
    ).fetchall()}
    now = datetime.now(timezone.utc)
    rows = [
        {
            "code": code, "name": name, "data_types": data_types,
            "acquisition_method": acquisition, "license": license_name,
            "attribution_required": attribution, "storage_permission": storage,
            "translation_permission": translation, "redistribution_permission": redistribution,
            "approval_status": approval, "limitations": limitations,
            "created_at": now, "updated_at": now,
        }
        for (code, name, data_types, acquisition, license_name, attribution, storage,
             translation, redistribution, approval, limitations) in _SOURCE_ROWS
        if code not in existing
    ]
    if rows:
        op.bulk_insert(sa.table(
            "food_sources",
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("data_types", sa.String),
            sa.column("acquisition_method", sa.String),
            sa.column("license", sa.String),
            sa.column("attribution_required", sa.Boolean),
            sa.column("storage_permission", sa.Boolean),
            sa.column("translation_permission", sa.Boolean),
            sa.column("redistribution_permission", sa.Boolean),
            sa.column("approval_status", sa.String),
            sa.column("limitations", sa.Text),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ), rows)


def upgrade():
    _create_tables()
    _create_indexes()
    _seed_sources()


def downgrade():
    raise RuntimeError(
        "0006_global_catalog is a forward-only, data-preserving migration. "
        "Dropping translations, aliases, nutrient profiles, servings or barcodes "
        "would delete catalog provenance. Restore a verified database backup instead."
    )
