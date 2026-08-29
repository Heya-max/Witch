import pytest


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def incr(self, key):
        v = self.store.get(key, 0) + 1
        self.store[key] = v
        return v

    async def expire(self, key, seconds):
        # Not modeling expiry in fake
        return True

    async def get(self, key):
        v = self.store.get(key)
        if v is None:
            return None
        return str(v)


@pytest.mark.asyncio
async def test_rate_limiter_allows_until_limit():
    import os
    import sys

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from app.services.rate_limiter import RateLimiter

    fake = FakeRedis()
    rl = RateLimiter(fake)

    allowed = []
    for _ in range(3):
        allowed.append(await rl.allow("k", limit=2, period=60))

    assert allowed == [True, True, False]
