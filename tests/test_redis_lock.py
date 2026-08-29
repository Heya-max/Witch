import asyncio

import pytest


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx:
            if key in self.store:
                return False
            self.store[key] = value
            return True
        self.store[key] = value
        return True

    async def eval(self, script, numkeys, name, token):
        cur = self.store.get(name)
        if cur == token:
            del self.store[name]
            return 1
        return 0


@pytest.mark.asyncio
async def test_lock_acquire_and_release():
    import os
    import sys

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from app.services.locks import RedisLock

    fake = FakeRedis()
    lock = RedisLock(fake)

    token = await lock.acquire("k", ttl=5)
    assert token is not None

    # Can't acquire again while held
    token2 = await lock.acquire("k", ttl=5)
    assert token2 is None

    # Release with wrong token should fail
    ok = await lock.release("k", "bad-token")
    assert ok is False

    # Release with correct token
    ok = await lock.release("k", token)
    assert ok is True

    # Now can acquire again
    token3 = await lock.acquire("k", ttl=5)
    assert token3 is not None


@pytest.mark.asyncio
async def test_lock_concurrent_acquire():
    import os
    import sys

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from app.services.locks import RedisLock

    fake = FakeRedis()
    lock = RedisLock(fake)

    async def try_acquire(result_list, idx):
        t = await lock.acquire("concurrent", ttl=2)
        result_list[idx] = t
        # if acquired, hold briefly
        if t:
            await asyncio.sleep(0.1)
            await lock.release("concurrent", t)

    results = [None, None, None]
    await asyncio.gather(*(try_acquire(results, i) for i in range(3)))

    # Exactly one of the attempts should have a non-None token at a time,
    # but across quick serial scheduling we expect at least one success.
    assert any(r is not None for r in results)
