"""Stage 5.4b rate-limiter tests.

Real Redis integration tests are gated by ``TEST_REDIS_URL``. HTTP wiring,
429/503 behaviour and failure policies use deterministic limiter doubles so they
run everywhere; the atomic token bucket itself is exercised against a real
temporary Redis.
"""

import asyncio
import os
import time

import pytest
from fastapi.testclient import TestClient
import redis as sync_redis
from redis.asyncio import Redis

import app.api.weather as weather_api
import app.core.rate_limit as rate_limit
import app.main as main_module
from app.core.rate_limit import (
    CATEGORY_LIMITS,
    Decision,
    Limit,
    RateLimiter,
    categories_for,
)
from app.main import app as fastapi_app


# ---------------------------------------------------------------------------
# Real Redis: atomic token bucket
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def redis_url():
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    return url


async def _cleanup(client):
    keys = [key async for key in client.scan_iter("rl:v1:*")]
    if keys:
        await client.delete(*keys)
    await client.aclose()


async def _new_client(url):
    return Redis.from_url(url, decode_responses=True, socket_timeout=2)


def test_token_bucket_allows_up_to_capacity_and_denies_next(redis_url):
    async def scenario():
        client = await _new_client(redis_url)
        try:
            limiter = RateLimiter(client, timeout_seconds=1)
            limits = {"test": Limit(2, 60)}
            first = await limiter.check(910001, ["test"], limits)
            second = await limiter.check(910001, ["test"], limits)
            third = await limiter.check(910001, ["test"], limits)
            return first, second, third
        finally:
            await _cleanup(client)

    first, second, third = asyncio.run(scenario())
    assert first.allowed and first.remaining == 1
    assert second.allowed and second.remaining == 0
    assert not third.allowed
    assert third.retry_after >= 1
    assert third.remaining == 0


def test_token_bucket_refills_over_time(redis_url):
    async def scenario():
        client = await _new_client(redis_url)
        try:
            limiter = RateLimiter(client, timeout_seconds=1)
            limits = {"test": Limit(1, 0.2)}
            first = await limiter.check(910002, ["test"], limits)
            denied = await limiter.check(910002, ["test"], limits)
            await asyncio.sleep(0.25)
            recovered = await limiter.check(910002, ["test"], limits)
            return first, denied, recovered
        finally:
            await _cleanup(client)

    first, denied, recovered = asyncio.run(scenario())
    assert first.allowed
    assert not denied.allowed
    assert recovered.allowed


def test_parallel_requests_do_not_bypass_the_bucket(redis_url):
    async def scenario():
        client = await _new_client(redis_url)
        try:
            limiter = RateLimiter(client, timeout_seconds=1)
            limits = {"test": Limit(3, 60)}
            return await asyncio.gather(
                *(limiter.check(910003, ["test"], limits) for _ in range(12))
            )
        finally:
            await _cleanup(client)

    decisions = asyncio.run(scenario())
    assert sum(decision.allowed for decision in decisions) == 3


def test_two_limiter_instances_share_redis_state(redis_url):
    async def scenario():
        first_client = await _new_client(redis_url)
        second_client = await _new_client(redis_url)
        try:
            first = RateLimiter(first_client, timeout_seconds=1)
            second = RateLimiter(second_client, timeout_seconds=1)
            limits = {"test": Limit(2, 60)}
            return (
                await first.check(910004, ["test"], limits),
                await second.check(910004, ["test"], limits),
                await first.check(910004, ["test"], limits),
            )
        finally:
            await _cleanup(first_client)
            await second_client.aclose()

    first, second, third = asyncio.run(scenario())
    assert first.allowed and second.allowed
    assert not third.allowed


def test_users_and_categories_have_independent_buckets(redis_url):
    async def scenario():
        client = await _new_client(redis_url)
        try:
            limiter = RateLimiter(client, timeout_seconds=1)
            limits = {"a": Limit(1, 60), "b": Limit(1, 60)}
            return {
                "user1_a": await limiter.check(910005, ["a"], limits),
                "user1_a_again": await limiter.check(910005, ["a"], limits),
                "user1_b": await limiter.check(910005, ["b"], limits),
                "user2_a": await limiter.check(910006, ["a"], limits),
            }
        finally:
            await _cleanup(client)

    decisions = asyncio.run(scenario())
    assert decisions["user1_a"].allowed
    assert not decisions["user1_a_again"].allowed
    assert decisions["user1_b"].allowed
    assert decisions["user2_a"].allowed


