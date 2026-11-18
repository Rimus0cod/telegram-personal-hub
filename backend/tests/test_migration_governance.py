"""Migration governance: frozen revisions, one head, one-shot service, lock."""

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.migration_lock import MIGRATION_LOCK_ID, migration_lock

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

# SHA-256 of the published revision files with line endings normalized to LF.
# Editing a published migration must fail this test and requires a new revision.
FROZEN_REVISIONS = {
    "0001_initial.py": "9a568c026796e971e8666f16f9556d6100449d7c95278e557fab4359b2b44779",
    "0002_add_last_city.py": "f254f77e93b11cd52f2605593eacb71e34f6e205fcdb2333af13391234841773",
    "0003_nutrition_schema.py": "2a7c9b00f4711d96dbe108a9983e0056208d416840ad31e06afe77e3f4f7e313",
    "0004_focus_session_client_id.py": "373082f525617fba49e8885d64f9e2eda5e6688a676757193833118da2042253",
    "0005_catalog_foods.py": "52227dca8e6bfb01d67a76a816572e8984863d35183f27313445e4aaa1fcdd58",
    "0006_global_catalog.py": "b416f8d85663b5027f76601d3221126204bd2fb4a7238e2cea9bd4d2eab5ff8f",
    "0007_catalog_cleanup.py": "9d42422945414cfc38e3a86b76ae1a3cd01efc4d0d82141d6ebd201e1427f45c",
    "0008_multilingual_search.py": "2934956a075845bf67d4f8ef81cdb176f68a0f103c44b2b77384949ea7bf8214",
    "0009_recipe_composition.py": "72ef1b6598ea6f9556005065b3638edf0e02f982b9b8400d5c93277439a21ba5",
}

EXPECTED_CHAIN = {
    "0001_initial": None,
    "0002_add_last_city": "0001_initial",
    "0003_nutrition_schema": "0002_add_last_city",
    "0004_focus_session_client_id": "0003_nutrition_schema",
    "0005_catalog_foods": "0004_focus_session_client_id",
    "0006_global_catalog": "0005_catalog_foods",
    "0007_catalog_cleanup": "0006_global_catalog",
    "0008_multilingual_search": "0007_catalog_cleanup",
    "0009_recipe_composition": "0008_multilingual_search",
}


def _script_directory():
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config)


def test_published_migrations_are_frozen():
    versions = BACKEND_DIR / "alembic" / "versions"
    for name, expected in FROZEN_REVISIONS.items():
        data = (versions / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == expected, name


def test_exactly_one_head_and_a_linear_chain():
    script = _script_directory()
    assert script.get_heads() == ["0009_recipe_composition"]

    revisions = {revision.revision: revision.down_revision for revision in script.walk_revisions()}
    assert revisions == EXPECTED_CHAIN
    assert len(revisions) == len(EXPECTED_CHAIN)


def test_backend_image_does_not_run_migrations():
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text(encoding="utf-8")
    cmd_lines = [
        line[len("CMD "):] for line in dockerfile.splitlines() if line.startswith("CMD ")
    ]
    assert cmd_lines, "Dockerfile has no CMD"
    command = json.loads(cmd_lines[-1])
    assert command[0] == "uvicorn"
    assert all("alembic" not in part.lower() for part in command)


def test_compose_uses_a_one_shot_migration_service():
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    migrate = services["migrate"]
    backend = services["backend"]
    bot = services["bot"]

    assert migrate["command"] == [
        "alembic",
        "-c",
        "/app/alembic.ini",
        "upgrade",
        "head",
    ]
    assert migrate["restart"] == "no"
    assert "ports" not in migrate
    assert migrate["depends_on"]["db"]["condition"] == "service_healthy"
    assert (
        backend["depends_on"]["migrate"]["condition"]
        == "service_completed_successfully"
    )
    assert bot["depends_on"]["backend"]["condition"] == "service_healthy"


def test_postgres_advisory_lock_blocks_and_is_released(postgres_engine):
    with postgres_engine.connect() as holder:
        with migration_lock(holder):
            with postgres_engine.connect() as other:
                acquired = other.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID},
                ).scalar()
                assert acquired is False


def test_postgres_advisory_lock_is_released_after_an_error(postgres_engine):
    with postgres_engine.connect() as failing:
        with pytest.raises(RuntimeError):
            with migration_lock(failing):
                raise RuntimeError("simulated migration failure")

    with postgres_engine.connect() as check:
        acquired = check.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": MIGRATION_LOCK_ID},
        ).scalar()
        assert acquired is True
        check.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": MIGRATION_LOCK_ID},
        )


def test_concurrent_migrations_serialize_on_postgres():
    admin = os.environ.get("TEST_POSTGRES_ADMIN_URL")
    if not admin:
        pytest.skip("TEST_POSTGRES_ADMIN_URL is not set")

    admin_url = make_url(admin)
    database = f"hub_54c_race_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        database_url = str(admin_url.set(database=database))
        env = os.environ.copy()
        env["DATABASE_URL"] = database_url
        command = [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
        ]
        processes = [
            subprocess.Popen(
                command,
                cwd=BACKEND_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        outputs = [process.communicate(timeout=180) for process in processes]
        assert [process.returncode for process in processes] == [0, 0], outputs

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchall()
                assert len(rows) == 1
                assert rows[0][0] == "0009_recipe_composition"
        finally:
            engine.dispose()
    finally:
        try:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
                )
        finally:
            admin_engine.dispose()
