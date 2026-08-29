from importlib import reload


def test_settings_loads(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "fake-token")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    # Import config and ensure no SystemExit
    from app import config

    reload(config)
    s = config.get_settings()
    assert s.BOT_TOKEN == "fake-token"