def test_multi_category_check_is_all_or_nothing(redis_url):
    async def scenario():
        client = await _new_client(redis_url)
        try:
            limiter = RateLimiter(client, timeout_seconds=1)
            limits = {"a": Limit(5, 60), "b": Limit(1, 60)}
            uid = 910007
            first = await limiter.check(uid, ["a", "b"], limits)
            denied = await limiter.check(uid, ["a", "b"], limits)
            key_a = rate_limit._bucket_key(uid, "a")
            tokens_after_denial = float(await client.hget(key_a, "tokens"))
            third = await limiter.check(uid, ["a"], limits)
            tokens_after_third = float(await client.hget(key_a, "tokens"))
            return first, denied, tokens_after_denial, third, tokens_after_third
        finally:
            await _cleanup(client)

    first, denied, tokens_after_denial, third, tokens_after_third = asyncio.run(
        scenario()
    )
    assert first.allowed
    assert not denied.allowed
    # The denied multi-category request consumed nothing from bucket "a".
    assert abs(tokens_after_denial - 4.0) < 0.05
    assert third.allowed
    # One token was consumed; a tiny refill may have happened since the first
    # request, so allow a small tolerance.
    assert 2.9 <= tokens_after_third < 3.2


def test_configured_limits_and_category_mapping():
    assert CATEGORY_LIMITS["general"].capacity == 120
    assert CATEGORY_LIMITS["write"].capacity == 60
    assert CATEGORY_LIMITS["weather"].capacity == 30
    assert CATEGORY_LIMITS["nutrition_external"].capacity == 30
    assert CATEGORY_LIMITS["catalog_search"].capacity == 20
    assert CATEGORY_LIMITS["catalog_lookup_minute"].capacity == 10
    assert CATEGORY_LIMITS["catalog_lookup_hour"].capacity == 100
    assert CATEGORY_LIMITS["weather"].fail_closed
    assert not CATEGORY_LIMITS["general"].fail_closed

    assert categories_for("/api/v1/notes", "GET") == ["general"]
    assert categories_for("/api/v1/notes", "POST") == ["general", "write"]
    assert categories_for("/api/v1/weather", "GET") == ["general", "weather"]
    assert categories_for("/api/v1/weather/search", "GET") == [
        "general",
        "weather",
    ]
    assert categories_for("/api/v1/nutrition/catalog-search", "GET") == [
        "general",
        "nutrition_external",
        "catalog_search",
    ]
    assert categories_for("/api/v1/nutrition/catalog/12345678", "GET") == [
        "general",
        "nutrition_external",
        "catalog_lookup_minute",
        "catalog_lookup_hour",
    ]
    assert categories_for("/api/v1/nutrition/catalog", "POST") == [
        "general",
        "write",
    ]


# ---------------------------------------------------------------------------
# HTTP wiring and policies
# ---------------------------------------------------------------------------


class RecordingLimiter:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def check(self, user_id, categories, limits=None):
        self.calls.append({"user_id": user_id, "categories": categories})
        return self.decision


class FailingLimiter:
    def __init__(self):
        self.calls = 0

    async def check(self, user_id, categories, limits=None):
        self.calls += 1
        return None


class CountingLimiter:
    """Allows one request per user id, then denies."""

    def __init__(self):
        self.counts = {}

    async def check(self, user_id, categories, limits=None):
        self.counts[user_id] = self.counts.get(user_id, 0) + 1
        allowed = self.counts[user_id] == 1
        return Decision(allowed, 7, 0, 120)


class SlowClient:
    async def eval(self, *args, **kwargs):
        await asyncio.sleep(0.2)


def _use_limiter(monkeypatch, limiter):
    monkeypatch.setattr(rate_limit, "get_limiter", lambda: limiter)


class SyncRedisAdapter:
    """Run the real Lua script from the TestClient's event loop."""

    def __init__(self, url):
        self.client = sync_redis.Redis.from_url(url, decode_responses=True)

    async def eval(self, script, numkeys, *args):
        return await asyncio.to_thread(self.client.eval, script, numkeys, *args)

    def close(self):
        keys = list(self.client.scan_iter("rl:v1:*"))
        if keys:
            self.client.delete(*keys)
        self.client.close()


def test_http_429_with_real_redis(api, redis_url, monkeypatch):
    adapter = SyncRedisAdapter(redis_url)
    monkeypatch.setattr(
        rate_limit,
        "get_limiter",
        lambda: RateLimiter(adapter, timeout_seconds=1),
    )
    monkeypatch.setitem(rate_limit.CATEGORY_LIMITS, "general", Limit(1, 60))
    try:
        first = api.client.get("/api/v1/notes")
        second = api.client.get("/api/v1/notes")
    finally:
        adapter.close()

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert second.headers["RateLimit-Limit"] == "1"
    assert second.json() == {"detail": "Too many requests"}


