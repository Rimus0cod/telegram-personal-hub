"""API tests for Focus session validation and idempotency (Stage 4.4).

The module imports the FastAPI app once, so the database URL is pinned to a
throwaway file before that import. Every test uses the Alembic-managed schema on
an isolated temporary SQLite database; PostgreSQL concurrency runs only when
``TEST_POSTGRES_URL`` points at a disposable test database.
"""

import os
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]

# ``app.database`` reads DATABASE_URL at import time. Point it at a throwaway
# location so importing the app can never open a developer database.
_IMPORT_DIR = tempfile.mkdtemp(prefix="focus-api-import-")
os.environ["DATABASE_URL"] = (
    f"sqlite:///{(Path(_IMPORT_DIR) / 'unused.db').as_posix()}"
)
os.environ.setdefault("BOT_TOKEN", "test-token")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.api.focus as focus_api  # noqa: E402
from app.api.focus import SessionInput, create_session  # noqa: E402
from app.database import get_db  # noqa: E402
from app.dependencies.auth import current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import FocusSession, User  # noqa: E402


URL_PREFIX = "/api/v1/focus"


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


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    directory = tmp_path_factory.mktemp("focus-api")
    path = directory / "focus.db"
    url = f"sqlite:///{path.as_posix()}"

    result = _run_alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    engine = create_engine(url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield engine, SessionLocal
    finally:
        engine.dispose()


class Api:
    def __init__(self, client, db, user_one, user_two):
        self.client = client
        self.db = db
        self.user_one = user_one
        self.user_two = user_two
        self._current = {"user": user_one}

    def as_user(self, user):
        self._current["user"] = user

    def post(self, body):
        return self.client.post(f"{URL_PREFIX}/sessions", json=body)

    def stats(self):
        return self.client.get(f"{URL_PREFIX}/stats")


@pytest.fixture
def api(database):
    _engine, SessionLocal = database
    db = SessionLocal()
    db.query(FocusSession).delete()
    db.commit()

    user_one = db.query(User).filter_by(telegram_id=101).first()
    user_two = db.query(User).filter_by(telegram_id=102).first()
    if user_one is None:
        user_one = User(telegram_id=101, first_name="One")
        user_two = User(telegram_id=102, first_name="Two")
        db.add_all([user_one, user_two])
        db.commit()
        db.refresh(user_one)
        db.refresh(user_two)
    if user_two is None:
        user_two = User(telegram_id=102, first_name="Two")
        db.add(user_two)
        db.commit()
        db.refresh(user_two)

    holder = {"user": user_one}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: holder["user"]
    client = TestClient(app)
    wrapper = Api(client, db, user_one, user_two)
    wrapper._current = holder
    wrapper.as_user = lambda user: holder.__setitem__("user", user)

    try:
        yield wrapper
    finally:
        app.dependency_overrides.clear()
        db.close()


def payload(
    *,
    client_id=None,
    duration=25,
    mode="focus",
    started_at=None,
    finished_at=None,
):
    end = finished_at or datetime.now(timezone.utc).replace(microsecond=0)
    start = started_at or end - timedelta(minutes=duration)
    body = {
        "duration": duration,
        "mode": mode,
        "completed": True,
        "started_at": start.isoformat(),
        "finished_at": end.isoformat(),
    }
    if client_id is not None:
        body["client_id"] = str(client_id)
    return body


def test_new_session_with_client_id_is_created(api):
    client_id = str(uuid.uuid4())

    response = api.post(payload(client_id=client_id))

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["client_id"] == client_id
    assert created["duration"] == 25
    assert created["mode"] == "focus"
    assert api.db.query(FocusSession).count() == 1
    assert api.db.query(FocusSession).one().client_id == client_id


def test_repeated_client_id_does_not_create_a_duplicate(api):
    client_id = str(uuid.uuid4())
    body = payload(client_id=client_id)

    first = api.post(body)
    second = api.post(body)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert api.db.query(FocusSession).count() == 1
    assert second.json()["id"] == first.json()["id"]


def test_identical_retry_returns_the_existing_session(api):
    client_id = str(uuid.uuid4())
    body = payload(client_id=client_id)

    first = api.post(body).json()
    second = api.post(body).json()

    for field in (
        "id",
        "duration",
        "mode",
        "completed",
        "started_at",
        "finished_at",
        "client_id",
    ):
        assert second[field] == first[field], field


def test_same_client_id_with_different_payload_conflicts(api):
    client_id = str(uuid.uuid4())
    original = payload(client_id=client_id, duration=25)
    assert api.post(original).status_code == 201

    conflict = api.post(payload(client_id=client_id, duration=30))

    assert conflict.status_code == 409
    assert "different session" in conflict.json()["detail"]
    assert api.db.query(FocusSession).count() == 1
    assert api.db.query(FocusSession).one().duration == 25


def test_same_client_id_is_allowed_for_different_users(api):
    client_id = str(uuid.uuid4())

    first = api.post(payload(client_id=client_id))
    api.as_user(api.user_two)
    second = api.post(payload(client_id=client_id))

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] != second.json()["id"]
    assert api.db.query(FocusSession).count() == 2


