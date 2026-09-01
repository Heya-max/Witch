# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- CI: run tests with `-m "not integration"` so the Docker-backed integration test
  doesn't run where external services aren't available.
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