def test_general_limit_returns_429_with_headers(api, monkeypatch):
    limiter = RecordingLimiter(Decision(False, 7, 0, 120))
    _use_limiter(monkeypatch, limiter)

    response = api.client.get("/api/v1/notes")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.headers["RateLimit-Limit"] == "120"
    assert response.headers["RateLimit-Remaining"] == "0"
    assert response.headers["RateLimit-Reset"] == "7"
    assert response.json() == {"detail": "Too many requests"}
    assert "rl:v1" not in response.text
    assert str(api.user1.id) not in response.text
    assert limiter.calls[0]["user_id"] == api.user1.id


def test_write_and_success_body_are_wired(api, monkeypatch):
    limiter = RecordingLimiter(Decision(True, 0, 5, 120))
    _use_limiter(monkeypatch, limiter)

    response = api.client.post(
        "/api/v1/notes", json={"title": "Rate limited", "content": "ok"}
    )

    assert response.status_code == 201, response.text
    assert limiter.calls[0]["categories"] == ["general", "write"]
    assert response.json()["title"] == "Rate limited"


@pytest.mark.parametrize(
    "path,mode,expected",
    [
        ("/api/v1/weather?latitude=50&longitude=30", "get", ["general", "weather"]),
        (
            "/api/v1/nutrition/catalog-search?q=milk",
            "get",
            ["general", "nutrition_external", "catalog_search"],
        ),
        (
            "/api/v1/nutrition/catalog/12345678",
            "get",
            [
                "general",
                "nutrition_external",
                "catalog_lookup_minute",
                "catalog_lookup_hour",
            ],
        ),
    ],
)
def test_expensive_endpoints_apply_all_categories(
    api, monkeypatch, path, mode, expected
):
    limiter = RecordingLimiter(Decision(False, 3, 0, 120))
    _use_limiter(monkeypatch, limiter)

    response = getattr(api.client, mode)(path)

    assert response.status_code == 429
    assert limiter.calls[0]["categories"] == expected


def test_different_users_do_not_share_limit(api, monkeypatch):
    _use_limiter(monkeypatch, CountingLimiter())

    first = api.client.get("/api/v1/notes")
    second = api.client.get("/api/v1/notes")
    api.holder["user"] = api.user2
    other_user = api.client.get("/api/v1/notes")

    assert first.status_code == 200
    assert second.status_code == 429
    assert other_user.status_code == 200


def test_forwarded_headers_do_not_replace_user_identity(api, monkeypatch):
    limiter = CountingLimiter()
    _use_limiter(monkeypatch, limiter)

    first = api.client.get(
        "/api/v1/notes", headers={"X-Forwarded-For": "203.0.113.10"}
    )
    second = api.client.get(
        "/api/v1/notes", headers={"X-Forwarded-For": "198.51.100.20"}
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert list(limiter.counts) == [api.user1.id]


def test_crud_fails_open_when_redis_is_unavailable(api, monkeypatch):
    _use_limiter(monkeypatch, FailingLimiter())

    assert api.client.get("/api/v1/notes").status_code == 200
    created = api.client.post(
        "/api/v1/notes", json={"title": "Still saved", "content": "ok"}
    )
    assert created.status_code == 201, created.text


def test_external_endpoints_fail_closed_when_redis_is_unavailable(api, monkeypatch):
    calls = {"n": 0}

    class FakeResponse:
        is_error = False

        def json(self):
            return {"current": {}, "daily": {}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            calls["n"] += 1
            return FakeResponse()

    monkeypatch.setattr(weather_api.httpx, "AsyncClient", FakeClient)
    _use_limiter(monkeypatch, FailingLimiter())

    weather = api.client.get("/api/v1/weather?latitude=50&longitude=30")
    catalog = api.client.get("/api/v1/nutrition/catalog-search?q=milk")
    barcode = api.client.get("/api/v1/nutrition/catalog/12345678")

    assert weather.status_code == 503
    assert catalog.status_code == 503
    assert barcode.status_code == 503
    assert weather.json() == {"detail": "Service temporarily unavailable"}
    assert "redis" not in weather.text.lower()
    assert calls["n"] == 0


def test_redis_timeout_is_controlled(api, monkeypatch):
    _use_limiter(monkeypatch, RateLimiter(SlowClient(), timeout_seconds=0.05))

    crud = api.client.get("/api/v1/notes")
    external = api.client.get("/api/v1/weather?latitude=50&longitude=30")

    assert crud.status_code == 200
    assert external.status_code == 503


def test_health_is_not_user_rate_limited(api, monkeypatch):
    limiter = FailingLimiter()
    _use_limiter(monkeypatch, limiter)
    monkeypatch.setattr(main_module, "SessionLocal", api.Session)

    response = api.client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert limiter.calls == 0


def test_authentication_runs_before_rate_limiting(monkeypatch):
    limiter = FailingLimiter()
    _use_limiter(monkeypatch, limiter)
    fastapi_app.dependency_overrides.clear()

    response = TestClient(fastapi_app).get("/api/v1/notes")

    assert response.status_code == 401
    assert limiter.calls == 0
