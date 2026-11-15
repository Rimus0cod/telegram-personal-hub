"""Per-user Redis token-bucket rate limiting.

The limiter is attached to the authenticated ``/api/v1`` routers, so it runs
after Telegram authentication and always uses the database user id. Counters
live only in Redis; the Lua script reads Redis ``TIME`` so every worker shares
one clock and one atomic check-and-consume operation.

When ``REDIS_URL`` is empty the limiter is disabled (useful for local tests).
When it is configured but Redis is unavailable, ordinary CRUD endpoints fail
open, while endpoints that call external providers fail closed with 503.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Mapping

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import REDIS_TIMEOUT_SECONDS, REDIS_URL
from app.dependencies.auth import current_user
from app.models.models import User

logger = logging.getLogger(__name__)
_last_failure_log = -60.0


@dataclass(frozen=True)
class Limit:
    """A token bucket: ``capacity`` tokens refilled over ``window_seconds``."""

    capacity: int
    window_seconds: float
    # External-provider endpoints must not run without a working limiter.
    fail_closed: bool = False

    @property
    def refill_per_second(self) -> float:
        return self.capacity / self.window_seconds


# Category limits are intentionally centralized. An endpoint can belong to
# several categories; every applicable bucket must allow the request.
CATEGORY_LIMITS: dict[str, Limit] = {
    "general": Limit(120, 60),
    "write": Limit(60, 60),
    # Both weather endpoints share one bucket, so switching endpoints cannot
    # bypass the Open-Meteo limit.
    "weather": Limit(30, 60, fail_closed=True),
    # Barcode lookup and catalog search share the Open Food Facts bucket.
    "nutrition_external": Limit(30, 60, fail_closed=True),
    "catalog_search": Limit(20, 60, fail_closed=True),
    "catalog_lookup_minute": Limit(10, 60, fail_closed=True),
    "catalog_lookup_hour": Limit(100, 3600, fail_closed=True),
}


# All-or-nothing token bucket. KEYS[i] is one category bucket; ARGV contains
# capacity/refill pairs in the same order. Redis TIME is authoritative; no
# worker-local clock is involved. On denial nothing is consumed. All keys get
# a TTL so inactive users disappear without a cleanup job.
_TOKEN_BUCKET_LUA = """
local time_parts = redis.call('TIME')
local now = tonumber(time_parts[1]) + tonumber(time_parts[2]) / 1000000
local states = {}
local allowed = 1
local retry = 0

for i = 1, #KEYS do
    local capacity = tonumber(ARGV[(i - 1) * 2 + 1])
    local refill = tonumber(ARGV[(i - 1) * 2 + 2])
    local values = redis.call('HMGET', KEYS[i], 'tokens', 'ts')
    local tokens = tonumber(values[1])
    local ts = tonumber(values[2])
    if tokens == nil or ts == nil then
        tokens = capacity
        ts = now
    end
    if now > ts then
        tokens = math.min(capacity, tokens + (now - ts) * refill)
    end
    tokens = math.min(capacity, tokens)
    states[i] = {tokens, capacity, refill}
    if tokens < 1 then
        allowed = 0
        local wait = (1 - tokens) / refill
        if wait > retry then
            retry = wait
        end
    end
end

if allowed == 0 then
    return {0, math.ceil(retry), 0}
end

local remaining = -1
for i = 1, #KEYS do
    local tokens = states[i][1] - 1
    if tokens < 0 then
        tokens = 0
    end
    local capacity = states[i][2]
    local refill = states[i][3]
    redis.call('HSET', KEYS[i], 'tokens', tostring(tokens), 'ts', tostring(now))
    local ttl = math.max(60, math.ceil(capacity / refill) + 60)
    redis.call('EXPIRE', KEYS[i], tostring(ttl))
    if remaining < 0 or tokens < remaining then
        remaining = tokens
    end
end