def test_race_between_lookup_and_insert_is_recovered(api, monkeypatch):
    """Simulate a concurrent request that commits between lookup and insert."""
    client_id = str(uuid.uuid4())
    body = payload(client_id=client_id)
    first = api.post(body)
    assert first.status_code == 201

    real_find = focus_api._find_session
    calls = {"count": 0}

    def miss_once(db, user_id, session_client_id):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return real_find(db, user_id, session_client_id)

    monkeypatch.setattr(focus_api, "_find_session", miss_once)

    second = api.post(body)

    assert calls["count"] >= 2
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert api.db.query(FocusSession).count() == 1


def test_legacy_requests_without_client_id_still_work(api):
    first = api.post(payload())
    second = api.post(payload())

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["client_id"] is None
    assert api.db.query(FocusSession).count() == 2


def test_invalid_client_id_is_rejected(api):
    body = payload()
    body["client_id"] = "not-a-uuid"

    response = api.post(body)

    assert response.status_code == 422
    assert api.db.query(FocusSession).count() == 0


def test_started_at_in_the_future_is_rejected(api):
    start = datetime.now(timezone.utc) + timedelta(minutes=10)
    response = api.post(
        payload(started_at=start, finished_at=start + timedelta(minutes=25))
    )

    assert response.status_code == 422
    assert "future" in response.json()["detail"]


def test_started_at_older_than_seven_days_is_rejected(api):
    start = datetime.now(timezone.utc) - timedelta(days=8)
    response = api.post(
        payload(started_at=start, finished_at=start + timedelta(minutes=25))
    )

    assert response.status_code == 422
    assert "7 days" in response.json()["detail"]


# ---------------------------------------------------------------------------
# D1: non-UTC offsets are normalized before storage
# ---------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    """Read a stored value as UTC; SQLite hands back naive datetimes."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, value
    return parsed.astimezone(timezone.utc)


def _offset_payload(client_id: str) -> tuple[dict, datetime, datetime]:
    offset = timezone(timedelta(hours=5))
    end_utc = datetime.now(timezone.utc).replace(microsecond=0)
    start_utc = end_utc - timedelta(minutes=25)
    return (
        {
            "duration": 25,
            "mode": "focus",
            "completed": True,
            "started_at": start_utc.astimezone(offset).isoformat(),
            "finished_at": end_utc.astimezone(offset).isoformat(),
            "client_id": client_id,
        },
        start_utc,
        end_utc,
    )


def test_offset_timestamps_are_stored_as_utc(api):
    body, start_utc, end_utc = _offset_payload(str(uuid.uuid4()))

    response = api.post(body)

    assert response.status_code == 201, response.text
    created = response.json()
    # The API must not echo the client's +05:00 offset back.
    assert "+05:00" not in created["started_at"]
    assert "+05:00" not in created["finished_at"]
    assert _parse_utc(created["started_at"]) == start_utc
    assert _parse_utc(created["finished_at"]) == end_utc

    row = api.db.query(FocusSession).one()
    assert _as_utc(row.started_at) == start_utc
    assert _as_utc(row.finished_at) == end_utc


def test_offset_retry_with_the_same_client_id_is_idempotent(api):
    body, _start_utc, end_utc = _offset_payload(str(uuid.uuid4()))

    first = api.post(body)
    second = api.post(body)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    # A retry must never move the stored finish time.
    assert second.json()["finished_at"] == first.json()["finished_at"]
    assert _parse_utc(second.json()["finished_at"]) == end_utc
    assert api.db.query(FocusSession).count() == 1


