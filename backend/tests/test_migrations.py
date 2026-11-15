"""Regression tests for the forward-only Nutrition schema reconciliation.

The tests run Alembic in a subprocess against disposable databases. This keeps
``app.core.config`` (which reads ``DATABASE_URL`` at import time) isolated from
the developer's environment and exercises the same command used in Docker.
"""

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD = "0004_focus_session_client_id"
LEGACY_REVISION = "0002_add_last_city"


def _run_alembic(database_url, *args):
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _sqlite_url(path):
    return f"sqlite:///{Path(path).as_posix()}"


def _assert_ok(result):
    assert result.returncode == 0, (
        f"alembic exited with {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _upgrade(path):
    result = _run_alembic(_sqlite_url(path), "upgrade", "head")
    _assert_ok(result)
    return result


def _execute_script(path, script):
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()


def _query(path, sql, parameters=()):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def _columns(path, table):
    return {row[1] for row in _query(path, f"PRAGMA table_info({table})")}


def _tables(path):
    return {
        row[0] for row in _query(
            path, "SELECT name FROM sqlite_master WHERE type='table'")
    }


def _indexes(path):
    return {
        row[0] for row in _query(
            path, "SELECT name FROM sqlite_master WHERE type='index'")
    }


def _version(path):
    return _query(path, "SELECT version_num FROM alembic_version")[0][0]


_CORE_LEGACY_SQL = """
CREATE TABLE users (
    id INTEGER NOT NULL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    username VARCHAR(64),
    first_name VARCHAR(128) NOT NULL,
    last_name VARCHAR(128),
    last_city VARCHAR(120),
    created_at DATETIME NOT NULL
);
CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id);

CREATE TABLE notes (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_notes_user_id ON notes (user_id);
CREATE INDEX ix_notes_user_updated ON notes (user_id, updated_at);

CREATE TABLE tasks (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    completed BOOLEAN NOT NULL,
    priority VARCHAR(10) NOT NULL,
    due_date DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_tasks_user_id ON tasks (user_id);
CREATE INDEX ix_tasks_user_completed ON tasks (user_id, completed);

CREATE TABLE focus_sessions (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    mode VARCHAR(16) NOT NULL,
    completed BOOLEAN NOT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_focus_sessions_user_id ON focus_sessions (user_id);
CREATE INDEX ix_focus_user_finished ON focus_sessions (user_id, finished_at);

CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
INSERT INTO alembic_version VALUES ('0002_add_last_city');
INSERT INTO users (id, telegram_id, first_name, created_at)
VALUES (1, 101, 'Legacy', '2026-01-01 00:00:00');
"""


_LEGACY_NUTRITION_SQL = _CORE_LEGACY_SQL + """
CREATE TABLE meal_entries (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    day VARCHAR(10) NOT NULL,
    meal_type VARCHAR(16) NOT NULL,
    name VARCHAR(160) NOT NULL,
    calories INTEGER NOT NULL,
    protein FLOAT NOT NULL,
    carbs FLOAT NOT NULL,
    fat FLOAT NOT NULL,
    ingredients TEXT NOT NULL,
    serving_grams INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_meal_entries_day ON meal_entries (day);
CREATE INDEX ix_meal_entries_user_id ON meal_entries (user_id);
CREATE INDEX ix_meals_user_day ON meal_entries (user_id, day);

CREATE TABLE saved_recipes (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name VARCHAR(160) NOT NULL,
    meal_type VARCHAR(16) NOT NULL,
    ingredients JSON NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_saved_recipes_user_id ON saved_recipes (user_id);
CREATE INDEX ix_recipes_user_name ON saved_recipes (user_id, name);

INSERT INTO meal_entries
    (id, user_id, day, meal_type, name, calories, protein, carbs, fat,
     ingredients, serving_grams, created_at)
VALUES
    (1, 1, '2026-01-01', 'lunch', 'normal meal',
     500, 20, 50, 15, '', 250, '2026-01-01 00:00:00'),
    (2, 1, '2026-01-01', 'snack', 'zero calorie macros',
     0, 7, 8, 9, '', 100, '2026-01-01 00:00:00'),
    (3, 1, '2026-01-02', 'dinner', 'all zero',
     0, 0, 0, 0, '', 100, '2026-01-01 00:00:00');
"""


def test_fresh_database_reaches_the_new_head(tmp_path):
    database = tmp_path / "fresh.db"

    _upgrade(database)

    assert _version(database) == HEAD
    assert {
        "users", "notes", "tasks", "focus_sessions", "meal_entries",
        "saved_recipes", "community_foods",
    } <= _tables(database)
    assert {
        "calorie_target", "protein_target", "carbs_target", "fat_target",
    } <= _columns(database, "users")
    assert {
        "serving_grams", "base_serving_grams", "base_calories",
        "base_protein", "base_carbs", "base_fat",
    } <= _columns(database, "meal_entries")
    assert {
        "ix_meal_entries_day", "ix_meal_entries_user_id", "ix_meals_user_day",
        "ix_saved_recipes_user_id", "ix_recipes_user_name",
        "ix_community_foods_barcode", "ix_community_foods_barcode_prefix",
        "ix_community_foods_prefix", "ix_community_foods_submitted_by",
    } <= _indexes(database)


def test_legacy_nutrition_database_is_reconciled_without_losing_data(tmp_path):
    database = tmp_path / "legacy_nutrition.db"
    _execute_script(database, _LEGACY_NUTRITION_SQL)

    _upgrade(database)

    assert _version(database) == HEAD
    assert "community_foods" in _tables(database)
    assert {
        "calorie_target", "protein_target", "carbs_target", "fat_target",
    } <= _columns(database, "users")

    targets = _query(
        database,
        "SELECT calorie_target, protein_target, carbs_target, fat_target "
        "FROM users WHERE id = 1",
    )[0]
    assert targets == (2000, 120, 250, 70)

    rows = _query(
        database,
        "SELECT id, calories, protein, carbs, fat, serving_grams, "
        "base_serving_grams, base_calories, base_protein, base_carbs, base_fat "
        "FROM meal_entries ORDER BY id",
    )
    by_id = {row[0]: row for row in rows}

    # A normal meal: current values are preserved and become the base values.
    assert by_id[1] == (1, 500, 20, 50, 15, 250, 250, 500, 20, 50, 15)
    # The historical bug: zero calories but non-zero macros. Both the current
    # values and the newly reconstructed base values must survive.
    assert by_id[2] == (2, 0, 7, 8, 9, 100, 100, 0, 7, 8, 9)
    # A genuinely empty meal stays empty.
    assert by_id[3] == (3, 0, 0, 0, 0, 100, 100, 0, 0, 0, 0)


def test_pre_nutrition_database_gets_all_missing_tables(tmp_path):
    database = tmp_path / "pre_nutrition.db"
    _execute_script(database, _CORE_LEGACY_SQL)

    _upgrade(database)

    assert _version(database) == HEAD
    assert {"meal_entries", "saved_recipes", "community_foods"} <= _tables(database)
    assert _query(
        database,
        "SELECT telegram_id, first_name FROM users WHERE id = 1",
    )[0] == (101, "Legacy")
    assert _query(
        database,
        "SELECT calorie_target, protein_target, carbs_target, fat_target "
        "FROM users WHERE id = 1",
    )[0] == (2000, 120, 250, 70)
    assert _query(database, "SELECT COUNT(*) FROM meal_entries")[0][0] == 0


def test_partially_patched_database_rebuilds_incomplete_base_columns(tmp_path):
    database = tmp_path / "partial.db"
    _execute_script(database, """
CREATE TABLE users (
    id INTEGER NOT NULL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    username VARCHAR(64),
    first_name VARCHAR(128) NOT NULL,
    last_name VARCHAR(128),
    last_city VARCHAR(120),
    calorie_target INTEGER NOT NULL DEFAULT 2000,
    created_at DATETIME NOT NULL
);
CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id);

CREATE TABLE focus_sessions (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    mode VARCHAR(16) NOT NULL,
    completed BOOLEAN NOT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_focus_sessions_user_id ON focus_sessions (user_id);
CREATE INDEX ix_focus_user_finished ON focus_sessions (user_id, finished_at);

CREATE TABLE meal_entries (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    day VARCHAR(10) NOT NULL,
    meal_type VARCHAR(16) NOT NULL,
    name VARCHAR(160) NOT NULL,
    calories INTEGER NOT NULL,
    protein FLOAT NOT NULL,
    carbs FLOAT NOT NULL,
    fat FLOAT NOT NULL,
    ingredients TEXT NOT NULL,
    serving_grams INTEGER NOT NULL,
    base_serving_grams INTEGER NOT NULL DEFAULT 100,
    base_calories FLOAT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL
);

CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
INSERT INTO alembic_version VALUES ('0002_add_last_city');
INSERT INTO users (id, telegram_id, first_name, calorie_target, created_at)
VALUES (1, 101, 'Partial', 1800, '2026-01-01 00:00:00');
INSERT INTO meal_entries
    (id, user_id, day, meal_type, name, calories, protein, carbs, fat,
     ingredients, serving_grams, base_serving_grams, base_calories, created_at)
VALUES
    (1, 1, '2026-01-01', 'lunch', 'partial bases',
     400, 10, 20, 5, '', 200, 100, 0, '2026-01-01 00:00:00'),
    (2, 1, '2026-01-02', 'dinner', 'second partial base',
     600, 20, 40, 10, '', 200, 100, 0, '2026-01-01 00:00:00');
""")

    _upgrade(database)

    assert _version(database) == HEAD
    assert {
        "base_serving_grams", "base_calories", "base_protein",
        "base_carbs", "base_fat",
    } <= _columns(database, "meal_entries")
    assert _query(
        database,
        "SELECT calorie_target, protein_target, carbs_target, fat_target "
        "FROM users WHERE id = 1",
    )[0] == (1800, 120, 250, 70)

    rows = _query(
        database,
        "SELECT id, calories, protein, carbs, fat, serving_grams, "
        "base_serving_grams, base_calories, base_protein, base_carbs, base_fat "
        "FROM meal_entries ORDER BY id",
    )
    # The surviving base columns still hold their ADD COLUMN defaults, so the
    # tuple was never usable by the application. Both rows are rebuilt from
    # their authoritative current values.
    assert {row[0]: row for row in rows} == {
        1: (1, 400, 10, 20, 5, 200, 200, 400, 10, 20, 5),
        2: (2, 600, 20, 40, 10, 200, 200, 600, 20, 40, 10),
    }


def test_already_patched_current_schema_repairs_only_broken_backfill(tmp_path):
    database = tmp_path / "already_patched.db"
    _execute_script(database, """
CREATE TABLE users (
    id INTEGER NOT NULL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    username VARCHAR(64),
    first_name VARCHAR(128) NOT NULL,
    last_name VARCHAR(128),
    last_city VARCHAR(120),
    calorie_target INTEGER NOT NULL DEFAULT 2000,
    protein_target INTEGER NOT NULL DEFAULT 120,
    carbs_target INTEGER NOT NULL DEFAULT 250,
    fat_target INTEGER NOT NULL DEFAULT 70,
    created_at DATETIME NOT NULL
);
CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id);

CREATE TABLE focus_sessions (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    mode VARCHAR(16) NOT NULL,
    completed BOOLEAN NOT NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_focus_sessions_user_id ON focus_sessions (user_id);
CREATE INDEX ix_focus_user_finished ON focus_sessions (user_id, finished_at);

CREATE TABLE meal_entries (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    day VARCHAR(10) NOT NULL,
    meal_type VARCHAR(16) NOT NULL,
    name VARCHAR(160) NOT NULL,
    calories INTEGER NOT NULL,
    protein FLOAT NOT NULL,
    carbs FLOAT NOT NULL,
    fat FLOAT NOT NULL,
    ingredients TEXT NOT NULL,
    serving_grams INTEGER NOT NULL,
    base_serving_grams INTEGER NOT NULL DEFAULT 100,
    base_calories FLOAT NOT NULL DEFAULT 0,
    base_protein FLOAT NOT NULL DEFAULT 0,
    base_carbs FLOAT NOT NULL DEFAULT 0,
    base_fat FLOAT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_meal_entries_day ON meal_entries (day);
CREATE INDEX ix_meal_entries_user_id ON meal_entries (user_id);
CREATE INDEX ix_meals_user_day ON meal_entries (user_id, day);

CREATE TABLE community_foods (
    id INTEGER NOT NULL PRIMARY KEY,
    barcode VARCHAR(14) NOT NULL,
    barcode_prefix VARCHAR(7),
    name VARCHAR(200) NOT NULL,
    brand VARCHAR(120) NOT NULL,
    kcal FLOAT NOT NULL,
    protein FLOAT NOT NULL,
    carbs FLOAT NOT NULL,
    fat FLOAT NOT NULL,
    source VARCHAR(32) NOT NULL,
    submitted_by INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(submitted_by) REFERENCES users (id) ON DELETE CASCADE
);
INSERT INTO community_foods
    (id, barcode, barcode_prefix, name, brand, kcal, protein, carbs, fat,
     source, submitted_by, created_at)
VALUES (1, '12345678', NULL, 'Existing shared food', '', 100, 1, 2, 3,
        'community', 1, '2026-01-01 00:00:00');

CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
INSERT INTO alembic_version VALUES ('0002_add_last_city');
INSERT INTO users (id, telegram_id, first_name, created_at)
VALUES (1, 101, 'Patched', '2026-01-01 00:00:00');
INSERT INTO meal_entries
    (id, user_id, day, meal_type, name, calories, protein, carbs, fat,
     ingredients, serving_grams, base_serving_grams, base_calories,
     base_protein, base_carbs, base_fat, created_at)
VALUES
    (1, 1, '2026-01-01', 'snack', 'broken backfill',
     0, 7, 8, 9, '', 100, 100, 0, 0, 0, 0, '2026-01-01 00:00:00'),
    (2, 1, '2026-01-02', 'lunch', 'valid resized',
     1000, 40, 80, 20, '', 500, 250, 500, 20, 50, 15,
     '2026-01-01 00:00:00');
""")

    _upgrade(database)

    assert _version(database) == HEAD
    rows = _query(
        database,
        "SELECT id, calories, protein, carbs, fat, serving_grams, "
        "base_serving_grams, base_calories, base_protein, base_carbs, base_fat "
        "FROM meal_entries ORDER BY id",
    )
    by_id = {row[0]: row for row in rows}
    assert by_id[1] == (1, 0, 7, 8, 9, 100, 100, 0, 7, 8, 9)
    # A valid base tuple and its resized current values must not be touched.
    assert by_id[2] == (2, 1000, 40, 80, 20, 500, 250, 500, 20, 50, 15)

    # Existing shared-catalog data is preserved and missing indexes are added.
    assert _query(
        database, "SELECT name, kcal FROM community_foods WHERE id = 1",
    )[0] == ("Existing shared food", 100)
    assert {
        "ix_community_foods_barcode", "ix_community_foods_barcode_prefix",
        "ix_community_foods_prefix", "ix_community_foods_submitted_by",
    } <= _indexes(database)


def test_repeated_migration_is_safe_and_keeps_resized_meals(tmp_path):
    database = tmp_path / "repeat.db"
    _upgrade(database)

    # Seed a user and a meal that has already been resized: current values are
    # twice the base values. Re-running 0003 must not reset either side.
    _execute_script(database, """
INSERT INTO users (telegram_id, first_name, created_at)
VALUES (101, 'Repeat', '2026-01-01 00:00:00');
INSERT INTO meal_entries
    (user_id, day, meal_type, name, calories, protein, carbs, fat, ingredients,
     serving_grams, base_serving_grams, base_calories, base_protein,
     base_carbs, base_fat, created_at)
VALUES
    (1, '2026-01-01', 'lunch', 'resized', 1000, 40, 80, 20, '',
     500, 250, 500, 20, 50, 15, '2026-01-01 00:00:00');
""")

    # A plain second upgrade is a no-op.
    _upgrade(database)

    # Simulate a database that is stamped 0002 even though the schema was
    # already reconciled, then run 0003 again.
    _assert_ok(_run_alembic(_sqlite_url(database), "stamp", LEGACY_REVISION))
    _upgrade(database)

    assert _version(database) == HEAD
    assert _query(
        database,
        "SELECT calories, protein, carbs, fat, serving_grams, "
        "base_serving_grams, base_calories, base_protein, base_carbs, base_fat "
        "FROM meal_entries WHERE id = 1",
    )[0] == (1000, 40, 80, 20, 500, 250, 500, 20, 50, 15)


def test_downgrade_is_refused_and_does_not_drop_tables(tmp_path):
    database = tmp_path / "downgrade.db"
    _upgrade(database)

    result = _run_alembic(_sqlite_url(database), "downgrade", "-1")

    assert result.returncode != 0
    assert "forward-only" in (result.stdout + result.stderr)
    assert _version(database) == HEAD
    assert {"users", "meal_entries", "saved_recipes", "community_foods"} <= _tables(database)


def test_importing_the_app_does_not_create_or_patch_schema(tmp_path):
    database = tmp_path / "empty.db"
    sqlite3.connect(database).close()
    env = os.environ.copy()
    env["DATABASE_URL"] = _sqlite_url(database)
    env["BOT_TOKEN"] = "test-token"

    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert _tables(database) == set()


# ---------------------------------------------------------------------------
# Stage 3.2 - preflight hardening (R1/R2/R3) and independent parity (R6)
# ---------------------------------------------------------------------------


def _schema_state(path):
    """Tables, columns and indexes, used to prove that a refusal did nothing."""
    state = {}
    for table in sorted(_tables(path)):
        state[table] = {
            "columns": tuple(sorted(_columns(path, table))),
            "indexes": tuple(sorted(
                row[1] for row in _query(path, f"PRAGMA index_list({table})")
            )),
        }
    return state


def _all_rows(path):
    return {
        table: _query(path, f"SELECT * FROM {table} ORDER BY 1")
        for table in sorted(_tables(path))
        if table != "alembic_version"
    }


def _upgrade_expecting_refusal(database, *fragments):
    """Run ``upgrade head`` and prove the refusal changed nothing at all."""
    before_schema = _schema_state(database)
    before_rows = _all_rows(database)

    result = _run_alembic(_sqlite_url(database), "upgrade", "head")

    assert result.returncode != 0, f"migration unexpectedly succeeded:\n{result.stdout}"
    output = result.stdout + result.stderr
    for fragment in fragments:
        assert fragment in output, f"{fragment!r} not found in:\n{output}"
    assert _version(database) == LEGACY_REVISION
    assert _schema_state(database) == before_schema
    assert _all_rows(database) == before_rows
    return result


def test_interrupted_startup_ddl_prefix_is_reconstructed(tmp_path):
    database = tmp_path / "interrupted_ddl.db"
    _execute_script(database, _LEGACY_NUTRITION_SQL)
    # The old startup loop crashed right after its first ALTER TABLE.
    _execute_script(database, """
ALTER TABLE meal_entries
    ADD COLUMN base_serving_grams INTEGER NOT NULL DEFAULT 100;
""")

    _upgrade(database)

    assert _version(database) == HEAD
    rows = _query(
        database,
        "SELECT id, calories, protein, carbs, fat, serving_grams, "
        "base_serving_grams, base_calories, base_protein, base_carbs, base_fat "
        "FROM meal_entries ORDER BY id",
    )
    assert {row[0]: row for row in rows} == {
        1: (1, 500, 20, 50, 15, 250, 250, 500, 20, 50, 15),
        2: (2, 0, 7, 8, 9, 100, 100, 0, 7, 8, 9),
        3: (3, 0, 0, 0, 0, 100, 100, 0, 0, 0, 0),
    }


def test_missing_base_serving_grams_with_nonzero_macros_is_refused(tmp_path):
    database = tmp_path / "suspicious_partial.db"
    _execute_script(database, _LEGACY_NUTRITION_SQL)
    # base_serving_grams is missing while the macro bases already hold real
    # values: this cannot come from the historical add-column order.
    _execute_script(database, """
ALTER TABLE meal_entries ADD COLUMN base_calories FLOAT NOT NULL DEFAULT 0;
ALTER TABLE meal_entries ADD COLUMN base_protein FLOAT NOT NULL DEFAULT 0;
ALTER TABLE meal_entries ADD COLUMN base_carbs FLOAT NOT NULL DEFAULT 0;
ALTER TABLE meal_entries ADD COLUMN base_fat FLOAT NOT NULL DEFAULT 0;
UPDATE meal_entries
SET base_calories = 500, base_protein = 20, base_carbs = 50, base_fat = 15
WHERE id = 1;
""")

    _upgrade_expecting_refusal(
        database, "base_serving_grams", "base_calories", "500", "refuses")


def test_partial_base_columns_with_suspicious_nulls_are_refused(tmp_path):
    database = tmp_path / "null_partial.db"
    _execute_script(database, _LEGACY_NUTRITION_SQL)
    # A nullable column without the ADD COLUMN default cannot come from the
    # historical startup DDL.
    _execute_script(database, """
ALTER TABLE meal_entries ADD COLUMN base_serving_grams INTEGER;
UPDATE meal_entries SET base_serving_grams = NULL;
""")

    _upgrade_expecting_refusal(database, "base_serving_grams", "nullable")


def test_partial_base_columns_outside_historical_order_are_refused(tmp_path):
    database = tmp_path / "non_prefix.db"
    _execute_script(database, _LEGACY_NUTRITION_SQL)
    # base_calories alone is not a prefix of the historical add order.
    _execute_script(database, """
ALTER TABLE meal_entries ADD COLUMN base_calories FLOAT NOT NULL DEFAULT 0;
""")

    _upgrade_expecting_refusal(
        database, "base_calories", "base_serving_grams", "not a prefix")


def test_complete_base_columns_are_not_overwritten(tmp_path):
    database = tmp_path / "complete_bases.db"
    _upgrade(database)
    _execute_script(database, """
INSERT INTO users (telegram_id, first_name, created_at)
VALUES (101, 'Complete', '2026-01-01 00:00:00');
INSERT INTO meal_entries
    (id, user_id, day, meal_type, name, calories, protein, carbs, fat,
     ingredients, serving_grams, base_serving_grams, base_calories,
     base_protein, base_carbs, base_fat, created_at)
VALUES
    (1, 1, '2026-01-01', 'lunch', 'valid resized',
     1000, 40, 80, 20, '', 500, 250, 500, 20, 50, 15,
     '2026-01-01 00:00:00'),
    (2, 1, '2026-01-02', 'snack', 'zero calorie valid bases',
     0, 7, 8, 9, '', 100, 100, 0, 7, 8, 9, '2026-01-01 00:00:00');
""")

    # Re-run 0003 by stamping back to the legacy revision.
    _assert_ok(_run_alembic(_sqlite_url(database), "stamp", LEGACY_REVISION))
    _upgrade(database)

    rows = _query(
        database,
        "SELECT id, calories, protein, carbs, fat, serving_grams, "
        "base_serving_grams, base_calories, base_protein, base_carbs, base_fat "
        "FROM meal_entries ORDER BY id",
    )
    assert {row[0]: row for row in rows} == {
        1: (1, 1000, 40, 80, 20, 500, 250, 500, 20, 50, 15),
        2: (2, 0, 7, 8, 9, 100, 100, 0, 7, 8, 9),
    }


_USERS_LEGACY_DECLARATIONS = (
    ("id", "INTEGER NOT NULL PRIMARY KEY", "1"),
    ("telegram_id", "BIGINT NOT NULL", "101"),
    ("username", "VARCHAR(64)", None),
    ("first_name", "VARCHAR(128) NOT NULL", "'Legacy'"),
    ("last_name", "VARCHAR(128)", None),
    ("last_city", "VARCHAR(120)", None),
    ("created_at", "DATETIME NOT NULL", "'2026-01-01 00:00:00'"),
)


def _users_schema_script(missing):
    kept = [
        (name, declaration, value)
        for name, declaration, value in _USERS_LEGACY_DECLARATIONS
        if name not in missing
    ]
    definitions = ", ".join(f"{name} {declaration}"
                            for name, declaration, _ in kept)
    names = ", ".join(name for name, _, value in kept if value is not None)
    values = ", ".join(value for _, _, value in kept if value is not None)
    return f"""
CREATE TABLE users ({definitions});
INSERT INTO users ({names}) VALUES ({values});
CREATE TABLE sentinel (id INTEGER NOT NULL PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO sentinel (id, value) VALUES (1, 'keep-me');
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
INSERT INTO alembic_version VALUES ('0002_add_last_city');
"""


@pytest.mark.parametrize(
    "missing",
    [
        ("telegram_id",),
        ("created_at",),
        ("last_city",),
        ("telegram_id", "created_at"),
    ],
)
def test_missing_users_legacy_columns_are_refused(tmp_path, missing):
    database = tmp_path / "users_missing.db"
    _execute_script(database, _users_schema_script(set(missing)))

    result = _upgrade_expecting_refusal(database, *missing)

    assert "Cannot reconcile the users table" in (result.stdout + result.stderr)


def test_index_name_collision_on_another_table_is_refused(tmp_path):
    database = tmp_path / "index_collision.db"
    _execute_script(database, _LEGACY_NUTRITION_SQL)
    _execute_script(database, """
CREATE INDEX ix_community_foods_barcode ON notes (user_id);
""")

    _upgrade_expecting_refusal(
        database, "ix_community_foods_barcode", "notes")


def test_existing_index_with_wrong_structure_is_refused(tmp_path):
    database = tmp_path / "index_structure.db"
    _execute_script(database, _LEGACY_NUTRITION_SQL)
    _execute_script(database, """
DROP INDEX ix_meal_entries_day;
CREATE INDEX ix_meal_entries_day ON meal_entries (meal_type);
""")

    _upgrade_expecting_refusal(
        database, "ix_meal_entries_day", "meal_type", "day")


def test_existing_index_with_wrong_uniqueness_is_refused(tmp_path):
    database = tmp_path / "index_unique.db"
    _upgrade(database)
    _execute_script(database, """
DROP INDEX ix_community_foods_barcode;
CREATE INDEX ix_community_foods_barcode ON community_foods (barcode);
""")
    _assert_ok(_run_alembic(_sqlite_url(database), "stamp", LEGACY_REVISION))

    _upgrade_expecting_refusal(
        database, "ix_community_foods_barcode", "unique=False")


def test_unknown_indexes_are_not_dropped(tmp_path):
    database = tmp_path / "unknown_index.db"
    _execute_script(database, _LEGACY_NUTRITION_SQL)
    _execute_script(database, """
CREATE INDEX custom_owner_index ON notes (user_id);
""")

    _upgrade(database)

    assert _version(database) == HEAD
    assert "custom_owner_index" in _indexes(database)


# A frozen, hand-written description of the schema that `alembic upgrade head`
# must produce on an empty SQLite database. It is intentionally independent of
# `Base.metadata` and of the SQLAlchemy models: if a migration and the models
# drift apart, this test fails instead of silently accepting either side.
_EXPECTED_SCHEMA = {
    "users": {
        "columns": {
            "id": ("INTEGER", False, None),
            "telegram_id": ("BIGINT", False, None),
            "username": ("VARCHAR(64)", True, None),
            "first_name": ("VARCHAR(128)", False, None),
            "last_name": ("VARCHAR(128)", True, None),
            "last_city": ("VARCHAR(120)", True, None),
            "calorie_target": ("INTEGER", False, "2000"),
            "protein_target": ("INTEGER", False, "120"),
            "carbs_target": ("INTEGER", False, "250"),
            "fat_target": ("INTEGER", False, "70"),
            "created_at": ("DATETIME", False, None),
        },
        "indexes": {
            "ix_users_telegram_id": (("telegram_id",), True),
        },
    },
    "notes": {
        "columns": {
            "id": ("INTEGER", False, None),
            "user_id": ("INTEGER", False, None),
            "title": ("VARCHAR(200)", False, None),
            "content": ("TEXT", False, None),
            "created_at": ("DATETIME", False, None),
            "updated_at": ("DATETIME", False, None),
        },
        "indexes": {
            "ix_notes_user_id": (("user_id",), False),
            "ix_notes_user_updated": (("user_id", "updated_at"), False),
        },
    },
    "tasks": {
        "columns": {
            "id": ("INTEGER", False, None),
            "user_id": ("INTEGER", False, None),
            "title": ("VARCHAR(200)", False, None),
            "description": ("TEXT", False, None),
            "completed": ("BOOLEAN", False, None),
            "priority": ("VARCHAR(10)", False, None),
            "due_date": ("DATETIME", True, None),
            "created_at": ("DATETIME", False, None),
            "updated_at": ("DATETIME", False, None),
        },
        "indexes": {
            "ix_tasks_user_id": (("user_id",), False),
            "ix_tasks_user_completed": (("user_id", "completed"), False),
        },
    },
    "focus_sessions": {
        "columns": {
            "id": ("INTEGER", False, None),
            "user_id": ("INTEGER", False, None),
            "duration": ("INTEGER", False, None),
            "mode": ("VARCHAR(16)", False, None),
            "completed": ("BOOLEAN", False, None),
            "started_at": ("DATETIME", False, None),
            "finished_at": ("DATETIME", False, None),
            "client_id": ("VARCHAR(36)", True, None),
        },
        "indexes": {
            "ix_focus_sessions_user_id": (("user_id",), False),
            "ix_focus_user_finished": (("user_id", "finished_at"), False),
            "uq_focus_sessions_user_client": (("user_id", "client_id"), True),
        },
    },
    "meal_entries": {
        "columns": {
            "id": ("INTEGER", False, None),
            "user_id": ("INTEGER", False, None),
            "day": ("VARCHAR(10)", False, None),
            "meal_type": ("VARCHAR(16)", False, None),
            "name": ("VARCHAR(160)", False, None),
            "calories": ("INTEGER", False, None),
            "protein": ("DOUBLE", False, None),
            "carbs": ("DOUBLE", False, None),
            "fat": ("DOUBLE", False, None),
            "ingredients": ("TEXT", False, None),
            "serving_grams": ("INTEGER", False, "100"),
            "base_serving_grams": ("INTEGER", False, "100"),
            "base_calories": ("DOUBLE", False, "0"),
            "base_protein": ("DOUBLE", False, "0"),
            "base_carbs": ("DOUBLE", False, "0"),
            "base_fat": ("DOUBLE", False, "0"),
            "created_at": ("DATETIME", False, None),
        },
        "indexes": {
            "ix_meal_entries_day": (("day",), False),
            "ix_meal_entries_user_id": (("user_id",), False),
            "ix_meals_user_day": (("user_id", "day"), False),
        },
    },
    "saved_recipes": {
        "columns": {
            "id": ("INTEGER", False, None),
            "user_id": ("INTEGER", False, None),
            "name": ("VARCHAR(160)", False, None),
            "meal_type": ("VARCHAR(16)", False, None),
            "ingredients": ("JSON", False, None),
            "created_at": ("DATETIME", False, None),
        },
        "indexes": {
            "ix_saved_recipes_user_id": (("user_id",), False),
            "ix_recipes_user_name": (("user_id", "name"), False),
        },
    },
    "community_foods": {
        "columns": {
            "id": ("INTEGER", False, None),
            "barcode": ("VARCHAR(14)", False, None),
            "barcode_prefix": ("VARCHAR(7)", True, None),
            "name": ("VARCHAR(200)", False, None),
            "brand": ("VARCHAR(120)", False, None),
            "kcal": ("DOUBLE", False, None),
            "protein": ("DOUBLE", False, None),
            "carbs": ("DOUBLE", False, None),
            "fat": ("DOUBLE", False, None),
            "source": ("VARCHAR(32)", False, None),
            "submitted_by": ("INTEGER", False, None),
            "created_at": ("DATETIME", False, None),
        },
        "indexes": {
            "ix_community_foods_barcode": (("barcode",), True),
            "ix_community_foods_barcode_prefix": (("barcode_prefix",), False),
            "ix_community_foods_prefix": (("barcode_prefix",), False),
            "ix_community_foods_submitted_by": (("submitted_by",), False),
        },
    },
}


def _schema_snapshot(path):
    snapshot = {}
    for table in sorted(_tables(path)):
        if table in {"alembic_version", "sqlite_sequence"}:
            continue
        columns = {
            row[1]: (row[2], not bool(row[3]), row[4])
            for row in _query(path, f"PRAGMA table_info({table})")
        }
        indexes = {}
        for row in _query(path, f"PRAGMA index_list({table})"):
            name, unique = row[1], bool(row[2])
            if name.startswith("sqlite_autoindex"):
                continue
            indexes[name] = (
                tuple(info[2] for info in
                      _query(path, f"PRAGMA index_info({name})")),
                unique,
            )
        snapshot[table] = {"columns": columns, "indexes": indexes}
    return snapshot


def test_fresh_schema_matches_frozen_snapshot(tmp_path):
    database = tmp_path / "parity.db"

    _upgrade(database)

    actual = _schema_snapshot(database)
    assert set(actual) == set(_EXPECTED_SCHEMA)
    for table, expected in _EXPECTED_SCHEMA.items():
        assert actual[table]["columns"] == expected["columns"], table
        assert actual[table]["indexes"] == expected["indexes"], table


# ---------------------------------------------------------------------------
# Stage 4.4 - Focus idempotency key (0004_focus_session_client_id)
# ---------------------------------------------------------------------------


_LEGACY_FOCUS_SQL = _CORE_LEGACY_SQL + """
INSERT INTO focus_sessions
    (id, user_id, duration, mode, completed, started_at, finished_at)
VALUES
    (1, 1, 25, 'focus', 1, '2026-01-01 09:00:00', '2026-01-01 09:25:00'),
    (2, 1, 5, 'short_break', 1, '2026-01-01 10:00:00', '2026-01-01 10:05:00');
"""

_CLIENT_A = "11111111-1111-4111-8111-111111111111"
_CLIENT_B = "22222222-2222-4222-8222-222222222222"


def _seed_two_users(database):
    _execute_script(database, """
INSERT INTO users (id, telegram_id, first_name, created_at) VALUES
    (1, 201, 'One', '2026-01-01 00:00:00'),
    (2, 202, 'Two', '2026-01-01 00:00:00');
""")


def test_focus_legacy_sessions_survive_with_null_client_id(tmp_path):
    database = tmp_path / "focus_legacy.db"
    _execute_script(database, _LEGACY_FOCUS_SQL)

    _upgrade(database)

    assert _version(database) == HEAD
    assert "client_id" in _columns(database, "focus_sessions")
    assert _query(
        database,
        "SELECT id, duration, mode, client_id FROM focus_sessions ORDER BY id",
    ) == [
        (1, 25, "focus", None),
        (2, 5, "short_break", None),
    ]
    assert "uq_focus_sessions_user_client" in _indexes(database)


def test_focus_client_id_is_unique_per_user_only(tmp_path):
    database = tmp_path / "focus_unique.db"
    _upgrade(database)
    _seed_two_users(database)

    _execute_script(database, f"""
INSERT INTO focus_sessions
    (user_id, duration, mode, completed, started_at, finished_at, client_id)
VALUES
    (1, 25, 'focus', 1, '2026-01-01 09:00:00', '2026-01-01 09:25:00', '{_CLIENT_A}');
""")

    with pytest.raises(sqlite3.IntegrityError):
        _execute_script(database, f"""
INSERT INTO focus_sessions
    (user_id, duration, mode, completed, started_at, finished_at, client_id)
VALUES
    (1, 5, 'short_break', 1, '2026-01-01 10:00:00', '2026-01-01 10:05:00', '{_CLIENT_A}');
""")

    # Same UUID for another user, and any number of legacy NULL rows, are fine.
    _execute_script(database, f"""
INSERT INTO focus_sessions
    (user_id, duration, mode, completed, started_at, finished_at, client_id)
VALUES
    (2, 25, 'focus', 1, '2026-01-01 09:00:00', '2026-01-01 09:25:00', '{_CLIENT_A}'),
    (1, 25, 'focus', 1, '2026-01-02 09:00:00', '2026-01-02 09:25:00', NULL),
    (1, 25, 'focus', 1, '2026-01-03 09:00:00', '2026-01-03 09:25:00', NULL);
""")

    assert _query(
        database,
        "SELECT COUNT(*), COUNT(client_id) FROM focus_sessions",
    )[0] == (4, 2)


def test_focus_migration_can_be_reapplied(tmp_path):
    database = tmp_path / "focus_repeat.db"
    _upgrade(database)
    _seed_two_users(database)
    _execute_script(database, f"""
INSERT INTO focus_sessions
    (user_id, duration, mode, completed, started_at, finished_at, client_id)
VALUES
    (1, 25, 'focus', 1, '2026-01-01 09:00:00', '2026-01-01 09:25:00', '{_CLIENT_A}');
""")

    _assert_ok(_run_alembic(_sqlite_url(database), "stamp", LEGACY_REVISION))
    _upgrade(database)

    assert _version(database) == HEAD
    assert _query(
        database,
        "SELECT id, user_id, client_id FROM focus_sessions",
    ) == [(1, 1, _CLIENT_A)]
    assert "uq_focus_sessions_user_client" in _indexes(database)


def test_focus_downgrade_is_refused_and_keeps_data(tmp_path):
    database = tmp_path / "focus_downgrade.db"
    _upgrade(database)
    _seed_two_users(database)
    _execute_script(database, f"""
INSERT INTO focus_sessions
    (user_id, duration, mode, completed, started_at, finished_at, client_id)
VALUES
    (1, 25, 'focus', 1, '2026-01-01 09:00:00', '2026-01-01 09:25:00', '{_CLIENT_A}');
""")

    result = _run_alembic(_sqlite_url(database), "downgrade", "-1")

    assert result.returncode != 0
    assert "forward-only" in (result.stdout + result.stderr)
    assert _version(database) == HEAD
    assert _query(
        database,
        "SELECT user_id, client_id FROM focus_sessions",
    ) == [(1, _CLIENT_A)]
    assert "client_id" in _columns(database, "focus_sessions")
    assert "uq_focus_sessions_user_client" in _indexes(database)


def test_focus_wrong_index_structure_is_refused(tmp_path):
    database = tmp_path / "focus_bad_index.db"
    _upgrade(database)
    _seed_two_users(database)
    _execute_script(database, f"""
DROP INDEX uq_focus_sessions_user_client;
CREATE INDEX uq_focus_sessions_user_client
    ON focus_sessions (user_id, client_id);
INSERT INTO focus_sessions
    (user_id, duration, mode, completed, started_at, finished_at, client_id)
VALUES
    (1, 25, 'focus', 1, '2026-01-01 09:00:00', '2026-01-01 09:25:00', '{_CLIENT_A}');
""")
    _assert_ok(_run_alembic(_sqlite_url(database), "stamp", "0003_nutrition_schema"))

    before_schema = _schema_state(database)
    before_rows = _all_rows(database)
    result = _run_alembic(_sqlite_url(database), "upgrade", "head")

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "uq_focus_sessions_user_client" in output
    assert "unique=False" in output
    assert _version(database) == "0003_nutrition_schema"
    assert _schema_state(database) == before_schema
    assert _all_rows(database) == before_rows


def test_focus_duplicate_client_ids_are_refused(tmp_path):
    database = tmp_path / "focus_duplicates.db"
    _upgrade(database)
    _seed_two_users(database)
    _execute_script(database, f"""
DROP INDEX uq_focus_sessions_user_client;
INSERT INTO focus_sessions
    (user_id, duration, mode, completed, started_at, finished_at, client_id)
VALUES
    (1, 25, 'focus', 1, '2026-01-01 09:00:00', '2026-01-01 09:25:00', '{_CLIENT_A}'),
    (1, 25, 'focus', 1, '2026-01-02 09:00:00', '2026-01-02 09:25:00', '{_CLIENT_A}');
""")
    _assert_ok(_run_alembic(_sqlite_url(database), "stamp", "0003_nutrition_schema"))

    before_rows = _all_rows(database)
    result = _run_alembic(_sqlite_url(database), "upgrade", "head")

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "duplicate" in output.lower()
    assert _version(database) == "0003_nutrition_schema"
    assert "uq_focus_sessions_user_client" not in _indexes(database)
    assert _all_rows(database) == before_rows


# ---------------------------------------------------------------------------
# PostgreSQL verification (gated by TEST_POSTGRES_URL)
#
# These tests use one disposable database and create a uniquely named schema
# per test. Every setup statement and every Alembic run is scoped to that
# schema through ``search_path``, so ``public`` and any pre-existing object are
# never touched. Each test drops only its own schema.
# ---------------------------------------------------------------------------


def _postgres_url():
    raw_url = os.environ.get("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("TEST_POSTGRES_URL is not set")

    from sqlalchemy.engine import make_url

    url = make_url(raw_url)
    if "test" not in (url.database or "").lower():
        pytest.skip("Refusing to run against a database name without 'test'")
    driver = url.drivername.split("+")[-1] if "+" in url.drivername else "psycopg2"
    pytest.importorskip(driver)
    return url


@pytest.fixture
def postgres_schema():
    """Provide (url, schema, engine) for one isolated PostgreSQL test."""
    from sqlalchemy import create_engine, text

    url = _postgres_url()
    engine = create_engine(url)
    schema = f"migration_test_{uuid.uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield url, schema, engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def _pg_scoped_url(url, schema):
    from sqlalchemy.engine import make_url

    parsed = make_url(str(url))
    return parsed.set(query={
        **dict(parsed.query),
        "options": f"-csearch_path={schema}",
    })


def _pg_run(url, schema, *args):
    return _run_alembic(str(_pg_scoped_url(url, schema)), *args)


def _pg_exec(engine, schema, *statements):
    from sqlalchemy import text

    with engine.begin() as connection:
        connection.execute(text(f'SET search_path TO "{schema}"'))
        for statement in statements:
            connection.execute(text(statement))


def _pg_query_all(engine, schema, sql, parameters=None):
    from sqlalchemy import text

    with engine.connect() as connection:
        connection.execute(text(f'SET search_path TO "{schema}"'))
        return [
            tuple(row)
            for row in connection.execute(text(sql), parameters or {})
        ]


def _pg_query_one(engine, schema, sql, parameters=None):
    from sqlalchemy import text

    with engine.connect() as connection:
        connection.execute(text(f'SET search_path TO "{schema}"'))
        return connection.execute(text(sql), parameters or {}).one()


def _pg_scalar(engine, schema, sql, parameters=None):
    from sqlalchemy import text

    with engine.connect() as connection:
        connection.execute(text(f'SET search_path TO "{schema}"'))
        return connection.execute(text(sql), parameters or {}).scalar_one()


def _pg_tables(engine, schema):
    from sqlalchemy import inspect

    return set(inspect(engine).get_table_names(schema=schema))


def _pg_columns(engine, schema, table):
    from sqlalchemy import inspect

    return {column["name"] for column in
            inspect(engine).get_columns(table, schema=schema)}


def _pg_indexes(engine, schema, table):
    from sqlalchemy import inspect

    return {
        index["name"]: (tuple(index.get("column_names") or ()),
                        bool(index.get("unique")))
        for index in inspect(engine).get_indexes(table, schema=schema)
    }


def _pg_schema_state(engine, schema):
    """Tables, columns and indexes, used to prove that a refusal did nothing."""
    return {
        table: (
            tuple(sorted(_pg_columns(engine, schema, table))),
            tuple(sorted(_pg_indexes(engine, schema, table))),
        )
        for table in sorted(_pg_tables(engine, schema))
    }


def _pg_data_state(engine, schema):
    return {
        table: _pg_query_all(engine, schema, f"SELECT * FROM {table} ORDER BY 1")
        for table in sorted(_pg_tables(engine, schema))
        if table != "alembic_version"
    }


def _pg_version(engine, schema):
    return _pg_scalar(engine, schema, "SELECT version_num FROM alembic_version")


def _pg_stamp_legacy(engine, schema, *statements):
    _pg_exec(
        engine,
        schema,
        "CREATE TABLE alembic_version "
        "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)",
        "INSERT INTO alembic_version VALUES ('0002_add_last_city')",
        *statements,
    )


def _pg_upgrade_expecting_refusal(engine, schema, url, *fragments):
    before_schema = _pg_schema_state(engine, schema)
    before_rows = _pg_data_state(engine, schema)

    result = _pg_run(url, schema, "upgrade", "head")

    assert result.returncode != 0, f"migration unexpectedly succeeded:\n{result.stdout}"
    output = result.stdout + result.stderr
    for fragment in fragments:
        assert fragment in output, f"{fragment!r} not found in:\n{output}"
    assert _pg_version(engine, schema) == LEGACY_REVISION
    assert _pg_schema_state(engine, schema) == before_schema
    assert _pg_data_state(engine, schema) == before_rows
    return result


_PG_USERS_STATEMENTS = (
    """
    CREATE TABLE users (
        id SERIAL PRIMARY KEY,
        telegram_id BIGINT NOT NULL,
        username VARCHAR(64),
        first_name VARCHAR(128) NOT NULL,
        last_name VARCHAR(128),
        last_city VARCHAR(120),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id)",
    "INSERT INTO users (id, telegram_id, first_name, created_at) "
    "VALUES (1, 101, 'Legacy', NOW())",
)

_PG_NUTRITION_STATEMENTS = (
    """
    CREATE TABLE meal_entries (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day VARCHAR(10) NOT NULL,
        meal_type VARCHAR(16) NOT NULL,
        name VARCHAR(160) NOT NULL,
        calories INTEGER NOT NULL,
        protein DOUBLE PRECISION NOT NULL,
        carbs DOUBLE PRECISION NOT NULL,
        fat DOUBLE PRECISION NOT NULL,
        ingredients TEXT NOT NULL,
        serving_grams INTEGER NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL
    )
    """,
    "CREATE INDEX ix_meal_entries_day ON meal_entries (day)",
    "CREATE INDEX ix_meal_entries_user_id ON meal_entries (user_id)",
    "CREATE INDEX ix_meals_user_day ON meal_entries (user_id, day)",
    """
    CREATE TABLE saved_recipes (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(160) NOT NULL,
        meal_type VARCHAR(16) NOT NULL,
        ingredients JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL
    )
    """,
    "CREATE INDEX ix_saved_recipes_user_id ON saved_recipes (user_id)",
    "CREATE INDEX ix_recipes_user_name ON saved_recipes (user_id, name)",
    "INSERT INTO meal_entries "
    "(id, user_id, day, meal_type, name, calories, protein, carbs, fat, "
    "ingredients, serving_grams, created_at) VALUES "
    "(1, 1, '2026-01-01', 'lunch', 'normal meal', 500, 20, 50, 15, '', 250, NOW()), "
    "(2, 1, '2026-01-01', 'snack', 'zero calorie macros', 0, 7, 8, 9, '', 100, NOW()), "
    "(3, 1, '2026-01-02', 'dinner', 'all zero', 0, 0, 0, 0, '', 100, NOW())",
)

_PG_FOCUS_STATEMENTS = (
    """
    CREATE TABLE focus_sessions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        duration INTEGER NOT NULL,
        mode VARCHAR(16) NOT NULL,
        completed BOOLEAN NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE NOT NULL
    )
    """,
    "CREATE INDEX ix_focus_sessions_user_id ON focus_sessions (user_id)",
    "CREATE INDEX ix_focus_user_finished "
    "ON focus_sessions (user_id, finished_at)",
    "INSERT INTO focus_sessions "
    "(user_id, duration, mode, completed, started_at, finished_at) VALUES "
    "(1, 25, 'focus', true, NOW() - INTERVAL '25 minutes', NOW()), "
    "(1, 5, 'short_break', true, NOW() - INTERVAL '5 minutes', NOW())",
)

_PG_CLIENT = "33333333-3333-4333-8333-333333333333"

_PG_MEAL_COLUMNS = (
    "SELECT id, calories, protein, carbs, fat, serving_grams, "
    "base_serving_grams, base_calories, base_protein, base_carbs, base_fat "
    "FROM meal_entries ORDER BY id"
)

# A frozen, hand-written description of the PostgreSQL schema that
# `alembic upgrade head` must produce on an empty database. It is independent
# of `Base.metadata` and the ORM models, exactly like the SQLite snapshot.
_PG_EXPECTED_SCHEMA = {
    "users": {
        "columns": {
            "id": ("INTEGER", False, "<sequence>"),
            "telegram_id": ("BIGINT", False, None),
            "username": ("VARCHAR(64)", True, None),
            "first_name": ("VARCHAR(128)", False, None),
            "last_name": ("VARCHAR(128)", True, None),
            "last_city": ("VARCHAR(120)", True, None),
            "calorie_target": ("INTEGER", False, "2000"),
            "protein_target": ("INTEGER", False, "120"),
            "carbs_target": ("INTEGER", False, "250"),
            "fat_target": ("INTEGER", False, "70"),
            "created_at": ("TIMESTAMP WITH TIME ZONE", False, None),
        },
        "indexes": {
            "ix_users_telegram_id": (("telegram_id",), True),
        },
    },
    "notes": {
        "columns": {
            "id": ("INTEGER", False, "<sequence>"),
            "user_id": ("INTEGER", False, None),
            "title": ("VARCHAR(200)", False, None),
            "content": ("TEXT", False, None),
            "created_at": ("TIMESTAMP WITH TIME ZONE", False, None),
            "updated_at": ("TIMESTAMP WITH TIME ZONE", False, None),
        },
        "indexes": {
            "ix_notes_user_id": (("user_id",), False),
            "ix_notes_user_updated": (("user_id", "updated_at"), False),
        },
    },
    "tasks": {
        "columns": {
            "id": ("INTEGER", False, "<sequence>"),
            "user_id": ("INTEGER", False, None),
            "title": ("VARCHAR(200)", False, None),
            "description": ("TEXT", False, None),
            "completed": ("BOOLEAN", False, None),
            "priority": ("VARCHAR(10)", False, None),
            "due_date": ("TIMESTAMP WITH TIME ZONE", True, None),
            "created_at": ("TIMESTAMP WITH TIME ZONE", False, None),
            "updated_at": ("TIMESTAMP WITH TIME ZONE", False, None),
        },
        "indexes": {
            "ix_tasks_user_id": (("user_id",), False),
            "ix_tasks_user_completed": (("user_id", "completed"), False),
        },
    },
    "focus_sessions": {
        "columns": {
            "id": ("INTEGER", False, "<sequence>"),
            "user_id": ("INTEGER", False, None),
            "duration": ("INTEGER", False, None),
            "mode": ("VARCHAR(16)", False, None),
            "completed": ("BOOLEAN", False, None),
            "started_at": ("TIMESTAMP WITH TIME ZONE", False, None),
            "finished_at": ("TIMESTAMP WITH TIME ZONE", False, None),
            "client_id": ("VARCHAR(36)", True, None),
        },
        "indexes": {
            "ix_focus_sessions_user_id": (("user_id",), False),
            "ix_focus_user_finished": (("user_id", "finished_at"), False),
            "uq_focus_sessions_user_client": (("user_id", "client_id"), True),
        },
    },
    "meal_entries": {
        "columns": {
            "id": ("INTEGER", False, "<sequence>"),
            "user_id": ("INTEGER", False, None),
            "day": ("VARCHAR(10)", False, None),
            "meal_type": ("VARCHAR(16)", False, None),
            "name": ("VARCHAR(160)", False, None),
            "calories": ("INTEGER", False, None),
            "protein": ("DOUBLE PRECISION", False, None),
            "carbs": ("DOUBLE PRECISION", False, None),
            "fat": ("DOUBLE PRECISION", False, None),
            "ingredients": ("TEXT", False, None),
            "serving_grams": ("INTEGER", False, "100"),
            "base_serving_grams": ("INTEGER", False, "100"),
            "base_calories": ("DOUBLE PRECISION", False, "0"),
            "base_protein": ("DOUBLE PRECISION", False, "0"),
            "base_carbs": ("DOUBLE PRECISION", False, "0"),
            "base_fat": ("DOUBLE PRECISION", False, "0"),
            "created_at": ("TIMESTAMP WITH TIME ZONE", False, None),
        },
        "indexes": {
            "ix_meal_entries_day": (("day",), False),
            "ix_meal_entries_user_id": (("user_id",), False),
            "ix_meals_user_day": (("user_id", "day"), False),
        },
    },
    "saved_recipes": {
        "columns": {
            "id": ("INTEGER", False, "<sequence>"),
            "user_id": ("INTEGER", False, None),
            "name": ("VARCHAR(160)", False, None),
            "meal_type": ("VARCHAR(16)", False, None),
            "ingredients": ("JSON", False, None),
            "created_at": ("TIMESTAMP WITH TIME ZONE", False, None),
        },
        "indexes": {
            "ix_saved_recipes_user_id": (("user_id",), False),
            "ix_recipes_user_name": (("user_id", "name"), False),
        },
    },
    "community_foods": {
        "columns": {
            "id": ("INTEGER", False, "<sequence>"),
            "barcode": ("VARCHAR(14)", False, None),
            "barcode_prefix": ("VARCHAR(7)", True, None),
            "name": ("VARCHAR(200)", False, None),
            "brand": ("VARCHAR(120)", False, None),
            "kcal": ("DOUBLE PRECISION", False, None),
            "protein": ("DOUBLE PRECISION", False, None),
            "carbs": ("DOUBLE PRECISION", False, None),
            "fat": ("DOUBLE PRECISION", False, None),
            "source": ("VARCHAR(32)", False, None),
            "submitted_by": ("INTEGER", False, None),
            "created_at": ("TIMESTAMP WITH TIME ZONE", False, None),
        },
        "indexes": {
            "ix_community_foods_barcode": (("barcode",), True),
            "ix_community_foods_barcode_prefix": (("barcode_prefix",), False),
            "ix_community_foods_prefix": (("barcode_prefix",), False),
            "ix_community_foods_submitted_by": (("submitted_by",), False),
        },
    },
}


def _pg_normalize_default(default):
    if default is None:
        return None
    if default.startswith("nextval("):
        return "<sequence>"
    return default


def _pg_schema_snapshot(engine, schema):
    from sqlalchemy import DateTime, inspect

    inspector = inspect(engine)
    snapshot = {}
    for table in inspector.get_table_names(schema=schema):
        if table == "alembic_version":
            continue
        columns = {}
        for column in inspector.get_columns(table, schema=schema):
            type_name = str(column["type"])
            if isinstance(column["type"], DateTime) and column["type"].timezone:
                type_name += " WITH TIME ZONE"
            columns[column["name"]] = (
                type_name,
                bool(column["nullable"]),
                _pg_normalize_default(column["default"]),
            )
        snapshot[table] = {
            "columns": columns,
            "indexes": _pg_indexes(engine, schema, table),
        }
    return snapshot


def test_postgres_fresh_upgrade_matches_frozen_snapshot(postgres_schema):
    url, schema, engine = postgres_schema

    result = _pg_run(url, schema, "upgrade", "head")
    _assert_ok(result)

    assert _pg_version(engine, schema) == HEAD
    actual = _pg_schema_snapshot(engine, schema)
    assert set(actual) == set(_PG_EXPECTED_SCHEMA)
    for table, expected in _PG_EXPECTED_SCHEMA.items():
        assert actual[table]["columns"] == expected["columns"], table
        assert actual[table]["indexes"] == expected["indexes"], table


def test_postgres_legacy_migration_preserves_data(postgres_schema):
    """Legacy 0002 with Nutrition tables and user data is reconciled in place."""
    url, schema, engine = postgres_schema
    _pg_stamp_legacy(engine, schema, *_PG_USERS_STATEMENTS,
                     *_PG_FOCUS_STATEMENTS, *_PG_NUTRITION_STATEMENTS)

    result = _pg_run(url, schema, "upgrade", "head")
    _assert_ok(result)

    assert _pg_version(engine, schema) == HEAD
    assert _pg_query_one(engine, schema,
                         "SELECT calorie_target, protein_target, carbs_target, "
                         "fat_target FROM users WHERE id = 1") == (2000, 120, 250, 70)
    rows = _pg_query_all(engine, schema, _PG_MEAL_COLUMNS)
    assert {row[0]: row for row in rows} == {
        1: (1, 500, 20, 50, 15, 250, 250, 500, 20, 50, 15),
        2: (2, 0, 7, 8, 9, 100, 100, 0, 7, 8, 9),
        3: (3, 0, 0, 0, 0, 100, 100, 0, 0, 0, 0),
    }
    assert _pg_scalar(engine, schema,
                      "SELECT COUNT(*) FROM community_foods") == 0
    assert _pg_indexes(engine, schema, "community_foods")[
        "ix_community_foods_barcode"][1] is True


def test_postgres_legacy_without_nutrition_creates_tables(postgres_schema):
    url, schema, engine = postgres_schema
    _pg_stamp_legacy(engine, schema, *_PG_USERS_STATEMENTS,
                     *_PG_FOCUS_STATEMENTS)

    result = _pg_run(url, schema, "upgrade", "head")
    _assert_ok(result)

    assert _pg_version(engine, schema) == HEAD
    assert {"meal_entries", "saved_recipes", "community_foods"} <= _pg_tables(
        engine, schema)
    assert _pg_query_one(engine, schema,
                         "SELECT telegram_id, first_name FROM users WHERE id = 1"
                         ) == (101, "Legacy")
    assert _pg_query_one(engine, schema,
                         "SELECT calorie_target, protein_target, carbs_target, "
                         "fat_target FROM users WHERE id = 1") == (2000, 120, 250, 70)
    assert _pg_scalar(engine, schema,
                      "SELECT COUNT(*) FROM meal_entries") == 0


def test_postgres_interrupted_ddl_prefix_is_reconstructed(postgres_schema):
    url, schema, engine = postgres_schema
    _pg_stamp_legacy(
        engine, schema, *_PG_USERS_STATEMENTS, *_PG_FOCUS_STATEMENTS,
        *_PG_NUTRITION_STATEMENTS,
        "ALTER TABLE meal_entries "
        "ADD COLUMN base_serving_grams INTEGER NOT NULL DEFAULT 100",
    )

    result = _pg_run(url, schema, "upgrade", "head")
    _assert_ok(result)

    assert _pg_version(engine, schema) == HEAD
    rows = _pg_query_all(engine, schema, _PG_MEAL_COLUMNS)
    assert {row[0]: row for row in rows} == {
        1: (1, 500, 20, 50, 15, 250, 250, 500, 20, 50, 15),
        2: (2, 0, 7, 8, 9, 100, 100, 0, 7, 8, 9),
        3: (3, 0, 0, 0, 0, 100, 100, 0, 0, 0, 0),
    }


def test_postgres_suspicious_partial_base_is_refused_without_ddl(postgres_schema):
    url, schema, engine = postgres_schema
    _pg_stamp_legacy(
        engine, schema, *_PG_USERS_STATEMENTS, *_PG_NUTRITION_STATEMENTS,
        "ALTER TABLE meal_entries "
        "ADD COLUMN base_calories DOUBLE PRECISION NOT NULL DEFAULT 0",
        "ALTER TABLE meal_entries "
        "ADD COLUMN base_protein DOUBLE PRECISION NOT NULL DEFAULT 0",
        "ALTER TABLE meal_entries "
        "ADD COLUMN base_carbs DOUBLE PRECISION NOT NULL DEFAULT 0",
        "ALTER TABLE meal_entries "
        "ADD COLUMN base_fat DOUBLE PRECISION NOT NULL DEFAULT 0",
        "UPDATE meal_entries SET base_calories = 500, base_protein = 20 "
        "WHERE id = 1",
    )

    _pg_upgrade_expecting_refusal(
        engine, schema, url, "base_serving_grams", "base_calories", "500",
        "refuses")
    # No DDL from `_ensure_users` may have survived the refusal.
    assert "calorie_target" not in _pg_columns(engine, schema, "users")
    assert "base_serving_grams" not in _pg_columns(
        engine, schema, "meal_entries")


def test_postgres_missing_users_columns_are_refused(postgres_schema):
    url, schema, engine = postgres_schema
    _pg_exec(
        engine,
        schema,
        "CREATE TABLE users ("
        "id SERIAL PRIMARY KEY, first_name VARCHAR(128) NOT NULL, "
        "last_city VARCHAR(120), "
        "created_at TIMESTAMP WITH TIME ZONE NOT NULL)",
        "INSERT INTO users (id, first_name, created_at) "
        "VALUES (1, 'Broken', NOW())",
        "CREATE TABLE sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)",
        "INSERT INTO sentinel VALUES (1, 'keep-me')",
        "CREATE TABLE alembic_version "
        "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)",
        "INSERT INTO alembic_version VALUES ('0002_add_last_city')",
    )

    _pg_upgrade_expecting_refusal(
        engine, schema, url, "Cannot reconcile the users table", "telegram_id")
    assert _pg_scalar(engine, schema,
                      "SELECT value FROM sentinel WHERE id = 1") == "keep-me"


def test_postgres_index_name_collision_is_refused(postgres_schema):
    url, schema, engine = postgres_schema
    _pg_stamp_legacy(
        engine, schema, *_PG_USERS_STATEMENTS, *_PG_NUTRITION_STATEMENTS,
        "CREATE TABLE sentinel (id INTEGER PRIMARY KEY)",
        "CREATE INDEX ix_community_foods_barcode ON sentinel (id)",
    )

    _pg_upgrade_expecting_refusal(
        engine, schema, url, "ix_community_foods_barcode", "sentinel")


def test_postgres_existing_index_with_wrong_structure_is_refused(postgres_schema):
    url, schema, engine = postgres_schema
    _pg_stamp_legacy(
        engine, schema, *_PG_USERS_STATEMENTS, *_PG_NUTRITION_STATEMENTS,
        "DROP INDEX ix_meal_entries_day",
        "CREATE INDEX ix_meal_entries_day ON meal_entries (meal_type)",
    )

    _pg_upgrade_expecting_refusal(
        engine, schema, url, "ix_meal_entries_day", "meal_type", "day")


def test_postgres_repeated_upgrade_is_noop(postgres_schema):
    url, schema, engine = postgres_schema
    _assert_ok(_pg_run(url, schema, "upgrade", "head"))
    _pg_exec(
        engine,
        schema,
        "INSERT INTO users (id, telegram_id, first_name, created_at) "
        "VALUES (1, 101, 'Repeat', NOW())",
        "INSERT INTO meal_entries "
        "(id, user_id, day, meal_type, name, calories, protein, carbs, fat, "
        "ingredients, serving_grams, base_serving_grams, base_calories, "
        "base_protein, base_carbs, base_fat, created_at) VALUES "
        "(1, 1, '2026-01-01', 'lunch', 'resized', 1000, 40, 80, 20, '', "
        "500, 250, 500, 20, 50, 15, NOW())",
    )

    _assert_ok(_pg_run(url, schema, "upgrade", "head"))
    _assert_ok(_pg_run(url, schema, "stamp", LEGACY_REVISION))
    _assert_ok(_pg_run(url, schema, "upgrade", "head"))

    assert _pg_version(engine, schema) == HEAD
    assert _pg_query_one(engine, schema, _PG_MEAL_COLUMNS) == (
        1, 1000, 40, 80, 20, 500, 250, 500, 20, 50, 15)


def test_postgres_downgrade_is_refused(postgres_schema):
    url, schema, engine = postgres_schema
    _assert_ok(_pg_run(url, schema, "upgrade", "head"))
    _pg_exec(
        engine,
        schema,
        "INSERT INTO users (id, telegram_id, first_name, created_at) "
        "VALUES (1, 101, 'Keep', NOW())",
        "INSERT INTO focus_sessions "
        "(user_id, duration, mode, completed, started_at, finished_at, client_id) "
        f"VALUES (1, 25, 'focus', true, NOW(), NOW(), '{_PG_CLIENT}')",
    )

    result = _pg_run(url, schema, "downgrade", "-1")

    assert result.returncode != 0
    assert "forward-only" in (result.stdout + result.stderr)
    assert _pg_version(engine, schema) == HEAD
    assert {"users", "meal_entries", "saved_recipes", "community_foods"} <= \
        _pg_tables(engine, schema)
    assert _pg_query_one(
        engine, schema,
        "SELECT user_id, client_id FROM focus_sessions",
    ) == (1, _PG_CLIENT)
    assert _pg_indexes(engine, schema, "focus_sessions")[
        "uq_focus_sessions_user_client"] == (("user_id", "client_id"), True)


def test_postgres_focus_legacy_sessions_get_null_client_id(postgres_schema):
    url, schema, engine = postgres_schema
    _pg_stamp_legacy(engine, schema, *_PG_USERS_STATEMENTS, *_PG_FOCUS_STATEMENTS)

    _assert_ok(_pg_run(url, schema, "upgrade", "head"))

    assert _pg_version(engine, schema) == HEAD
    assert _pg_query_all(
        engine, schema,
        "SELECT id, duration, mode, client_id FROM focus_sessions ORDER BY id",
    ) == [(1, 25, "focus", None), (2, 5, "short_break", None)]
    assert _pg_indexes(engine, schema, "focus_sessions")[
        "uq_focus_sessions_user_client"] == (("user_id", "client_id"), True)

    # The composite index is unique per user and allows many NULL rows.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        _pg_exec(
            engine,
            schema,
            "INSERT INTO focus_sessions "
            "(user_id, duration, mode, completed, started_at, finished_at, client_id) "
            f"VALUES (1, 25, 'focus', true, NOW(), NOW(), '{_PG_CLIENT}')",
            "INSERT INTO focus_sessions "
            "(user_id, duration, mode, completed, started_at, finished_at, client_id) "
            f"VALUES (1, 5, 'short_break', true, NOW(), NOW(), '{_PG_CLIENT}')",
        )

    _pg_exec(
        engine,
        schema,
        "INSERT INTO users (id, telegram_id, first_name, created_at) "
        "VALUES (2, 202, 'Two', NOW())",
        "INSERT INTO focus_sessions "
        "(user_id, duration, mode, completed, started_at, finished_at, client_id) "
        f"VALUES (2, 25, 'focus', true, NOW(), NOW(), '{_PG_CLIENT}')",
        "INSERT INTO focus_sessions "
        "(user_id, duration, mode, completed, started_at, finished_at, client_id) "
        "VALUES (1, 25, 'focus', true, NOW(), NOW(), NULL)",
        "INSERT INTO focus_sessions "
        "(user_id, duration, mode, completed, started_at, finished_at, client_id) "
        "VALUES (1, 25, 'focus', true, NOW(), NOW(), NULL)",
    )

    assert _pg_scalar(engine, schema, "SELECT COUNT(*) FROM focus_sessions") == 5
    assert _pg_scalar(
        engine, schema,
        "SELECT COUNT(*) FROM focus_sessions WHERE client_id = :client",
        {"client": _PG_CLIENT},
    ) == 1


def test_postgres_mid_migration_failure_rolls_back_ddl(postgres_schema):
    """PostgreSQL DDL is transactional: a failure after partial DDL rolls back."""
    url, schema, engine = postgres_schema
    _pg_stamp_legacy(
        engine, schema, *_PG_USERS_STATEMENTS, *_PG_FOCUS_STATEMENTS,
        *_PG_NUTRITION_STATEMENTS,
        "CREATE VIEW community_foods AS SELECT id, first_name FROM users",
    )

    failed = _pg_run(url, schema, "upgrade", "head")

    assert failed.returncode != 0
    assert "community_foods" in (failed.stdout + failed.stderr)
    # Alembic wrote no new revision and every earlier DDL was rolled back.
    assert _pg_version(engine, schema) == LEGACY_REVISION
    assert "calorie_target" not in _pg_columns(engine, schema, "users")
    assert "base_serving_grams" not in _pg_columns(
        engine, schema, "meal_entries")
    assert _pg_query_all(
        engine, schema,
        "SELECT id, calories, protein, carbs, fat, serving_grams "
        "FROM meal_entries ORDER BY id") == [
        (1, 500, 20, 50, 15, 250),
        (2, 0, 7, 8, 9, 100),
        (3, 0, 0, 0, 0, 100),
    ]

    # After removing the conflicting view a retry completes normally.
    _pg_exec(engine, schema, "DROP VIEW community_foods")
    _assert_ok(_pg_run(url, schema, "upgrade", "head"))

    assert _pg_version(engine, schema) == HEAD
    rows = _pg_query_all(engine, schema, _PG_MEAL_COLUMNS)
    assert {row[0]: row for row in rows} == {
        1: (1, 500, 20, 50, 15, 250, 250, 500, 20, 50, 15),
        2: (2, 0, 7, 8, 9, 100, 100, 0, 7, 8, 9),
        3: (3, 0, 0, 0, 0, 100, 100, 0, 0, 0, 0),
    }
