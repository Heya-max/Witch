import asyncio
import logging
import random
import uuid

logger = logging.getLogger(__name__)


class RedisLock:
    """A minimal distributed lock using Redis SET NX EX and safe release via token.

    Features:
    - Optional retries with exponential backoff on acquire.
    - Safe release using a small Lua script that checks the token.
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    async def acquire(
        self, name: str, ttl: int = 10, retries: int = 0, backoff_base: float = 0.1, jitter: bool = True
    ) -> str | None:
        """Attempt to acquire the lock.

        Args:
            name: lock key name
            ttl: expiration in seconds
            retries: number of additional attempts if initial acquire fails
            backoff_base: base sleep time for exponential backoff
            jitter: whether to add random jitter

        Returns:
            token str if acquired, else None
        """
        token = str(uuid.uuid4())
        attempt = 0
        while True:
            try:
                ok = await self.redis.set(name, token, nx=True, ex=ttl)
            except Exception:
                logger.exception("Redis set failed when acquiring lock %s", name)
                ok = False

            if ok:
                logger.debug("Acquired lock %s token=%s", name, token)
                return token

            if attempt >= retries:
                logger.debug("Failed to acquire lock %s after %d attempts", name, attempt + 1)
                return None

            # backoff before retrying
            attempt += 1
            sleep_for = backoff_base * (2 ** (attempt - 1))
            if jitter:
                sleep_for = sleep_for * (0.5 + random.random() * 0.5)
            await asyncio.sleep(sleep_for)

    async def release(self, name: str, token: str) -> bool:
        # Safe release: only delete if token matches
        script = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"
        try:
            res = await self.redis.eval(script, 1, name, token)
            ok = res == 1
            if not ok:
                logger.debug("Failed to release lock %s (token mismatch)", name)
            return ok
        except Exception:
            logger.exception("Redis eval failed when releasing lock %s", name)
            return False