return {1, 0, math.floor(remaining)}
"""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: int
    remaining: int
    limit: int


def _bucket_key(user_id: int, category: str) -> str:
    # The internal database id is hashed so no user identifier or PII appears
    # in Redis keys. The category separates independent buckets.
    user_hash = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:32]
    return f"rl:v1:u:{user_hash}:{category}"


def categories_for(path: str, method: str) -> list[str]:
    """Map a request to every bucket that must allow it."""
    categories = ["general"]
    if path in ("/api/v1/weather", "/api/v1/weather/search"):
        categories.append("weather")
    elif path == "/api/v1/nutrition/catalog-search":
        categories.extend(("nutrition_external", "catalog_search"))
    elif path.startswith("/api/v1/nutrition/catalog/"):
        categories.extend(
            ("nutrition_external", "catalog_lookup_minute", "catalog_lookup_hour")
        )
    if method.upper() in {"POST", "PATCH", "DELETE"}:
        categories.append("write")
    return categories


class RateLimiter:
    """Thin async wrapper around the atomic Redis script."""

    def __init__(self, client: Redis | None, timeout_seconds: float | None = None):
        self.client = client
        self.timeout_seconds = (
            REDIS_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        )

    async def check(
        self,
        user_id: int,
        categories: list[str],
        limits: Mapping[str, Limit] | None = None,
    ) -> Decision | None:
        """Return a decision, or ``None`` when Redis cannot be consulted."""
        if self.client is None or not categories:
            return None
        source = limits or CATEGORY_LIMITS
        selected = [(category, source[category]) for category in categories]
        keys = [_bucket_key(user_id, category) for category, _ in selected]
        args: list[float | int] = []
        for _, limit in selected:
            args.extend((limit.capacity, limit.refill_per_second))
        # Reflect the strictest applicable bucket in the response header.
        limit_header = min(limit.capacity for _, limit in selected)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                raw = await self.client.eval(
                    _TOKEN_BUCKET_LUA, len(keys), *keys, *args
                )
            allowed = bool(int(raw[0]))
            retry_after = max(0, int(raw[1]))
            remaining = max(0, int(raw[2]))
            return Decision(
                allowed=allowed,
                retry_after=retry_after,
                remaining=remaining,
                limit=limit_header,
            )
        except (RedisError, OSError, TimeoutError, ValueError, TypeError, IndexError) as error:
            _log_failure(error)
            return None
        except Exception as error:  # noqa: BLE001 - never leak limiter internals
            _log_failure(error)
            return None


_client: Redis | None = None
_client_invalid = False


def get_limiter() -> RateLimiter | None:
    """Return the process limiter, or ``None`` when rate limiting is disabled."""
    global _client, _client_invalid
    if not REDIS_URL:
        return None
    if _client is None and not _client_invalid:
        try:
            _client = Redis.from_url(
                REDIS_URL,
                socket_timeout=REDIS_TIMEOUT_SECONDS,
                socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
                retry_on_timeout=False,
                decode_responses=True,
            )
        except Exception as error:  # noqa: BLE001 - invalid URL must not crash
            _client_invalid = True
            _log_failure(error)
    return RateLimiter(_client)


async def close_limiter() -> None:
    """Close the shared Redis client (used by tests and shutdown hooks)."""
    global _client, _client_invalid
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
    _client = None
    _client_invalid = False


def _log_failure(error: Exception) -> None:
    global _last_failure_log
    now = time.monotonic()
    if now - _last_failure_log < 60:
        return
    _last_failure_log = now
    # Only the exception class: no DSN, host, key or stack trace.
    logger.warning("Rate limiter unavailable (%s)", type(error).__name__)


async def enforce_rate_limit(
    request: Request,
    user: User = Depends(current_user),
) -> None:
    """FastAPI dependency applied to every authenticated router."""
    limiter = get_limiter()
    if limiter is None:
        return
    categories = categories_for(request.url.path, request.method)
    decision = await limiter.check(user.id, categories)
    if decision is None:
        if any(CATEGORY_LIMITS[category].fail_closed for category in categories):
            raise HTTPException(503, "Service temporarily unavailable")
        return
    if not decision.allowed:
        retry_after = max(1, decision.retry_after)
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={
                "Retry-After": str(retry_after),
                "RateLimit-Limit": str(decision.limit),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(retry_after),
            },
        )
