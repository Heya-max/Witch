# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Expanded test suite to 41 files / 378 passing tests, lifting `app/` coverage to
  99% (the only uncovered statement is the `if __name__ == "__main__"` guard in
  `main.py`).
- Playback (`app/bot/handlers/playback.py`) and player queue/manager
  (`app/player/queue.py`, `app/player/manager.py`) now reach 100% coverage,
  exercising lock-failure metric paths, rate-limiter outages, inline-picker edge
  cases, admin-guard denials, and player error branches.
- Covered all services (`rate_limiter`, `locks`, `metrics`, `favorites`) and
  source providers (`simple_provider`, `yt_dlp_provider`) to 100%.
- Postgres integration tests (`tests/test_integration_postgres.py`) exercising
  queue persistence across instances, stale-row pruning, and favorites against
  a real Postgres (picked up from the CI `postgres` service or the
  docker-compose host mapping; skipped when neither is reachable).
- CI coverage gate: the test job now runs `--cov=app --cov-fail-under=100`.
  The `main.py` `__main__` guard is `# pragma: no cover`, so the suite reports a
  literal 100% for `app/`.
- `tests/conftest.py` selects the Windows SelectorEventLoop policy so asyncpg
  integration tests pass on Windows developers' machines.

### Changed

- CI: the test job now provisions a `postgres:15` service alongside Redis, so
  both integration tests run in every matrix job.
- CI: install the project's dev dependencies in the lint job so mypy has access
  to the full dependency set.
- Error messages: replaced generic "Check logs." text with actionable
  "Try again later/moment" phrasing across playback, media, and favorites handlers.
- Improved the favorites database-unavailable message to hide internal Postgres
  details from end users.
- Added a `.dockerignore` to keep the build context small and avoid leaking
  session files / `.env` into images.
- Fixed a missing `@pytest.mark.asyncio` on `test_player_advance_on_engine_finish`
  so the test runs instead of being silently skipped.
- Added `pytest-cov` to the dev dependencies and a `[tool.coverage]` section so
  coverage reports are consistent anywhere `pytest` runs.

## [0.1.0] - 2026-08-30

### Added

- Telegram voice-chat music bot with `/play` search across SoundCloud → YouTube.
- Inline result picker showing title + duration.
- Voice-chat streaming via py-tgcalls 2.3 with queue management
  (`/queue`, `/playnext`, `/skip`, `/rm`, `/move`, `/shuffle`).
- Optional userbot assistant that creates/joins voice chats automatically.
- `/download` (`/dl`) sends audio files into the chat.
- `/lyrics` (`/ly`) with a 1h cache and now-playing fallback.
- Favorites (`/fav`, `/favs`, `/unfav`) persisted in Postgres.
- Inline search for private-chat audio delivery.
- URL validation to prevent non-audio junk from reaching playback.
- Queue health: per-chat cap, startup stale-row pruning, corrupt-row deletion,
  retry-on-failure, fresh URL re-resolution.
- Redis-backed rate limiting and distributed locks.
- Prometheus metrics endpoint (optional).
- Structured JSON logging and graceful shutdown.
- `/health` subsystem report.
- Alembic migrations (queue + favorites).
- Docker Compose stack (bot + PostgreSQL 15 + Redis 7).
- GitHub Actions CI: lint (ruff, mypy) + test matrix on Python 3.11/3.12.
- Full pytest suite (unit + one Docker-backed integration test).
