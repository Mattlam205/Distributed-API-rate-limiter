"""FastAPI app exposing Redis-backed rate-limited endpoints."""

import os
from contextlib import asynccontextmanager
from math import ceil

from fastapi import Depends, FastAPI, HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.limiter import Algorithm, RateLimiter

LIMIT = int(os.getenv("RATE_LIMIT", "5"))
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "60"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ALGORITHM: Algorithm = os.getenv("RATE_ALGORITHM", "sliding")  # type: ignore[assignment]

if LIMIT <= 0 or WINDOW_SECONDS <= 0:
    raise ValueError("RATE_LIMIT and WINDOW_SECONDS must be positive")
if ALGORITHM not in ("fixed", "sliding"):
    raise ValueError("RATE_ALGORITHM must be fixed or sliding")


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.redis = redis
    app.state.limiter = RateLimiter(redis, LIMIT, WINDOW_SECONDS)
    try:
        await redis.ping()
        yield
    finally:
        await redis.aclose()


app = FastAPI(title="API Rate Limiter", version="1.0.0", lifespan=lifespan)


async def limit_requests(request: Request) -> None:
    # Only trust the actual connecting peer, not user-controlled X-Forwarded-For.
    # Behind a trusted reverse proxy, configure an authenticated API key/user ID.
    identity = request.client.host if request.client else "unknown"
    try:
        decision = await request.app.state.limiter.check(identity, ALGORITHM)
    except RedisError as exc:
        # Fail closed: don't silently disable limits during Redis outage.
        raise HTTPException(status_code=503, detail="Rate limiter unavailable") from exc

    if not decision.allowed:
        seconds = max(1, ceil(decision.retry_after_ms / 1000))
        raise HTTPException(
            status_code=429,
            detail="Too Many Requests",
            headers={"Retry-After": str(seconds)},
        )


@app.get("/")
async def home():
    return {"message": "API Rate Limiter is running", "algorithm": ALGORITHM}


@app.get("/health")
async def health(request: Request):
    try:
        await request.app.state.redis.ping()
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    return {"status": "ok"}


@app.get("/api/products", dependencies=[Depends(limit_requests)])
async def products():
    return {"products": ["Laptop", "Keyboard", "Mouse"]}