def test_normalizing_utc_requests_keeps_the_same_instant(api):
    end_utc = datetime.now(timezone.utc).replace(microsecond=0)
    start_utc = end_utc - timedelta(minutes=25)

    response = api.post(
        payload(
            client_id=str(uuid.uuid4()),
            started_at=start_utc,
            finished_at=end_utc,
        )
    )

    assert response.status_code == 201, response.text
    assert _parse_utc(response.json()["started_at"]) == start_utc
    assert _parse_utc(response.json()["finished_at"]) == end_utc


# ---------------------------------------------------------------------------
# D2: the idempotency lookup runs before the age window
# ---------------------------------------------------------------------------


def _store_old_session(api, client_id: str) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=8)
    start = end - timedelta(minutes=25)
    api.db.add(
        FocusSession(
            user_id=api.user_one.id,
            duration=25,
            mode="focus",
            completed=True,
            started_at=start,
            finished_at=end,
            client_id=client_id,
        )
    )
    api.db.commit()
    return start, end


def test_existing_session_older_than_seven_days_can_be_replayed(api):
    client_id = str(uuid.uuid4())
    start, end = _store_old_session(api, client_id)
    stored = api.db.query(FocusSession).one()

    response = api.post(
        payload(client_id=client_id, started_at=start, finished_at=end)
    )

    assert response.status_code == 201, response.text
    assert response.json()["id"] == stored.id
    assert _parse_utc(response.json()["finished_at"]) == end
    assert api.db.query(FocusSession).count() == 1
    # The replay must not rewrite the stored row.
    assert _as_utc(api.db.query(FocusSession).one().finished_at) == end


def test_old_existing_session_with_a_changed_payload_conflicts(api):
    client_id = str(uuid.uuid4())
    start, end = _store_old_session(api, client_id)

    response = api.post(
        payload(
            client_id=client_id,
            duration=30,
            started_at=start,
            finished_at=end,
        )
    )

    assert response.status_code == 409
    assert "different session" in response.json()["detail"]
    assert api.db.query(FocusSession).count() == 1
    assert api.db.query(FocusSession).one().duration == 25
    assert _as_utc(api.db.query(FocusSession).one().finished_at) == end


def test_finished_at_before_started_at_is_rejected(api):
    end = datetime.now(timezone.utc)
    response = api.post(
        payload(started_at=end, finished_at=end - timedelta(minutes=1), duration=1)
    )

    assert response.status_code == 422
    assert "earlier" in response.json()["detail"]


def test_naive_timestamps_are_rejected(api):
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    body = {
        "duration": 25,
        "mode": "focus",
        "completed": True,
        "started_at": (now - timedelta(minutes=25)).isoformat(),
        "finished_at": now.isoformat(),
    }

    response = api.post(body)

    assert response.status_code == 422
    assert "timezone" in response.json()["detail"]


def test_interval_longer_than_24_hours_is_rejected(api):
    end = datetime.now(timezone.utc)
    response = api.post(
        payload(started_at=end - timedelta(hours=25), finished_at=end)
    )

    assert response.status_code == 422
    assert "24 hours" in response.json()["detail"]


def test_duration_and_mode_are_validated(api):
    too_short = payload()
    too_short["duration"] = 0
    assert api.post(too_short).status_code == 422

    too_long = payload()
    too_long["duration"] = 181
    assert api.post(too_long).status_code == 422

    bad_mode = payload(mode="pomodoro")
    invalid_mode = api.post(bad_mode)
    assert invalid_mode.status_code == 422
    assert "Invalid mode" in invalid_mode.json()["detail"]

    mismatch = payload(duration=25, finished_at=None)
    end = datetime.now(timezone.utc).replace(microsecond=0)
    mismatch = payload(
        duration=25,
        started_at=end - timedelta(minutes=5),
        finished_at=end,
    )
    response = api.post(mismatch)
    assert response.status_code == 422
    assert "duration" in response.json()["detail"]

    assert api.db.query(FocusSession).count() == 0


