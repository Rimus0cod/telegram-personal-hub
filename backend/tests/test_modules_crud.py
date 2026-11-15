"""M8 regressions: CRUD, validation and isolation for Notes, Tasks, Nutrition.

Uses the shared ``api`` fixture (SQLite and PostgreSQL, two independent users)
from ``conftest.py``.
"""

import uuid
from datetime import date, timedelta

PREFIX = "/api/v1"


def _switch(api, user):
    api.holder["user"] = user


def _barcode():
    return str(600_000_000_000 + uuid.uuid4().int % 100_000_000_000)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def test_notes_crud_and_missing_ids(api):
    created = api.client.post(
        f"{PREFIX}/notes", json={"title": "Title", "content": "Body"}
    )
    assert created.status_code == 201, created.text
    note_id = created.json()["id"]

    listed = api.client.get(f"{PREFIX}/notes")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [note_id]

    fetched = api.client.get(f"{PREFIX}/notes/{note_id}")
    assert fetched.status_code == 200
    assert fetched.json()["content"] == "Body"

    patched = api.client.patch(
        f"{PREFIX}/notes/{note_id}", json={"content": "Updated"}
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Title"
    assert patched.json()["content"] == "Updated"

    deleted = api.client.delete(f"{PREFIX}/notes/{note_id}")
    assert deleted.status_code == 204
    assert api.client.get(f"{PREFIX}/notes/{note_id}").status_code == 404
    assert api.client.patch(
        f"{PREFIX}/notes/{note_id}", json={"title": "x"}
    ).status_code == 404
    assert api.client.delete(f"{PREFIX}/notes/{note_id}").status_code == 404


def test_notes_validation_and_search(api):
    for body in ({}, {"title": ""}, {"title": "x" * 201}, {"title": "ok", "content": "y" * 20001}):
        response = api.client.post(f"{PREFIX}/notes", json=body)
        assert response.status_code == 422, (body, response.text)

    first = api.client.post(
        f"{PREFIX}/notes", json={"title": "Grocery list", "content": "milk"}
    ).json()
    second = api.client.post(
        f"{PREFIX}/notes", json={"title": "Ideas", "content": "grocery app"}
    ).json()

    found = api.client.get(f"{PREFIX}/notes", params={"q": "grocery"})
    assert {item["id"] for item in found.json()} == {first["id"], second["id"]}

    assert api.client.get(f"{PREFIX}/notes", params={"q": "x" * 101}).status_code == 422

    # Search term with SQL syntax is treated as text, not as a predicate.
    injected = api.client.get(f"{PREFIX}/notes", params={"q": "' OR 1=1 --"})
    assert injected.status_code == 200
    assert injected.json() == []


def test_notes_are_isolated_between_users(api):
    note = api.client.post(
        f"{PREFIX}/notes", json={"title": "Alice only", "content": "secret"}
    ).json()

    _switch(api, api.user2)
    assert api.client.get(f"{PREFIX}/notes").json() == []
    assert api.client.get(f"{PREFIX}/notes/{note['id']}").status_code == 404
    assert api.client.patch(
        f"{PREFIX}/notes/{note['id']}", json={"title": "stolen"}
    ).status_code == 404
    assert api.client.delete(f"{PREFIX}/notes/{note['id']}").status_code == 404
    assert api.client.get(f"{PREFIX}/notes", params={"q": "Alice"}).json() == []

    _switch(api, api.user1)
    assert api.client.get(f"{PREFIX}/notes/{note['id']}").json()["title"] == "Alice only"


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def test_tasks_crud_filters_and_missing_ids(api):
    created = api.client.post(
        f"{PREFIX}/tasks", json={"title": "First", "priority": "low"}
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["completed"] is False
    assert task["priority"] == "low"
    assert task["due_date"] is None

    assert [item["id"] for item in api.client.get(f"{PREFIX}/tasks").json()] == [task["id"]]
    assert len(api.client.get(f"{PREFIX}/tasks", params={"status": "active"}).json()) == 1
    assert api.client.get(f"{PREFIX}/tasks", params={"status": "completed"}).json() == []

    patched = api.client.patch(
        f"{PREFIX}/tasks/{task['id']}",
        json={"completed": True, "title": "Done", "due_date": "2031-05-06T07:08:09Z"},
    )
    assert patched.status_code == 200
    assert patched.json()["completed"] is True
    assert patched.json()["due_date"] is not None

    completed = api.client.get(f"{PREFIX}/tasks", params={"status": "completed"})
    assert [item["id"] for item in completed.json()] == [task["id"]]

    sorted_due = api.client.get(f"{PREFIX}/tasks", params={"sort": "due"})
    assert sorted_due.status_code == 200

    assert api.client.delete(f"{PREFIX}/tasks/{task['id']}").status_code == 204
    assert api.client.get(f"{PREFIX}/tasks/{task['id']}").status_code == 404
    assert api.client.patch(
        f"{PREFIX}/tasks/{task['id']}", json={"title": "x"}
    ).status_code == 404
    assert api.client.delete(f"{PREFIX}/tasks/{task['id']}").status_code == 404


def test_tasks_validation_and_patch_semantics(api):
    for body in (
        {},
        {"title": ""},
        {"title": "x" * 201},
        {"title": "ok", "description": "y" * 10001},
        {"title": "ok", "priority": "urgent"},
    ):
        assert api.client.post(f"{PREFIX}/tasks", json=body).status_code == 422, body

    assert api.client.get(f"{PREFIX}/tasks", params={"status": "nope"}).status_code == 422
    assert api.client.get(f"{PREFIX}/tasks", params={"sort": "nope"}).status_code == 422

    task = api.client.post(
        f"{PREFIX}/tasks", json={"title": "Keep", "description": "Body"}
    ).json()
    assert api.client.patch(f"{PREFIX}/tasks/{task['id']}", json={}).status_code == 200
    assert api.client.patch(
        f"{PREFIX}/tasks/{task['id']}", json={"priority": "urgent"}
    ).status_code == 422
    unchanged = api.client.get(f"{PREFIX}/tasks/{task['id']}").json()
    assert unchanged["title"] == "Keep"
    assert unchanged["description"] == "Body"
    assert unchanged["priority"] == "medium"

    reset = api.client.patch(
        f"{PREFIX}/tasks/{task['id']}", json={"due_date": None}
    )
    assert reset.status_code == 200
    assert reset.json()["due_date"] is None


def test_tasks_are_isolated_between_users(api):
    task = api.client.post(f"{PREFIX}/tasks", json={"title": "Alice task"}).json()

    _switch(api, api.user2)
    assert api.client.get(f"{PREFIX}/tasks").json() == []
    assert api.client.get(f"{PREFIX}/tasks/{task['id']}").status_code == 404
    assert api.client.patch(
        f"{PREFIX}/tasks/{task['id']}", json={"completed": True}
    ).status_code == 404
    assert api.client.delete(f"{PREFIX}/tasks/{task['id']}").status_code == 404

    _switch(api, api.user1)
    assert api.client.get(f"{PREFIX}/tasks/{task['id']}").status_code == 200


# ---------------------------------------------------------------------------
# Nutrition: meals and copy-day
# ---------------------------------------------------------------------------


def _meal(api, day, name="Meal", **overrides):
    body = {
        "day": day,
        "meal_type": "lunch",
        "name": name,
        "calories": 500,
        "protein": 30,
        "carbs": 40,
        "fat": 20,
        "ingredients": "chicken",
        "serving_grams": 200,
        **overrides,
    }
    return api.client.post(f"{PREFIX}/nutrition", json=body)


def test_meals_crud_portion_and_missing_ids(api):
    day = date.today().isoformat()
    created = _meal(api, day)
    assert created.status_code == 201, created.text
    meal = created.json()
    assert meal["serving_grams"] == 200

    listed = api.client.get(f"{PREFIX}/nutrition", params={"from": day, "to": day})
    assert [item["id"] for item in listed.json()] == [meal["id"]]

    portion = api.client.patch(
        f"{PREFIX}/nutrition/{meal['id']}", json={"serving_grams": 100}
    )
    assert portion.status_code == 200
    assert portion.json()["calories"] == 250
    assert portion.json()["protein"] == 15.0
    assert portion.json()["serving_grams"] == 100

    assert api.client.patch(
        f"{PREFIX}/nutrition/999999999", json={"serving_grams": 100}
    ).status_code == 404
    assert api.client.delete(f"{PREFIX}/nutrition/999999999").status_code == 404

    assert api.client.delete(f"{PREFIX}/nutrition/{meal['id']}").status_code == 204
    assert api.client.get(
        f"{PREFIX}/nutrition", params={"from": day, "to": day}
    ).json() == []


def test_meals_validation_and_range_limits(api):
    day = date.today().isoformat()

    invalid_bodies = [
        {"day": day, "meal_type": "brunch", "name": "x", "calories": 1},
        {"day": day, "meal_type": "lunch", "name": "x", "calories": 20001},
        {"day": day, "meal_type": "lunch", "name": "", "calories": 1},
        {"day": day, "meal_type": "lunch", "name": "x", "calories": 1, "serving_grams": 0},
        {"day": day, "meal_type": "lunch", "name": "x", "calories": 1, "protein": -1},
    ]
    for body in invalid_bodies:
        assert api.client.post(f"{PREFIX}/nutrition", json=body).status_code == 422, body

    assert api.client.get(
        f"{PREFIX}/nutrition",
        params={"from": day, "to": (date.today() + timedelta(days=14)).isoformat()},
    ).status_code == 400
    assert api.client.get(
        f"{PREFIX}/nutrition",
        params={"from": (date.today() + timedelta(days=1)).isoformat(), "to": day},
    ).status_code == 400

    # Upper bounds remain valid.
    accepted = _meal(
        api,
        day,
        calories=20000,
        serving_grams=10000,
        protein=2000,
        carbs=2000,
        fat=2000,
    )
    assert accepted.status_code == 201, accepted.text


def test_copy_day_and_isolation(api):
    today = date.today()
    source = today.isoformat()
    target = (today + timedelta(days=1)).isoformat()

    assert api.client.post(
        f"{PREFIX}/nutrition/copy-day",
        json={"source_day": source, "target_day": target},
    ).status_code == 404
    assert api.client.post(
        f"{PREFIX}/nutrition/copy-day",
        json={"source_day": source, "target_day": source},
    ).status_code == 400

    assert _meal(api, source, name="One").status_code == 201
    assert _meal(api, source, name="Two").status_code == 201

    copied = api.client.post(
        f"{PREFIX}/nutrition/copy-day",
        json={"source_day": source, "target_day": target},
    )
    assert copied.status_code == 201, copied.text
    assert copied.json()["copied"] == 2
    target_rows = api.client.get(
        f"{PREFIX}/nutrition", params={"from": target, "to": target}
    ).json()
    assert len(target_rows) == 2

    _switch(api, api.user2)
    assert api.client.get(
        f"{PREFIX}/nutrition", params={"from": source, "to": target}
    ).json() == []
    assert api.client.post(
        f"{PREFIX}/nutrition/copy-day",
        json={"source_day": source, "target_day": (today + timedelta(days=2)).isoformat()},
    ).status_code == 404


# ---------------------------------------------------------------------------
# Nutrition: recipes, preferences, shared catalog
# ---------------------------------------------------------------------------


def _recipe(name="Recipe", meal_type="dinner", grams=200):
    return {
        "name": name,
        "meal_type": meal_type,
        "ingredients": [
            {
                "name": "Rice",
                "category": "Grains",
                "kcal": 130,
                "p": 3,
                "c": 28,
                "f": 0,
                "grams": grams,
            }
        ],
    }


def test_recipes_crud_upsert_and_validation(api):
    created = api.client.post(f"{PREFIX}/nutrition/recipes", json=_recipe())
    assert created.status_code == 201, created.text
    recipe_id = created.json()["id"]

    listed = api.client.get(f"{PREFIX}/nutrition/recipes").json()
    assert [item["id"] for item in listed] == [recipe_id]

    upserted = api.client.post(
        f"{PREFIX}/nutrition/recipes", json=_recipe(grams=300)
    )
    assert upserted.status_code == 201
    assert upserted.json()["id"] == recipe_id
    assert upserted.json()["ingredients"][0]["grams"] == 300
    assert len(api.client.get(f"{PREFIX}/nutrition/recipes").json()) == 1

    for body in (
        {**_recipe(), "ingredients": []},
        {**_recipe(), "meal_type": "brunch"},
        {**_recipe(), "ingredients": [{**_recipe()["ingredients"][0], "grams": 0}]},
        {**_recipe(), "ingredients": [{**_recipe()["ingredients"][0], "kcal": 10001}]},
    ):
        assert api.client.post(
            f"{PREFIX}/nutrition/recipes", json=body
        ).status_code == 422, body

    assert api.client.delete(f"{PREFIX}/nutrition/recipes/999999999").status_code == 404
    assert api.client.delete(f"{PREFIX}/nutrition/recipes/{recipe_id}").status_code == 204
    assert api.client.get(f"{PREFIX}/nutrition/recipes").json() == []


def test_recipes_and_preferences_are_isolated(api):
    recipe = api.client.post(f"{PREFIX}/nutrition/recipes", json=_recipe("Alice")).json()
    targets = {
        "calorie_target": 2400,
        "protein_target": 140,
        "carbs_target": 280,
        "fat_target": 75,
    }
    assert api.client.patch(f"{PREFIX}/auth/preferences", json=targets).status_code == 200
    assert api.client.get(f"{PREFIX}/auth/me").json()["calorie_target"] == 2400

    _switch(api, api.user2)
    assert api.client.get(f"{PREFIX}/nutrition/recipes").json() == []
    assert api.client.delete(
        f"{PREFIX}/nutrition/recipes/{recipe['id']}"
    ).status_code == 404
    assert api.client.get(f"{PREFIX}/auth/me").json()["calorie_target"] == 2000
    assert api.client.patch(
        f"{PREFIX}/auth/preferences", json={**targets, "calorie_target": 499}
    ).status_code == 422
    assert api.client.patch(
        f"{PREFIX}/auth/preferences", json={**targets, "calorie_target": 10001}
    ).status_code == 422

    _switch(api, api.user1)
    assert len(api.client.get(f"{PREFIX}/nutrition/recipes").json()) == 1


def test_community_catalog_is_shared_but_protected(api):
    barcode = _barcode()
    payload = {
        "barcode": barcode,
        "name": "Shared",
        "brand": "Brand",
        "kcal": 100,
        "protein": 5,
        "carbs": 10,
        "fat": 2,
    }
    created = api.client.post(f"{PREFIX}/nutrition/catalog", json=payload)
    assert created.status_code == 201, created.text

    _switch(api, api.user2)
    lookup = api.client.get(f"{PREFIX}/nutrition/catalog/{barcode}")
    assert lookup.status_code == 200
    assert lookup.json()["found"] is True
    assert api.client.post(
        f"{PREFIX}/nutrition/catalog", json={**payload, "name": "Taken"}
    ).status_code == 409
    assert api.client.post(
        f"{PREFIX}/nutrition/catalog", json={**payload, "barcode": "bad"}
    ).status_code == 422

    _switch(api, api.user1)
    updated = api.client.post(
        f"{PREFIX}/nutrition/catalog", json={**payload, "name": "Updated"}
    )
    assert updated.status_code == 201
    assert updated.json()["product"]["product_name"] == "Updated"
