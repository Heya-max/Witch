"""Direct unit tests for the app.services core primitives (rate limiter, locks, metrics, favorites text)."""

import asyncio

import app.services.favorites as favs_mod
import app.services.locks as locks_mod
import app.services.metrics as metrics_mod
import app.services.rate_limiter as rate_limiter_mod
from app.player.models import Track


class FakeLimiterRedis:
    def __init__(self, value):
        self.value = value
        self.incr_calls = []
        self.expire_calls = []

    async def incr(self, key):
        self.incr_calls.append(key)
        return self.value

    async def expire(self, key, period):
        self.expire_calls.append((key, period))

    async def get(self, key):
        return self.value


def test_rate_limiter_allow_first_and_limit():
    redis = FakeLimiterRedis(1)
    limiter = rate_limiter_mod.RateLimiter(redis)
    assert asyncio.run(limiter.allow("k", limit=2, period=10)) is True
    assert redis.incr_calls == ["k"]
    assert redis.expire_calls == [("k", 10)]

    redis_over = FakeLimiterRedis(3)
    assert asyncio.run(rate_limiter_mod.RateLimiter(redis_over).allow("k", limit=2, period=10)) is False
    assert redis_over.expire_calls == []


def test_rate_limiter_get_count_paths():
    limiter = rate_limiter_mod.RateLimiter(FakeLimiterRedis(None))
    assert asyncio.run(limiter.get_count("k")) is None

    limiter_int = rate_limiter_mod.RateLimiter(FakeLimiterRedis("7"))
    assert asyncio.run(limiter_int.get_count("k")) == 7

    limiter_bad = rate_limiter_mod.RateLimiter(FakeLimiterRedis(b"nope"))
    assert asyncio.run(limiter_bad.get_count("k")) is None


class FakeSetRedis:
    def __init__(self, set_results=None, raise_on=None, eval_result=1):
        self.set_results = list(set_results or [])
        self.set_calls = []
        self.eval_calls = []
        self.raise_on = raise_on
        self.eval_result = eval_result

    async def set(self, name, token, nx=None, ex=None):
        self.set_calls.append((name, token, nx, ex))
        if self.raise_on == "set":
            raise RuntimeError("boom")
        return self.set_results.pop(0) if self.set_results else None

    async def eval(self, script, numkeys, name, token):
        self.eval_calls.append((script, numkeys, name, token))
        if self.raise_on == "eval":
            raise RuntimeError("boom")
        return self.eval_result


def test_lock_acquire_success_and_exhausted(monkeypatch):
    redis = FakeSetRedis([True])
    lock = locks_mod.RedisLock(redis)
    token = asyncio.run(lock.acquire("lock:a"))
    assert token
    assert redis.set_calls[0][1] == token
    assert redis.set_calls[0][2:] == (True, 10)

    redis_no = FakeSetRedis([None])
    assert asyncio.run(locks_mod.RedisLock(redis_no).acquire("lock:b", retries=0)) is None


def test_lock_acquire_retries_with_backoff(monkeypatch):
    sleeps = []
    redis = FakeSetRedis([None, True])

    async def fake_sleep(secs):
        sleeps.append(secs)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    lock = locks_mod.RedisLock(redis)
    token = asyncio.run(lock.acquire("lock:c", retries=3, backoff_base=0.5, jitter=True))
    assert token
    assert sleeps and all(0.25 <= s <= 0.75 for s in sleeps)


def test_lock_acquire_redis_error_falls_back_to_retry(monkeypatch):
    sleeps = []

    async def fake_sleep(secs):
        sleeps.append(secs)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    redis = FakeSetRedis([], raise_on="set")
    assert asyncio.run(locks_mod.RedisLock(redis).acquire("lock:d", retries=1)) is None
    assert sleeps


def test_lock_release_matched_mismatched_and_error():
    redis_ok = FakeSetRedis(eval_result=1)
    assert asyncio.run(locks_mod.RedisLock(redis_ok).release("lock:e", "tok")) is True
    assert redis_ok.eval_calls[0][2] == "lock:e"
    assert redis_ok.eval_calls[0][3] == "tok"

    redis_no = FakeSetRedis(eval_result=0)
    assert asyncio.run(locks_mod.RedisLock(redis_no).release("lock:f", "tok")) is False

    redis_err = FakeSetRedis(raise_on="eval")
    assert asyncio.run(locks_mod.RedisLock(redis_err).release("lock:g", "tok")) is False


def test_metrics_inc_get_no_bridge():
    m = metrics_mod.Metrics()
    m.inc("plays")
    m.inc("plays", 3)
    assert m.get("plays") == 4
    assert m.get("missing") == 0
    assert m.counters["plays"] == 4


def test_metrics_prom_bridge_and_suppressed_errors():
    calls = []

    class FakeCounter:
        def labels(self, name=None):
            self._name = name
            return self

        def inc(self, amount):
            calls.append((self._name, amount))

    metrics_mod.set_prometheus_counter(FakeCounter())
    try:
        m = metrics_mod.Metrics()
        m.inc("events", 2)
    finally:
        metrics_mod._set_prom_counter(None)
    assert calls == [("events", 2)]

    class BoomCounter:
        def labels(self, name=None):
            raise RuntimeError("prom client broke")

    metrics_mod.set_prometheus_counter(BoomCounter())
    try:
        m2 = metrics_mod.Metrics()
        m2.inc("events")  # must not raise
    finally:
        metrics_mod._set_prom_counter(None)
    assert m2.get("events") == 1


def test_favs_text_empty_and_rendered(monkeypatch):
    async def empty(sf, user_id):
        return []

    async def full(sf, user_id):
        return [
            Track(id="1", title="Song A", artist="Artist"),
            Track(id="2", title="Song B", artist="Artist"),
        ]

    monkeypatch.setattr(favs_mod, "list_favorites", empty)
    assert asyncio.run(favs_mod.favs_text(None, 1)) == "⭐ No favorites yet. Use /fav <query> to save one."

    monkeypatch.setattr(favs_mod, "list_favorites", full)
    text = asyncio.run(favs_mod.favs_text(None, 1))
    assert text == "⭐ Your favorites:\n1. Song A\n2. Song B"


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        class _Scalars:
            def all(self_):
                return self.rows

        return _Scalars()


class _Session:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return _Result(self.rows)

    def add(self, obj):
        self.added = obj

    async def commit(self):
        self.committed = True


class _Factory:
    def __init__(self, rows=()):
        self.rows = rows

    def __call__(self):
        return _Session(self.rows)


def test_favorites_add_duplicate_returns_false():
    track = Track(id="s1", title="Song", metadata={"hint": "x"})
    factory = _Factory(rows=["existing"])
    assert asyncio.run(favs_mod.add_favorite(factory, 42, track)) is False


def test_favorites_remove_out_of_range_returns_false():
    factory = _Factory(rows=[])
    assert asyncio.run(favs_mod.remove_favorite(factory, 42, 0)) is False
    assert asyncio.run(favs_mod.remove_favorite(factory, 42, -1)) is False
