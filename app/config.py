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
    # Prometheus metrics endpoint. Defaults to on (127.0.0.1 binding inside the
    # container); set to 0 to disable entirely.
    METRICS_PORT: int | None = 9090

    # Queue health / limits.
    # - QUEUE_MAX_SIZE caps tracks per chat so a runaway playlist can't wedge
    #   the player or balloon the database (0 disables the cap).
    QUEUE_MAX_SIZE: int = 200
    # - QUEUE_MAX_AGE_SECONDS: persisted entries older than this are pruned on
    #   startup (0 disables). Stale rows from earlier runs used to poison the
    #   queue after a restart.
    QUEUE_MAX_AGE_SECONDS: int = 86400
    # - PLAY_MAX_RETRIES: times a track that fails to start is retried before
    #   being dropped instead of wedging the queue forever (0 = never drop).
    PLAY_MAX_RETRIES: int = 2

    # Optional userbot "assistant": a user session that drives PyTgCalls and can
    # create/join voice chats itself (bots cannot create group calls). Point
    # either USERBOT_SESSION (a *.session file name in the workdir) or
    # USERBOT_SESSION_STRING (an in-memory session string) to enable it.
    USERBOT_SESSION: str | None = None
    USERBOT_SESSION_STRING: str | None = None
    USERBOT_API_ID: int | None = None
    USERBOT_API_HASH: str | None = None

    model_config = SettingsConfigDict(env_file=".env")


def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        # Provide a clear startup error for missing required env vars
        raise SystemExit(f"Configuration error: {e}") from e
