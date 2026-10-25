# API Rate Limiter

A Redis-backed FastAPI rate limiter supporting **sliding window** and **fixed window** algorithms, with atomic Lua execution, per-client limits, HTTP 429 / Retry-After, and integration tests.

## Quick start (Windows, with Docker Desktop)

1. Start Docker Desktop.
2. In PowerShell, `cd api-rate-limiter`.
3. Run `docker compose up --build -d`.
4. Open http://127.0.0.1:8000/docs or http://127.0.0.1:8000/api/products.
5. Run `docker compose down` to stop.

Hit `/api/products` six times within 60 seconds (without other clients using the same IP): the sixth and later requests return HTTP **429** with the `Retry-After` header. `/` and `/health` are not rate limited.

## Configuration

Copy `.env.example` to `.env` and change:
- `RATE_LIMIT`: number of accepted requests per window (default 5)
- `WINDOW_SECONDS`: period in seconds (default 60)
- `RATE_ALGORITHM`: `sliding` (default) or `fixed`

Restart the stack after configuration changes: `docker compose up -d --build --force-recreate`.

## Run tests

Tests require a running Redis. For example:

```powershell
docker compose up -d redis
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Run the API locally without Docker (Redis must be available on port 6379):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

## How it works

- A client is identified by its connecting IP address. Do **not** blindly trust X-Forwarded-For. Production apps should prefer an authenticated user ID or API key and appropriately configured trusted proxies.
- Redis executes Lua scripts atomically so concurrent requests do not exceed the configured limit across API workers.
- Fixed window: buckets time into fixed periods; can allow bursts near boundaries. Uses Redis server time and automatically expires counters.
- Sliding window: stores accepted request timestamps in a sorted set; evicts timestamps that have aged out, then permits or rejects. Uses Redis server time and unique members, and automatically expires keys.
- Redis errors return 503 instead of silently dropping limits (fail-closed).

## Trade-offs

- Sliding window stores up to `RATE_LIMIT` sorted-set entries per active identity, so it costs more memory than fixed window.
- IP-only identity is not a reliable user ID: NAT/shared networks and reverse proxies need special care.
- For a production service, add metrics/logging, authentication and proxy configuration, resource limits, and abuse-resistant identity handling.
- This demo is intended for local development. Docker ports bind to localhost only.
