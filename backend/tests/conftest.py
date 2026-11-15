"""Shared fixtures for the Stage 5.1 regression tests.

The application engine is pinned to a throwaway file before any test module can
import ``app``. Each module gets its own migrated SQLite database; PostgreSQL is
created as an isolated schema inside the disposable ``TEST_POSTGRES_URL``
database (the name must contain "test").
"""

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(Path(tempfile.mkdtemp(prefix='hub-51-import-')) / 'unused.db').as_posix()}",
)

from app.database import get_db  # noqa: E402
from app.dependencies.auth import current_user  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.models import User  # noqa: E402


def run_alembic(database_url: str, *args: str):
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


def _assert_migrated(result):
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@pytest.fixture(scope="module")
def sqlite_engine(tmp_path_factory):
    path = tmp_path_factory.mktemp("hub-51-sqlite") / "test.db"
    url = f"sqlite:///{path.as_posix()}"
    _assert_migrated(run_alembic(url, "upgrade", "head"))
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def postgres_engine():
    raw_url = os.environ.get("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("TEST_POSTGRES_URL is not set")

    from sqlalchemy.engine import make_url

    url = make_url(raw_url)
    if "test" not in (url.database or "").lower():
        pytest.skip("Refusing to run against a database name without 'test'")

    admin_engine = create_engine(raw_url)
    schema = f"hub_51_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    scoped = url.set(query={**dict(url.query), "options": f"-csearch_path={schema}"})
    result = run_alembic(str(scoped), "upgrade", "head")
    if result.returncode != 0:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
        raise AssertionError(f"{result.stdout}\n{result.stderr}")

    engine = create_engine(str(scoped))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _make_api(engine):
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    suffix = uuid.uuid4().int % 1_000_000_000
    user1 = User(telegram_id=5_000_000_000 + suffix, first_name="One")
    user2 = User(telegram_id=5_100_000_000 + suffix, first_name="Two")
    db.add_all([user1, user2])
    db.commit()
    db.refresh(user1)
    db.refresh(user2)

    holder = {"user": user1}
    fastapi_app.dependency_overrides[get_db] = lambda: db
    fastapi_app.dependency_overrides[current_user] = lambda: holder["user"]
    client = TestClient(fastapi_app)
    return SimpleNamespace(
        client=client,
        db=db,
        user1=user1,
        user2=user2,
        holder=holder,
        Session=Session,
        engine=engine,
    )


@pytest.fixture(params=["sqlite", "postgres"])
def api(request):
    """Authenticated API client on SQLite and PostgreSQL with two users."""
    fixture = "sqlite_engine" if request.param == "sqlite" else "postgres_engine"
    engine = request.getfixturevalue(fixture)
    env = _make_api(engine)
    try:
        yield env
    finally:
        fastapi_app.dependency_overrides.clear()
        env.db.close()
