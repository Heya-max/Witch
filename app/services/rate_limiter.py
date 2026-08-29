class RateLimiter:
    """Simple Redis-backed rate limiter using INCR+EXPIRE.

    Usage:
        allowed = await rate_limiter.allow("user:123:play", limit=5, period=60)
    """

    def __init__(self, redis_client) -> None:
        self.redis = redis_client

    async def allow(self, key: str, limit: int, period: int) -> bool:
        """Return True if allowed, False if limit exceeded."""
        # Use INCR; if first increment, set expiry
        val = await self.redis.incr(key)
        if val == 1:
            # set expiration in seconds
            await self.redis.expire(key, period)
        return val <= limit

    async def get_count(self, key: str) -> int | None:
        v = await self.redis.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except Exception:
            return None
