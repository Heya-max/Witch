import app.bot.assistant as assistant_mod
from app.bot.assistant import create_assistant_client
from app.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "BOT_TOKEN": "bot-token",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379/0",
        # Explicitly disable the userbot assistant; otherwise Settings may pick
        # up USERBOT_*/API_* values from the local .env file.
        "USERBOT_SESSION": None,
        "USERBOT_SESSION_STRING": None,
        "USERBOT_API_ID": None,
        "USERBOT_API_HASH": None,
    }
    base.update(overrides)
    return Settings(**base)


def test_assistant_not_configured_returns_none():
    assert create_assistant_client(_settings()) is None


class FakeClient:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs


def test_assistant_session_file(monkeypatch):
    monkeypatch.setattr(assistant_mod, "Client", FakeClient)
    client = create_assistant_client(
        _settings(USERBOT_SESSION="user", USERBOT_API_ID=123, USERBOT_API_HASH="hh")
    )
    assert client is not None
    assert client.name == "user"
    assert client.kwargs["api_id"] == 123
    assert client.kwargs["api_hash"] == "hh"


def test_assistant_session_string_falls_back_to_api_credentials(monkeypatch):
    monkeypatch.setattr(assistant_mod, "Client", FakeClient)
    client = create_assistant_client(
        _settings(USERBOT_SESSION_STRING="abc", API_ID=42, API_HASH="bot-hash")
    )
    assert client is not None
    assert client.kwargs["session_string"] == "abc"
    assert client.kwargs["api_id"] == 42
    assert client.kwargs["api_hash"] == "bot-hash"


def test_assistant_needs_api_credentials(monkeypatch):
    monkeypatch.setattr(assistant_mod, "Client", FakeClient)
    assert (
        create_assistant_client(
            _settings(USERBOT_SESSION="user", API_ID=None, API_HASH=None)
        )
        is None
    )
