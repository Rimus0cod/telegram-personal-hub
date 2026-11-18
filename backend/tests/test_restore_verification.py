"""Hermetic tests for the schema-aware restore verification.

These tests run the real ``scripts/restore-postgres.sh`` against small fake
libpq tools. They verify the script's exit codes and verification branches
without needing a PostgreSQL server. Real PostgreSQL integration coverage lives
in ``test_backup_restore.py``.
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
RESTORE = REPO_ROOT / "scripts" / "restore-postgres.sh"

R0002 = "0002_add_last_city"
R0003 = "0003_nutrition_schema"
R0004 = "0004_focus_session_client_id"
R0005 = "0005_catalog_foods"
R0006 = "0006_global_catalog"
R0007 = "0007_catalog_cleanup"
R0008 = "0008_multilingual_search"
R0009 = "0009_recipe_composition"

BASE_TABLES = "users notes tasks focus_sessions alembic_version"
NUTRITION_TABLES = f"{BASE_TABLES} meal_entries saved_recipes community_foods"
CATALOG_TABLES = f"{NUTRITION_TABLES} catalog_foods"
GLOBAL_TABLES = (
    f"{CATALOG_TABLES} food_sources food_translations food_aliases "
    "nutrient_profiles food_servings food_barcodes recipe_ingredients"
)
BASE_INDEXES = (
    "users:ix_users_telegram_id notes:ix_notes_user_updated "
    "tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished"
)
NUTRITION_INDEXES = (
    f"{BASE_INDEXES} meal_entries:ix_meals_user_day "
    "saved_recipes:ix_recipes_user_name community_foods:ix_community_foods_barcode"
)
CATALOG_INDEXES = (
    f"{NUTRITION_INDEXES} catalog_foods:uq_catalog_foods_source_source_id"
)
GLOBAL_INDEXES = (
    f"{CATALOG_INDEXES} food_translations:uq_food_translations_food_language "
    "nutrient_profiles:ix_nutrient_profiles_primary "
    "food_barcodes:uq_food_barcodes_barcode"
)
# 0007 drops the duplicate food_id indexes and changes no table or other index.
CLEANUP_TABLES = GLOBAL_TABLES
CLEANUP_INDEXES = GLOBAL_INDEXES
# 0008 adds the derived search index and normalized community names.
SEARCH_TABLES = f"{CLEANUP_TABLES} food_search_terms"
SEARCH_INDEXES = (
    f"{CLEANUP_INDEXES} community_foods:ix_community_foods_normalized "
    "food_search_terms:uq_food_search_terms "
    "food_search_terms:ix_food_search_terms_normalized"
)
RECIPE_TABLES = f"{SEARCH_TABLES} recipe_details"
FOCUS_INDEX = "focus_sessions:uq_focus_sessions_user_client"


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = path.resolve()
    return "/" + resolved.drive[0].lower() + resolved.as_posix()[2:]


def _bash() -> str:
    bash = shutil.which("bash")
    if os.name == "nt":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.exists():
            bash = str(git_bash)
    if not bash:
        pytest.skip("bash is required")
    return bash


FAKE_TOOL = r'''
import os
import re
import sys

tool = sys.argv[1]
args = sys.argv[2:]
sql = " ".join(args)
marker = os.environ.get("FAKE_MARKER")


def mark(value):
    if marker:
        with open(marker, "a", encoding="utf-8") as handle:
            handle.write(value + "\n")


if tool == "createdb":
    mark("createdb")
    sys.exit(0)

if tool == "pg_restore":
    if "--list" in args:
        sys.exit(1 if os.environ.get("FAKE_BAD_ARCHIVE") == "1" else 0)
    if os.environ.get("FAKE_RESTORE_FAIL") == "1":
        mark("restore_failed")
        print("fake pg_restore failure", file=sys.stderr)
        sys.exit(1)
    mark("restore")
    sys.exit(0)

if tool == "psql":
    if "FROM pg_database" in sql and "datname" in sql:
        print("1" if os.environ.get("FAKE_TARGET_EXISTS") == "1" else "")
        sys.exit(0)
    if "version_num FROM alembic_version" in sql:
        if os.environ.get("FAKE_MISSING_REVISION") == "1":
            print('relation "alembic_version" does not exist', file=sys.stderr)
            sys.exit(1)
        print(os.environ.get("FAKE_HEAD", ""))
        sys.exit(0)
    match = re.search(r"to_regclass\('public\.([^']+)'\)", sql)
    if match:
        tables = set(os.environ.get("FAKE_TABLES", "").split())
        print("t" if match.group(1) in tables else "f")
        sys.exit(0)
    match = re.search(r"tablename='([^']+)' AND indexname='([^']+)'", sql)
    if match:
        indexes = set(os.environ.get("FAKE_INDEXES", "").split())
        print("1" if f"{match.group(1)}:{match.group(2)}" in indexes else "0")
        sys.exit(0)
    print("")
    sys.exit(0)

print(f"unexpected fake tool: {tool}", file=sys.stderr)
sys.exit(2)
'''


@pytest.fixture
def fake_restore(tmp_path):
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    fake_tool = tmp_path / "fake_tool.py"
    fake_tool.write_text(FAKE_TOOL, encoding="utf-8", newline="\n")
    python = _bash_path(Path(sys.executable))
    fake_tool_path = _bash_path(fake_tool)
    for tool in ("psql", "createdb", "pg_restore"):
        wrapper = tool_dir / tool
        wrapper.write_text(
            f'#!/bin/sh\nexec "{python}" "{fake_tool_path}" {tool} "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"not-a-real-dump-but-the-fake-pg_restore-accepts-it")
    marker = tmp_path / "marker.txt"
    return {
        "tool_dir": tool_dir,
        "backup": backup,
        "marker": marker,
        "tmp_path": tmp_path,
    }


def _env(fake, **overrides):
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{_bash_path(fake['tool_dir'])}:{env.get('PATH', '')}",
            "FAKE_MARKER": str(fake["marker"]),
            "PGHOST": "127.0.0.1",
            "PGPORT": "5432",
            "PGUSER": "stage55",
            "PGDATABASE": "postgres",
            "RESTORE_CONFIRM": "stage55_restore",
            "PGMAINTENANCE_DB": "postgres",
            "FAKE_HEAD": R0009,
            "FAKE_TABLES": RECIPE_TABLES,
            "FAKE_INDEXES": f"{SEARCH_INDEXES} {FOCUS_INDEX}",
            "FAKE_TARGET_EXISTS": "0",
            "FAKE_BAD_ARCHIVE": "0",
            "FAKE_RESTORE_FAIL": "0",
            "FAKE_MISSING_REVISION": "0",
        }
    )
    env.update(overrides)
    return env


def _run(fake, **overrides):
    env = _env(fake, **overrides)
    return subprocess.run(
        [_bash(), _bash_path(RESTORE), _bash_path(fake["backup"]), "stage55_restore"],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )


def _marker(fake):
    if not fake["marker"].exists():
        return ""
    return fake["marker"].read_text(encoding="utf-8")


def test_0002_accepts_base_schema_without_0004_index(fake_restore):
    result = _run(
        fake_restore,
        FAKE_HEAD=R0002,
        FAKE_TABLES=BASE_TABLES,
        FAKE_INDEXES=BASE_INDEXES,
        EXPECTED_ALEMBIC_HEAD=R0002,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Alembic head: 0002_add_last_city" in result.stdout
    assert "createdb" in _marker(fake_restore)
    assert "restore" in _marker(fake_restore)


def test_0002_accepts_production_like_richer_schema(fake_restore):
    result = _run(
        fake_restore,
        FAKE_HEAD=R0002,
        FAKE_TABLES=NUTRITION_TABLES,
        FAKE_INDEXES=NUTRITION_INDEXES,
        EXPECTED_ALEMBIC_HEAD=R0002,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_0003_accepts_nutrition_schema(fake_restore):
    result = _run(
        fake_restore,
        FAKE_HEAD=R0003,
        FAKE_TABLES=NUTRITION_TABLES,
        FAKE_INDEXES=NUTRITION_INDEXES,
        EXPECTED_ALEMBIC_HEAD=R0003,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_0004_accepts_full_schema(fake_restore):
    result = _run(
        fake_restore,
        FAKE_HEAD=R0004,
        FAKE_TABLES=NUTRITION_TABLES,
        FAKE_INDEXES=f"{NUTRITION_INDEXES} {FOCUS_INDEX}",
        EXPECTED_ALEMBIC_HEAD=R0004,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_0005_accepts_catalog_schema(fake_restore):
    result = _run(
        fake_restore,
        FAKE_HEAD=R0005,
        FAKE_TABLES=CATALOG_TABLES,
        FAKE_INDEXES=f"{CATALOG_INDEXES} {FOCUS_INDEX}",
        EXPECTED_ALEMBIC_HEAD=R0005,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_0006_accepts_global_catalog_schema(fake_restore):
    result = _run(
        fake_restore, EXPECTED_ALEMBIC_HEAD=R0006, FAKE_HEAD=R0006,
        FAKE_TABLES=GLOBAL_TABLES,
        FAKE_INDEXES=f"{GLOBAL_INDEXES} {FOCUS_INDEX}",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_0007_accepts_cleanup_schema(fake_restore):
    result = _run(
        fake_restore, EXPECTED_ALEMBIC_HEAD=R0007,
        FAKE_HEAD=R0007,
        FAKE_TABLES=CLEANUP_TABLES,
        FAKE_INDEXES=f"{CLEANUP_INDEXES} {FOCUS_INDEX}",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_0008_accepts_search_schema(fake_restore):
    result = _run(
        fake_restore, EXPECTED_ALEMBIC_HEAD=R0008,
        FAKE_HEAD=R0008,
        FAKE_TABLES=SEARCH_TABLES,
        FAKE_INDEXES=f"{SEARCH_INDEXES} {FOCUS_INDEX}",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_0009_accepts_recipe_schema(fake_restore):
    result = _run(
        fake_restore, EXPECTED_ALEMBIC_HEAD=R0009,
        FAKE_TABLES=RECIPE_TABLES,
        FAKE_INDEXES=f"{SEARCH_INDEXES} {FOCUS_INDEX}",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_unknown_revision_is_rejected(fake_restore):
    result = _run(fake_restore, FAKE_HEAD="9999_unknown")
    assert result.returncode != 0
    assert "unsupported Alembic revision" in result.stdout + result.stderr


def test_missing_revision_is_rejected(fake_restore):
    result = _run(fake_restore, FAKE_MISSING_REVISION="1")
    assert result.returncode != 0
    assert "alembic_version is missing or unreadable" in result.stdout + result.stderr


def test_empty_revision_is_rejected(fake_restore):
    result = _run(fake_restore, FAKE_HEAD="")
    assert result.returncode != 0
    assert "revision is missing" in result.stdout + result.stderr


def test_ambiguous_revision_is_rejected(fake_restore):
    result = _run(fake_restore, FAKE_HEAD=f"{R0009}\n{R0002}")
    assert result.returncode != 0
    assert "exactly one Alembic revision" in result.stdout + result.stderr


def test_expected_head_mismatch_is_rejected(fake_restore):
    result = _run(fake_restore, FAKE_HEAD=R0002, EXPECTED_ALEMBIC_HEAD=R0004)
    assert result.returncode != 0
    assert "expected 0004_focus_session_client_id" in result.stdout + result.stderr


def test_missing_0004_index_is_rejected(fake_restore):
    result = _run(fake_restore, FAKE_INDEXES=NUTRITION_INDEXES)
    assert result.returncode != 0
    assert "missing required index" in result.stdout + result.stderr
    assert "uq_focus_sessions_user_client" in result.stdout + result.stderr


def test_missing_required_table_is_rejected(fake_restore):
    result = _run(
        fake_restore,
        FAKE_TABLES="users notes tasks focus_sessions alembic_version meal_entries saved_recipes",
    )
    assert result.returncode != 0
    assert "missing required table" in result.stdout + result.stderr
    assert "community_foods" in result.stdout + result.stderr


def test_0002_missing_base_index_is_rejected(fake_restore):
    result = _run(
        fake_restore,
        FAKE_HEAD=R0002,
        FAKE_TABLES=BASE_TABLES,
        FAKE_INDEXES="users:ix_users_telegram_id tasks:ix_tasks_user_completed focus_sessions:ix_focus_user_finished",
    )
    assert result.returncode != 0
    assert "missing required index" in result.stdout + result.stderr
    assert "ix_notes_user_updated" in result.stdout + result.stderr


def test_existing_target_is_refused_before_createdb(fake_restore):
    result = _run(fake_restore, FAKE_TARGET_EXISTS="1")
    assert result.returncode != 0
    assert "already exists" in result.stdout + result.stderr
    assert _marker(fake_restore) == ""


def test_missing_confirmation_is_refused_before_createdb(fake_restore):
    result = _run(fake_restore, RESTORE_CONFIRM="")
    assert result.returncode != 0
    assert "Set RESTORE_CONFIRM" in result.stdout + result.stderr
    assert _marker(fake_restore) == ""


def test_production_target_is_refused(fake_restore):
    env = _env(fake_restore, RESTORE_CONFIRM="production")
    result = subprocess.run(
        [_bash(), _bash_path(RESTORE), _bash_path(fake_restore["backup"]), "production"],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "production-like" in result.stdout + result.stderr
    assert _marker(fake_restore) == ""


def test_bad_archive_is_refused_before_createdb(fake_restore):
    result = _run(fake_restore, FAKE_BAD_ARCHIVE="1")
    assert result.returncode != 0
    assert "not readable by pg_restore" in result.stdout + result.stderr
    assert _marker(fake_restore) == ""


def test_restore_failure_is_not_masked(fake_restore):
    result = _run(fake_restore, FAKE_RESTORE_FAIL="1")
    assert result.returncode != 0
    assert "Restore failed" in result.stdout + result.stderr
    assert "createdb" in _marker(fake_restore)
    assert "restore_failed" in _marker(fake_restore)


def test_script_has_no_destructive_schema_operations():
    text = RESTORE.read_text(encoding="utf-8").lower()
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in (
        "dropdb",
        "drop database",
        "drop table",
        "truncate",
        "delete from",
        "alembic upgrade",
        "alembic downgrade",
    ):
        assert forbidden not in code
