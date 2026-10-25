"""Integration tests: require running Redis at REDIS_URL."""

import asyncio
import uuid

import httpx
import pytest
from redis.asyncio import Redis

from app.limiter import RateLimiter
from app.main import REDIS_URL, app


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", ["fixed", "sliding"])
async def test_limit_and_reject(algorithm):
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis.ping()
        limiter = RateLimiter(redis, limit=3, window_seconds=30)
        identity = uuid.uuid4().hex
        results = [await limiter.check(identity, algorithm) for _ in range(4)]
        assert [r.allowed for r in results] == [True, True, True, False]
        assert results[-1].retry_after_ms > 0
    finally:
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", ["fixed", "sliding"])
async def test_concurrent_requests_are_atomic(algorithm):
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        limiter = RateLimiter(redis, limit=5, window_seconds=30)
        identity = uuid.uuid4().hex
        responses = await asyncio.gather(*(limiter.check(identity, algorithm) for _ in range(20)))
        assert sum(d.allowed for d in responses) == 5
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_endpoint_returns_429_and_retry_after():
    # Uses a unique mock identity via ASGITransport's configurable client address.
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(f"test-{uuid.uuid4().hex}", 12345)),
            base_url="http://testserver",
        ) as client:
            responses = [await client.get("/api/products") for _ in range(6)]
            assert [r.status_code for r in responses] == [200] * 5 + [429]
            assert int(responses[-1].headers["Retry-After"]) >= 1
