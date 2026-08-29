from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = Field(..., description="Telegram bot token")
    API_ID: int | None = None
    API_HASH: str | None = None
    DATABASE_URL: str = Field(..., description="Async SQLAlchemy database URL")
    REDIS_URL: str = Field(..., description="Redis connection URL")
    LOG_LEVEL: str = "INFO"
    BOT_OWNER_ID: int | None = None
    METRICS_PORT: int | None = None

    model_config = SettingsConfigDict(env_file=".env")


def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        # Provide a clear startup error for missing required env vars
        raise SystemExit(f"Configuration error: {e}") from e
