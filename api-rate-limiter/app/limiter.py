"""Atomic Redis-backed fixed and sliding-window request limiters."""

from dataclasses import dataclass
from typing import Literal

from redis.asyncio import Redis

Algorithm = Literal["fixed", "sliding"]

# Use Redis server time to avoid clock disagreement across app instances.
# Sorted-set members are unique via an atomic per-key sequence.
SLIDING_LUA = """
local time = redis.call('TIME')
local now = time[1] * 1000 + math.floor(time[2] / 1000)
local start = now - tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', start)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[1]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry = oldest[2] and (tonumber(oldest[2]) + tonumber(ARGV[2]) - now) or tonumber(ARGV[2])
  return {0, count, math.max(1, retry)}
end
local seq = redis.call('INCR', KEYS[2])
redis.call('ZADD', KEYS[1], now, tostring(now) .. '-' .. tostring(seq))
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]))
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[2]))
return {1, count + 1, 0}
"""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    count: int
    retry_after_ms: int


class RateLimiter:
    def __init__(self, redis: Redis, limit: int, window_seconds: int):
        self.redis = redis
        self.limit = limit
        self.window_ms = window_seconds * 1000

    async def check(self, identity: str, algorithm: Algorithm) -> Decision:
        # Hash the supplied identity so arbitrary client input cannot create huge keys.
        import hashlib

        identity_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if algorithm == "fixed":
            # Both the window and TTL are derived from Redis TIME for consistency.
            # This script computes the time bucket server-side.
            result = await self.redis.eval(
                """
                local t = redis.call('TIME')
                local now = t[1] * 1000 + math.floor(t[2] / 1000)
                local period = tonumber(ARGV[2])
                local bucket = math.floor(now / period)
                local key = KEYS[1] .. ':' .. tostring(bucket)
                local count = redis.call('INCR', key)
                if count == 1 then
                  redis.call('PEXPIRE', key, period - (now % period) + 1000)
                end
                return {count <= tonumber(ARGV[1]) and 1 or 0, count,
                        period - (now % period)}
                """,
                1,
                f"rate:fixed:{identity_key}",
                self.limit,
                self.window_ms,
            )
        elif algorithm == "sliding":
            result = await self.redis.eval(
                SLIDING_LUA,
                2,
                f"rate:sliding:{identity_key}",
                f"rate:seq:{identity_key}",
                self.limit,
                self.window_ms,
            )
        else:
            raise ValueError("Unknown algorithm")
        return Decision(bool(result[0]), int(result[1]), int(result[2]))
