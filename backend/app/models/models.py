from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Double, ForeignKey, Integer, String, Text, BigInteger, Index, JSON, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

def now(): return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    last_city: Mapped[str | None] = mapped_column(String(120))
    calorie_target: Mapped[int] = mapped_column(Integer, default=2000, server_default=text("2000"))
    protein_target: Mapped[int] = mapped_column(Integer, default=120, server_default=text("120"))
    carbs_target: Mapped[int] = mapped_column(Integer, default=250, server_default=text("250"))
    fat_target: Mapped[int] = mapped_column(Integer, default=70, server_default=text("70"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    notes: Mapped[list["Note"]] = relationship(cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(cascade="all, delete-orphan")
    sessions: Mapped[list["FocusSession"]] = relationship(cascade="all, delete-orphan")
    meals: Mapped[list["MealEntry"]] = relationship(cascade="all, delete-orphan")

class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_user_updated", "user_id", "updated_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_user_completed", "user_id", "completed"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class FocusSession(Base):
    __tablename__ = "focus_sessions"
    __table_args__ = (
        Index("ix_focus_user_finished", "user_id", "finished_at"),
        # Idempotency key scoped to one user; NULL means "legacy session".
        Index("uq_focus_sessions_user_client", "user_id", "client_id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    duration: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16))
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    client_id: Mapped[str | None] = mapped_column(String(36))

class MealEntry(Base):
    __tablename__ = "meal_entries"
    __table_args__ = (Index("ix_meals_user_day", "user_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    meal_type: Mapped[str] = mapped_column(String(16), default="other")
    name: Mapped[str] = mapped_column(String(160))
    calories: Mapped[int] = mapped_column(Integer)
    protein: Mapped[float] = mapped_column(Double, default=0)
    carbs: Mapped[float] = mapped_column(Double, default=0)
    fat: Mapped[float] = mapped_column(Double, default=0)
    ingredients: Mapped[str] = mapped_column(Text, default="")
    serving_grams: Mapped[int] = mapped_column(Integer, default=100, server_default=text("100"))
    base_serving_grams: Mapped[int] = mapped_column(Integer, default=100, server_default=text("100"))
    base_calories: Mapped[float] = mapped_column(Double, default=0, server_default=text("0"))
    base_protein: Mapped[float] = mapped_column(Double, default=0, server_default=text("0"))
    base_carbs: Mapped[float] = mapped_column(Double, default=0, server_default=text("0"))
    base_fat: Mapped[float] = mapped_column(Double, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SavedRecipe(Base):
    __tablename__ = "saved_recipes"
    __table_args__ = (Index("ix_recipes_user_name", "user_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    meal_type: Mapped[str] = mapped_column(String(16), default="lunch")
    ingredients: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class CommunityFood(Base):
    __tablename__ = "community_foods"
    __table_args__ = (Index("ix_community_foods_barcode", "barcode", unique=True),
                      Index("ix_community_foods_prefix", "barcode_prefix"),
                      Index("ix_community_foods_normalized", "normalized_name"),
                      Index("ix_community_foods_normalized_prefix", "normalized_name",
                            postgresql_ops={"normalized_name": "varchar_pattern_ops"}))
    id: Mapped[int] = mapped_column(primary_key=True)
    barcode: Mapped[str] = mapped_column(String(14))
    barcode_prefix: Mapped[str | None] = mapped_column(String(7), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str | None] = mapped_column(String(200))
    brand: Mapped[str] = mapped_column(String(120), default="")
    kcal: Mapped[float] = mapped_column(Double, default=0)
    protein: Mapped[float] = mapped_column(Double, default=0)
    carbs: Mapped[float] = mapped_column(Double, default=0)
    fat: Mapped[float] = mapped_column(Double, default=0)
    source: Mapped[str] = mapped_column(String(32), default="community")
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CatalogItem(Base):
    """Curated, shared food catalog stored independently of barcode products.

    ``CommunityFood`` remains the user-contributed barcode catalog because its
    required barcode and submitter columns do not fit generic imported foods.
    This table is the PostgreSQL-backed curated catalog used by Nutrition.
    """
    __tablename__ = "catalog_foods"
    __table_args__ = (
        Index("uq_catalog_foods_source_source_id", "source", "source_id", unique=True),
        Index("ix_catalog_foods_type_name", "type", "name"),
        Index("ix_catalog_foods_category", "category"),
        Index("ix_catalog_foods_active", "is_active"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="builtin")
    source_id: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    name_original: Mapped[str | None] = mapped_column(String(200))
    name_ru: Mapped[str | None] = mapped_column(String(200))
    name_uk: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80), default="Другое")
    type: Mapped[str] = mapped_column(String(16), default="ingredient")
    kcal: Mapped[float] = mapped_column(Double, default=0)
    protein: Mapped[float] = mapped_column(Double, default=0)
    carbs: Mapped[float] = mapped_column(Double, default=0)
    fat: Mapped[float] = mapped_column(Double, default=0)
    serving_size: Mapped[float] = mapped_column(Double, default=100)
    serving_unit: Mapped[str] = mapped_column(String(8), default="g")
    preparation_state: Mapped[str | None] = mapped_column(String(16))
    approximate: Mapped[bool] = mapped_column(Boolean, default=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    translations: Mapped[list["FoodTranslation"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin")
    aliases: Mapped[list["FoodAlias"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin")
    nutrient_profiles: Mapped[list["NutrientProfile"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin")
    servings: Mapped[list["FoodServing"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin")
    barcodes: Mapped[list["FoodBarcode"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin")
    search_terms: Mapped[list["FoodSearchTerm"]] = relationship(
        cascade="all, delete-orphan")
    recipe_ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        cascade="all, delete-orphan",
        foreign_keys="RecipeIngredient.recipe_food_id")
    recipe_details: Mapped["RecipeDetails | None"] = relationship(
        cascade="all, delete-orphan", uselist=False, single_parent=True)


class FoodSource(Base):
    """Registry of data providers and their licensing/storage permissions."""
    __tablename__ = "food_sources"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    data_types: Mapped[str] = mapped_column(String(160), default="")
    acquisition_method: Mapped[str] = mapped_column(String(64), default="")
    license: Mapped[str] = mapped_column(String(160), default="")
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_permission: Mapped[bool] = mapped_column(Boolean, default=False)
    translation_permission: Mapped[bool] = mapped_column(Boolean, default=False)
    redistribution_permission: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_status: Mapped[str] = mapped_column(String(16), default="pending")
    limitations: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class FoodTranslation(Base):
    __tablename__ = "food_translations"
    __table_args__ = (
        Index("uq_food_translations_food_language", "food_id", "language", unique=True),
        Index("ix_food_translations_language_name", "language", "name"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    food_id: Mapped[int] = mapped_column(ForeignKey("catalog_foods.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    translation_source: Mapped[str] = mapped_column(String(32), default="manual")
    translation_status: Mapped[str] = mapped_column(String(16), default="draft")
    review_status: Mapped[str] = mapped_column(String(16), default="pending")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FoodAlias(Base):
    __tablename__ = "food_aliases"
    __table_args__ = (
        Index("uq_food_aliases_food_language_alias", "food_id", "language", "alias", unique=True),
        Index("ix_food_aliases_alias", "alias"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    food_id: Mapped[int] = mapped_column(ForeignKey("catalog_foods.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(16), default="und")
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(16), default="search")
    source_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NutrientProfile(Base):
    __tablename__ = "nutrient_profiles"
    __table_args__ = (
        Index("ix_nutrient_profiles_primary", "food_id", "is_primary"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    food_id: Mapped[int] = mapped_column(ForeignKey("catalog_foods.id", ondelete="CASCADE"), index=True)
    reference_amount: Mapped[float] = mapped_column(Double, default=100)
    reference_unit: Mapped[str] = mapped_column(String(8), default="g")
    kcal: Mapped[float | None] = mapped_column(Double)
    protein: Mapped[float | None] = mapped_column(Double)
    carbs: Mapped[float | None] = mapped_column(Double)
    fat: Mapped[float | None] = mapped_column(Double)
    fiber: Mapped[float | None] = mapped_column(Double)
    sugar: Mapped[float | None] = mapped_column(Double)
    sodium: Mapped[float | None] = mapped_column(Double)
    saturated_fat: Mapped[float | None] = mapped_column(Double)
    source_code: Mapped[str | None] = mapped_column(String(32))
    source_record_id: Mapped[str | None] = mapped_column(String(200))
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    review_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FoodServing(Base):
    __tablename__ = "food_servings"
    id: Mapped[int] = mapped_column(primary_key=True)
    food_id: Mapped[int] = mapped_column(ForeignKey("catalog_foods.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Double, default=100)
    unit: Mapped[str] = mapped_column(String(16), default="g")
    grams: Mapped[float | None] = mapped_column(Double)
    description: Mapped[str | None] = mapped_column(String(120))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    source_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FoodBarcode(Base):
    __tablename__ = "food_barcodes"
    __table_args__ = (
        Index("uq_food_barcodes_barcode", "barcode", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    food_id: Mapped[int] = mapped_column(ForeignKey("catalog_foods.id", ondelete="CASCADE"), index=True)
    barcode: Mapped[str] = mapped_column(String(14), nullable=False)
    packaging_variant: Mapped[str | None] = mapped_column(String(120))
    source_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        Index("uq_recipe_ingredients_position", "recipe_food_id", "position", unique=True),
        Index("ix_recipe_ingredients_food", "recipe_food_id"),
        Index("ix_recipe_ingredients_ingredient", "ingredient_food_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_food_id: Mapped[int] = mapped_column(ForeignKey("catalog_foods.id", ondelete="CASCADE"))
    ingredient_food_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_foods.id", ondelete="SET NULL"))
    ingredient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[float | None] = mapped_column(Double)
    unit: Mapped[str | None] = mapped_column(String(16))
    grams: Mapped[float | None] = mapped_column(Double)
    preparation_state: Mapped[str | None] = mapped_column(String(16))
    edible_portion: Mapped[float | None] = mapped_column(Double)
    nutrient_snapshot: Mapped[dict | None] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecipeDetails(Base):
    """Yield and quality metadata for one prepared meal.

    Ingredient composition lives in ``recipe_ingredients``; this row records
    what the nutrient calculation used as its denominator and how reliable the
    recipe mass is.
    """
    __tablename__ = "recipe_details"
    food_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_foods.id", ondelete="CASCADE"), primary_key=True
    )
    cooked_weight_grams: Mapped[float] = mapped_column(Double, nullable=False)
    uncooked_weight_grams: Mapped[float | None] = mapped_column(Double)
    portion_count: Mapped[int | None] = mapped_column(Integer)
    quality: Mapped[str] = mapped_column(String(16), default="estimated")
    calculation_method: Mapped[str] = mapped_column(
        String(32), default="ingredient_sum"
    )
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FoodSearchTerm(Base):
    """Derived normalized search term for one catalog food.

    Rows are rebuilt from names, approved translations and aliases.  The table
    is an index, not a source of truth: ``catalog_foods``,
    ``food_translations`` and ``food_aliases`` stay authoritative.
    """
    __tablename__ = "food_search_terms"
    __table_args__ = (
        Index("uq_food_search_terms", "food_id", "language", "term_type",
              "normalized", unique=True),
        Index("ix_food_search_terms_normalized", "normalized"),
        Index("ix_food_search_terms_normalized_prefix", "normalized",
              postgresql_ops={"normalized": "varchar_pattern_ops"}),
        Index("ix_food_search_terms_food", "food_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    food_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_foods.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(String(16), default="und")
    term_type: Mapped[str] = mapped_column(String(16), default="canonical")
    term: Mapped[str] = mapped_column(String(200))
    normalized: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
