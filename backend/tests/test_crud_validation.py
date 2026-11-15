"""M1/M2/M4/M5 regressions plus user-isolation checks on SQLite and PostgreSQL."""

import threading
import uuid

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import app.api.weather as weather_api
import app.main as main_module
from app.api.nutrition import CatalogFoodInput, add_catalog_food
from app.models.models import CommunityFood, User

PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# M1: explicit null in PATCH
# ---------------------------------------------------------------------------


def test_note_patch_rejects_explicit_null_and_keeps_other_fields(api):
    note = api.client.post(f"{PREFIX}/notes", json={"title": "keep", "content": "body"})
    assert note.status_code == 201, note.text
    note_id = note.json()["id"]

    unchanged = api.client.patch(f"{PREFIX}/notes/{note_id}", json={})
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["title"] == "keep"
    assert unchanged.json()["content"] == "body"

    title_null = api.client.patch(f"{PREFIX}/notes/{note_id}", json={"title": None})
    assert title_null.status_code == 422, title_null.text
    content_null = api.client.patch(f"{PREFIX}/notes/{note_id}", json={"content": None})
    assert content_null.status_code == 422, content_null.text

    after = api.client.get(f"{PREFIX}/notes/{note_id}")
    assert after.json()["title"] == "keep"
    assert after.json()["content"] == "body"

    updated = api.client.patch(
        f"{PREFIX}/notes/{note_id}", json={"title": "new title"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "new title"
    assert updated.json()["content"] == "body"


def test_task_patch_rejects_explicit_null_and_allows_due_date_reset(api):
    task = api.client.post(
        f"{PREFIX}/tasks",
        json={
            "title": "keep",
            "description": "body",
            "completed": False,
            "priority": "high",
            "due_date": "2030-01-02T03:04:05Z",
        },
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]

    for field in ("title", "description", "completed", "priority"):
        response = api.client.patch(
            f"{PREFIX}/tasks/{task_id}", json={field: None}
        )
        assert response.status_code == 422, f"{field}: {response.text}"

    still = api.client.get(f"{PREFIX}/tasks/{task_id}").json()
    assert still["title"] == "keep"
    assert still["description"] == "body"
    assert still["completed"] is False
    assert still["priority"] == "high"

    cleared = api.client.patch(f"{PREFIX}/tasks/{task_id}", json={"due_date": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["due_date"] is None

    updated = api.client.patch(
        f"{PREFIX}/tasks/{task_id}", json={"title": "new", "completed": True}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "new"
    assert updated.json()["completed"] is True
    assert updated.json()["description"] == "body"


def test_notes_and_tasks_remain_user_scoped(api):
    note_id = api.client.post(
        f"{PREFIX}/notes", json={"title": "private", "content": "x"}
    ).json()["id"]
    task_id = api.client.post(
        f"{PREFIX}/tasks", json={"title": "private task"}
    ).json()["id"]

    api.holder["user"] = api.user2

    assert api.client.get(f"{PREFIX}/notes/{note_id}").status_code == 404
    assert api.client.patch(
        f"{PREFIX}/notes/{note_id}", json={"title": "stolen"}
    ).status_code == 404
    assert api.client.delete(f"{PREFIX}/notes/{note_id}").status_code == 404

    assert api.client.get(f"{PREFIX}/tasks/{task_id}").status_code == 404
    assert api.client.patch(
        f"{PREFIX}/tasks/{task_id}", json={"completed": True}
    ).status_code == 404
    assert api.client.delete(f"{PREFIX}/tasks/{task_id}").status_code == 404

    api.holder["user"] = api.user1
    assert api.client.get(f"{PREFIX}/notes/{note_id}").status_code == 200
    assert api.client.get(f"{PREFIX}/tasks/{task_id}").status_code == 200


# ---------------------------------------------------------------------------
# M2: shared catalog race
# ---------------------------------------------------------------------------


def _barcode():
    return str(500_000_000_000 + uuid.uuid4().int % 100_000_000_000)


def _catalog_post(api, barcode, name="Food"):
    return api.client.post(
        f"{PREFIX}/nutrition/catalog",
        json={
            "barcode": barcode,
            "name": name,
            "brand": "Brand",
            "kcal": 100,
            "protein": 5,
            "carbs": 10,
            "fat": 2,
        },
    )


def test_catalog_add_update_and_conflict(api):
    barcode = _barcode()
    created = _catalog_post(api, barcode)
    assert created.status_code == 201, created.text

    api.holder["user"] = api.user2
    conflict = _catalog_post(api, barcode)
    assert conflict.status_code == 409, conflict.text
    assert "already in the shared catalog" in conflict.json()["detail"]

    api.holder["user"] = api.user1
    updated = _catalog_post(api, barcode, name="Food v2")
    assert updated.status_code == 201, updated.text
    assert updated.json()["product"]["product_name"] == "Food v2"


def _concurrent_catalog_insert(engine, user1_id, user2_id, barcode):
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    barrier = threading.Barrier(2, timeout=30)
    results = []
    errors = []

    def worker(user_id, name):
        db = Session()
        try:
            user = db.get(User, user_id)
            payload = CatalogFoodInput(
                barcode=barcode,
                name=name,
                brand="Brand",
                kcal=100,
                protein=5,
                carbs=10,
                fat=2,
            )
            barrier.wait()
            results.append(add_catalog_food(payload, db, user))
        except BaseException as error:  # noqa: BLE001 - recorded for the assertion
            errors.append(error)
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=(user1_id, "One food")),
        threading.Thread(target=worker, args=(user2_id, "Two food")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results, errors


def test_catalog_concurrent_insert_returns_409(api):
    barcode = _barcode()
    for _ in range(3):
        barcode = _barcode()
        results, errors = _concurrent_catalog_insert(
            api.engine, api.user1.id, api.user2.id, barcode
        )
        assert len(results) == 1, (results, errors)
        assert len(errors) == 1, (results, errors)
        assert getattr(errors[0], "status_code", None) == 409, errors[0]
        api.db.expire_all()
        assert api.db.query(CommunityFood).filter_by(barcode=barcode).count() == 1


# ---------------------------------------------------------------------------
# M4: city length
# ---------------------------------------------------------------------------


class _FakeResponse:
    is_error = False

    def json(self):
        return {"current": {"temperature_2m": 1}, "daily": {"time": []}}


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return _FakeResponse()


def test_city_maximum_length_is_accepted_with_coordinates(api, monkeypatch):
    monkeypatch.setattr(weather_api.httpx, "AsyncClient", _FakeAsyncClient)
    city = "Ж" * 120

    response = api.client.get(
        f"{PREFIX}/weather",
        params={"city": city, "latitude": 50.45, "longitude": 30.52},
    )

    assert response.status_code == 200, response.text
    api.db.refresh(api.user1)
    assert api.user1.last_city == city
    assert len(api.user1.last_city) == 120


def test_city_over_limit_is_rejected_before_the_database(api, monkeypatch):
    monkeypatch.setattr(weather_api.httpx, "AsyncClient", _FakeAsyncClient)
    city = "Ж" * 121

    with_coordinates = api.client.get(
        f"{PREFIX}/weather",
        params={"city": city, "latitude": 50.45, "longitude": 30.52},
    )
    without_coordinates = api.client.get(
        f"{PREFIX}/weather", params={"city": city}
    )

    assert with_coordinates.status_code == 422, with_coordinates.text
    assert without_coordinates.status_code == 422, without_coordinates.text
    api.db.refresh(api.user1)
    assert api.user1.last_city is None


def test_city_at_limit_without_coordinates_does_not_500(api, monkeypatch):
    monkeypatch.setattr(weather_api.httpx, "AsyncClient", _FakeAsyncClient)

    response = api.client.get(
        f"{PREFIX}/weather", params={"city": "Ж" * 120}
    )

    # The fake geocoder returns no results; the endpoint reports 404, not 500.
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# M5: healthcheck
# ---------------------------------------------------------------------------


def test_health_reports_ok_with_a_reachable_database(api, monkeypatch):
    monkeypatch.setattr(main_module, "SessionLocal", api.Session)

    response = api.client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}


def test_health_reports_503_without_leaking_details(api, monkeypatch):
    class BrokenSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            raise OperationalError(
                "SELECT 1",
                {},
                Exception("connection refused for hub@secret-host/secret-db"),
            )

    monkeypatch.setattr(main_module, "SessionLocal", lambda: BrokenSession())

    response = api.client.get("/health")

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "Database unavailable"}
    body = response.text.lower()
    assert "secret" not in body
    assert "select" not in body
    assert "postgres" not in body