def test_stats_count_each_session_once_after_retries(api):
    client_id = str(uuid.uuid4())
    body = payload(client_id=client_id)

    assert api.post(body).status_code == 201
    assert api.post(body).status_code == 201
    assert api.post(payload(mode="short_break", duration=5, client_id=str(uuid.uuid4()))).status_code == 201

    stats = api.stats().json()

    assert stats["sessions"] == 1
    assert stats["focus_minutes"] == 25
    assert len(stats["history"]) == 1


# ---------------------------------------------------------------------------
# PostgreSQL: real concurrent inserts (gated by TEST_POSTGRES_URL)
# ---------------------------------------------------------------------------


@pytest.fixture
def postgres_focus_schema():
    raw_url = os.environ.get("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("TEST_POSTGRES_URL is not set")

    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    url = make_url(raw_url)
    if "test" not in (url.database or "").lower():
        pytest.skip("Refusing to run against a database name without 'test'")

    admin_engine = create_engine(raw_url)
    schema = f"focus_api_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    scoped = url.set(query={
        **dict(url.query),
        "options": f"-csearch_path={schema}",
    })
    result = _run_alembic(str(scoped), "upgrade", "head")
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


def test_postgres_concurrent_requests_create_one_session(postgres_focus_schema):
    SessionLocal = sessionmaker(
        bind=postgres_focus_schema, autoflush=False, expire_on_commit=False
    )
    setup = SessionLocal()
    setup.add(User(telegram_id=501, first_name="Concurrent"))
    setup.commit()
    setup.close()

    client_id = str(uuid.uuid4())
    body = SessionInput(**payload(client_id=client_id))
    barrier = threading.Barrier(2, timeout=15)
    results = []
    errors = []

    def worker():
        db = SessionLocal()
        try:
            user = db.query(User).filter_by(telegram_id=501).one()
            barrier.wait()
            results.append(create_session(body, db, user))
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 2
    assert results[0]["id"] == results[1]["id"]

    check = SessionLocal()
    try:
        assert check.query(FocusSession).count() == 1
        assert check.query(FocusSession).one().client_id == client_id
    finally:
        check.close()


def test_postgres_offset_timestamps_round_trip(postgres_focus_schema):
    """D1 on PostgreSQL: offsets are normalized and retries stay idempotent."""
    SessionLocal = sessionmaker(
        bind=postgres_focus_schema, autoflush=False, expire_on_commit=False
    )
    db = SessionLocal()
    db.add(User(telegram_id=601, first_name="Offset"))
    db.commit()
    user = db.query(User).filter_by(telegram_id=601).one()

    body_dict, start_utc, end_utc = _offset_payload(str(uuid.uuid4()))
    body = SessionInput(**body_dict)

    try:
        first = create_session(body, db, user)
        second = create_session(body, db, user)
        db.expire_all()
        row = db.query(FocusSession).one()

        assert first["id"] == second["id"]
        assert first["started_at"] == start_utc
        assert first["finished_at"] == end_utc
        assert second["finished_at"] == first["finished_at"]
        assert _as_utc(row.started_at) == start_utc
        assert _as_utc(row.finished_at) == end_utc
        assert db.query(FocusSession).count() == 1
    finally:
        db.close()


def test_postgres_existing_old_session_can_be_replayed(postgres_focus_schema):
    """D2 on PostgreSQL: the lookup wins over the 7-day window."""
    SessionLocal = sessionmaker(
        bind=postgres_focus_schema, autoflush=False, expire_on_commit=False
    )
    db = SessionLocal()
    db.add(User(telegram_id=602, first_name="Old"))
    db.commit()
    user = db.query(User).filter_by(telegram_id=602).one()

    client_id = str(uuid.uuid4())
    end = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=8)
    start = end - timedelta(minutes=25)
    db.add(
        FocusSession(
            user_id=user.id,
            duration=25,
            mode="focus",
            completed=True,
            started_at=start,
            finished_at=end,
            client_id=client_id,
        )
    )
    db.commit()
    stored_id = db.query(FocusSession).one().id

    body = SessionInput(
        **payload(client_id=client_id, started_at=start, finished_at=end)
    )

    try:
        result = create_session(body, db, user)
        assert result["id"] == stored_id
        assert result["finished_at"] == end
        assert db.query(FocusSession).count() == 1
    finally:
        db.close()
