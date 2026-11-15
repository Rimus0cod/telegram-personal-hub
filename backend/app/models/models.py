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
                      Index("ix_community_foods_prefix", "barcode_prefix"))
    id: Mapped[int] = mapped_column(primary_key=True)
    barcode: Mapped[str] = mapped_column(String(14))
    barcode_prefix: Mapped[str | None] = mapped_column(String(7), index=True)
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str] = mapped_column(String(120), default="")
    kcal: Mapped[float] = mapped_column(Double, default=0)
    protein: Mapped[float] = mapped_column(Double, default=0)
    carbs: Mapped[float] = mapped_column(Double, default=0)
    fat: Mapped[float] = mapped_column(Double, default=0)
    source: Mapped[str] = mapped_column(String(32), default="community")
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
