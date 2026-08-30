import pytest
from app.config import OptionalInt, _empty_to_none, get_settings


@pytest.mark.parametrize(
    "_field,value,expected",
    [
        ("API_ID", "", None),
        ("API_ID", "123456", 123456),
        ("BOT_OWNER_ID", "", None),
        ("BOT_OWNER_ID", "99", 99),
        ("USERBOT_API_ID", "", None),
        ("METRICS_PORT", "", None),
        ("METRICS_PORT", "0", 0),
        ("METRICS_PORT", "9090", 9090),
    ],
)
def test_optional_int_env_empty_string_coerced_to_none(monkeypatch, _field, value, expected):
    base = {
        "BOT_TOKEN": "123:test",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv(_field, value)

    settings = get_settings()
    assert getattr(settings, _field) == expected


def test_empty_to_none_unit():
    assert _empty_to_none("") is None
    assert _empty_to_none("   ") is None
    assert _empty_to_none(0) == 0
    assert _empty_to_none(None) is None
    assert _empty_to_none("123") == "123"


def test_blank_required_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with pytest.raises(SystemExit):
        get_settings()


def test_env_file_does_not_break_typed_defaults(monkeypatch):
    # Docker passes `KEY: ${VAR:-}` which is an empty string when unset; the
    # settings must still load when a real .env is present (see composer).
    monkeypatch.setenv("BOT_TOKEN", "123:test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    settings = get_settings()
    # real environment wins over .env; an empty optional stays None; the ints
    # that matter for startup parse correctly even with a populated .env
    assert settings.BOT_TOKEN == "123:test"
    assert settings.METRICS_PORT is None or isinstance(settings.METRICS_PORT, int)


def test_optional_int_type_annotation_does_not_break_import():
    # sanity: the Annotated alias can be used in field declarations
    from pydantic import BaseModel

    class M(BaseModel):
        v: OptionalInt = None

    assert M(v="").v is None
    assert M(v=7).v == 7
