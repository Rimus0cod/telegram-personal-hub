"""H1/M4 regressions: user creation, profile policy and concurrent first login."""

import hashlib
import hmac
import itertools
import json
import threading
import time
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.dependencies.auth import current_user
from app.models.models import User

TOKEN = "test-token"
_UIDS = itertools.count(100_000)


def signed(uid, **profile):
    user = {"id": uid, "first_name": "Test", **profile}
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps(user, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def new_uid():
    return next(_UIDS)


@pytest.fixture
def sessions(sqlite_engine):
    return sessionmaker(bind=sqlite_engine, autoflush=False, expire_on_commit=False)


def _count(engine, uid):
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        return db.query(User).filter_by(telegram_id=uid).count()
    finally:
        db.close()


def test_first_request_creates_user(sqlite_engine, sessions):
    uid = new_uid()
    db = sessions()
    try:
        user = current_user(authorization="tma " + signed(uid, first_name="Alice"), db=db)
        assert user.telegram_id == uid
        assert user.first_name == "Alice"
        assert db.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        db.close()
    assert _count(sqlite_engine, uid) == 1


def test_repeated_request_reuses_the_row_and_skips_an_unchanged_commit(
    sqlite_engine, sessions, monkeypatch
):
    uid = new_uid()
    db = sessions()
    try:
        first = current_user(authorization="tma " + signed(uid, username="alice"), db=db)
        calls = {"n": 0}
        real_commit = db.commit

        def counting_commit():
            calls["n"] += 1
            return real_commit()

        monkeypatch.setattr(db, "commit", counting_commit)

        second = current_user(authorization="tma " + signed(uid, username="alice"), db=db)
        assert second.id == first.id
        assert calls["n"] == 0

        third = current_user(authorization="tma " + signed(uid, username="alice2"), db=db)
        assert third.id == first.id
        assert calls["n"] == 1
        assert third.username == "alice2"
    finally:
        db.close()
    assert _count(sqlite_engine, uid) == 1


def _run_concurrent(engine, uid, threads=12):
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    barrier = threading.Barrier(threads, timeout=30)
    header = "tma " + signed(uid)
    ids = []
    errors = []
    session_ok = []

    def worker():
        db = Session()
        try:
            barrier.wait()
            user = current_user(authorization=header, db=db)
            ids.append(user.id)
            session_ok.append(db.execute(text("SELECT 1")).scalar_one() == 1)
        except BaseException as error:  # noqa: BLE001 - recorded for the assertion
            errors.append(error)
        finally:
            db.close()

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join(timeout=60)

    return {
        "ids": ids,
        "errors": errors,
        "session_ok": session_ok,
        "rows": _count(engine, uid),
    }


def test_concurrent_first_requests_create_one_user(sqlite_engine):
    result = _run_concurrent(sqlite_engine, new_uid())

    assert result["errors"] == [], result["errors"]
    assert len(result["ids"]) == 12
    assert len(set(result["ids"])) == 1
    assert result["rows"] == 1
    assert all(result["session_ok"])


def test_conflict_recovery_returns_the_concurrent_user(
    sqlite_engine, sessions, monkeypatch
):
    """The row is created by another session between our SELECT and commit."""
    uid = new_uid()
    db = sessions()
    state = {"created": False}
    real_commit = db.commit

    def racing_commit():
        if not state["created"]:
            state["created"] = True
            other = sessions()
            other.add(User(telegram_id=uid, first_name="Concurrent"))
            other.commit()
            other.close()
        return real_commit()

    monkeypatch.setattr(db, "commit", racing_commit)
    try:
        user = current_user(
            authorization="tma " + signed(uid, first_name="Race", username="race"),
            db=db,
        )
        assert user.telegram_id == uid
        assert user.first_name == "Race"
        assert user.username == "race"
    finally:
        db.close()
    assert _count(sqlite_engine, uid) == 1


def test_unrelated_integrity_error_is_not_masked(sqlite_engine, sessions, monkeypatch):
    uid = new_uid()
    db = sessions()

    def failing_commit():
        raise IntegrityError(
            "INSERT INTO users ...", {}, Exception("some other constraint")
        )

    monkeypatch.setattr(db, "commit", failing_commit)
    try:
        with pytest.raises(IntegrityError):
            current_user(authorization="tma " + signed(uid), db=db)
        # The dependency rolled back, so the session is still usable.
        assert db.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        db.close()
    assert _count(sqlite_engine, uid) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_name", "N" * 129),
        ("last_name", "L" * 129),
        ("username", "u" * 65),
    ],
)
def test_oversized_profile_is_rejected(sqlite_engine, sessions, field, value):
    uid = new_uid()
    db = sessions()
    try:
        with pytest.raises(HTTPException) as error:
            current_user(
                authorization="tma " + signed(uid, **{field: value}),
                db=db,
            )
        assert error.value.status_code == 401
        assert error.value.detail == "Invalid Telegram user profile"
    finally:
        db.close()
    assert _count(sqlite_engine, uid) == 0


def test_first_name_falls_back_to_friend(sqlite_engine, sessions):
    uid = new_uid()
    db = sessions()
    try:
        user = current_user(authorization="tma " + signed(uid, first_name=None), db=db)
        assert user.first_name == "Friend"
    finally:
        db.close()


def test_postgres_concurrent_first_requests_create_one_user(postgres_engine):
    result = _run_concurrent(postgres_engine, new_uid())

    assert result["errors"] == [], result["errors"]
    assert len(result["ids"]) == 12
    assert len(set(result["ids"])) == 1
    assert result["rows"] == 1
    assert all(result["session_ok"])


def test_postgres_oversized_profile_is_rejected(postgres_engine):
    Session = sessionmaker(bind=postgres_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    try:
        with pytest.raises(HTTPException) as error:
            current_user(
                authorization="tma " + signed(new_uid(), first_name="N" * 129),
                db=db,
            )
        assert error.value.status_code == 401
    finally:
        db.close()
