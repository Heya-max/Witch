import asyncio
import shutil
import subprocess
import time

import pytest


def _compose_cmd() -> list:
    if shutil.which("docker") is not None:
        return ["docker", "compose"]
    if shutil.which("docker-compose") is not None:
        return ["docker-compose"]
    return []


def ensure_compose_redis_started():
    # Start only the redis service so we don't build everything
    subprocess.run(_compose_cmd() + ["up", "-d", "redis"], check=True)


def stop_compose_redis():
    subprocess.run(_compose_cmd() + ["rm", "-sf", "redis"], check=False)


@pytest.mark.integration
def test_redis_lock_and_metrics_exposure():
    # Requires docker compose and Docker running locally
    if not _compose_cmd():
        pytest.skip("docker compose not available in test environment")

    ensure_compose_redis_started()
    try:
        # wait for redis to be available
        time.sleep(2)

        import redis.asyncio as aioredis
        import requests
        from app.services.locks import RedisLock
        from app.services.metrics import set_prometheus_counter
        from prometheus_client import Counter, start_http_server

        r = aioredis.from_url("redis://localhost:6379")

        async def run_check():
            # verify lock against real redis
            lock = RedisLock(r)
            token = await lock.acquire("it-lock", ttl=5, retries=1, backoff_base=0.01, jitter=False)
            assert token is not None
            ok = await lock.release("it-lock", token)
            assert ok is True
            await r.aclose()

        asyncio.run(run_check())

        # Start Prometheus server and ensure metrics endpoint returns data after increment
        prom = Counter("app_events_total", "Application event counters", ["name"])
        set_prometheus_counter(prom)
        start_http_server(8001)
        prom.labels(name="locks.released.play").inc()

        resp = requests.get("http://localhost:8001/metrics", timeout=5)
        assert resp.status_code == 200
        assert "app_events_total" in resp.text
    finally:
        stop_compose_redis()
