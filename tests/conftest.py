import asyncio
import sys


def pytest_sessionstart(session):
    if sys.platform == "win32":
        # asyncpg (used by the Postgres integration test) only supports the
        # Selector event loop, but Python defaults to Proactor on Windows.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
