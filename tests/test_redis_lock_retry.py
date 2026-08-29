import os
import sys

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class FlakyRedis:
    """Simulate a Redis that initially refuses to set a key, then later accepts."""

    def __init__(self):
        self.attempts = 0
        self.store = {}

    async def set(self, key, value, nx=False, ex=None):
        self.attempts += 1
        # Fail the first two attempts, then succeed
        if self.attempts <= 2:
            return False
        self.store[key] = value
        return True

    async def eval(self, script, numkeys, name, token):
        cur = self.store.get(name)
        if cur == token:
            del self.store[name]
            return 1
        return 0


@pytest.mark.asyncio
async def test_acquire_with_retries_succeeds():
    from app.services.locks import RedisLock

    redis = FlakyRedis()
    lock = RedisLock(redis)

    token = await lock.acquire("retry-lock", ttl=5, retries=3, backoff_base=0.01, jitter=False)
    assert token is not None
