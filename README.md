# Witch

A Telegram voice-chat music bot. Search and stream music from YouTube and
SoundCloud, share tracks as audio files, fetch lyrics, and control everything
with commands or inline queries.

Built with [pyrogrammod](https://github.com/MyEidolon/pyrogrammod) (Pyrogram),
[py-tgcalls](https://github.com/pytgcalls) 2.3, [yt-dlp](https://github.com/yt-dlp/yt-dlp),
SQLAlchemy 2 (async), Redis, and Docker.

> Note: yt-dlp removed its Spotify/Deezer search extractors; the search chain
> is SoundCloud → YouTube. Spotify *tracks* can still be played when given as
> YouTube URLs.

## Features

- `/play` search across SoundCloud → YouTube with an inline result picker
  (shows title + duration)
- Voice-chat streaming via py-tgcalls with queue management
  (`/queue`, `/playnext`, `/skip`, `/rm`, `/move`, `/shuffle`)
- Optional userbot assistant that creates/joins voice chats automatically
  (bots alone cannot create Telegram group calls)
- `/download` sends the audio file directly into the chat (alias `/dl`)
- `/lyrics` fetches lyrics for a query or the currently playing track (alias `/ly`)
- Inline search: type `@<botname> <song>` from any chat and tap a result to
  receive the audio in your private chat
- Album art on pickers and now-playing, duration formatting everywhere
- Redis-backed rate limiting and join/play locks; Prometheus metrics (optional)
- `/health` — reports client/DB/Redis/voice/player status (handy after a reboot)
- Structured JSON logs (`docker compose logs -f bot`), optional (default) Prometheus
  endpoint, and a graceful shutdown that cancels playback tasks and tears down the
  voice stream

## Requirements

- Python 3.11+
- ffmpeg on the `PATH` (installed automatically in Docker)
- PostgreSQL + Redis (provided by `docker compose`)

> On Windows there is no need to do anything special: this project uses the
> `pyrogrammod` fork and `py-tgcalls`, both of which install and run on Windows
> (unlike the older `pytgcalls`).

## Setup

Clone the repo, create a virtualenv, and install:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy the environment template and fill it in:

```powershell
Copy-Item .env.example .env
```

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | yes | Token from [@BotFather](https://t.me/botfather) |
| `API_ID` / `API_HASH` | no | Optional; needed for user-account features |
| `DATABASE_URL` | yes | Async SQLAlchemy URL (e.g. `postgresql+asyncpg://user:pass@host:5433/telegram_music`) |
| `REDIS_URL` | yes | `redis://host:6379/0` |
| `LOG_LEVEL` | no | Default `INFO` |
| `BOT_OWNER_ID` | no | Telegram user id granted full admin power |
| `METRICS_PORT` | no | Port for the Prometheus metrics endpoint (default `9090`; `0` disables) |
| `QUEUE_MAX_SIZE` | no | Max queued tracks per chat before `QueueFullError` (default `200`; `0` = no cap) |
| `QUEUE_MAX_AGE_SECONDS` | no | Persisted queue rows older than this are pruned at startup (default `86400`; `0` = keep) |
| `PLAY_MAX_RETRIES` | no | Times a failing track is retried before it is dropped from the queue (default `2`; `0` = never drop) |
| `LYRICS_API_BASE` | no | Override the lyrics API base (default `https://api.lyrics.ovh/v1`) |
| `USERBOT_SESSION` | no | Userbot session file name (e.g. `user` → `user.session`); enables the voice assistant |
| `USERBOT_SESSION_STRING` | no | In-memory session string instead of a session file (handy for Docker) |
| `USERBOT_API_ID` / `USERBOT_API_HASH` | no | Fall back to `API_ID`/`API_HASH` when unset |

Start the backing services and apply migrations:

```powershell
docker compose up -d postgres redis
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5433/telegram_music"
.venv\Scripts\alembic upgrade head
```

## Running

Locally (from the repo root, with `.env` in place):

```powershell
.venv\Scripts\python -m app.main
```

Or fully containerized:

```powershell
docker compose up --build -d
```

The Docker setup runs `alembic upgrade head` before starting the bot and
overrides `DATABASE_URL`/`REDIS_URL` with the internal service names. The bot
service reads all other settings straight from the shell/`.env`, so `BOT_TOKEN`
must be set or `docker compose up` refuses to start.

### Inline mode

To enable inline search, open your bot in [@BotFather](https://t.me/botfather),
use `/setinline`, and press the "Yes" prompt.

### Voice assistant (userbot)

Telegram bots cannot create group voice calls, so **without** an assistant
someone must open the group's voice chat manually before `/play` (the bot joins
it and streams, and replies with a hint if no VC is active).

To let the bot open and stream into voice chats on its own, configure a **user
account** assistant. Generate a session once (interactive, needs your phone):

```powershell
python scripts\create_user_session.py            # writes user.session
# or
python scripts\create_user_session.py --string   # prints a session string
```

Then set `USERBOT_SESSION=user` (or `USERBOT_SESSION_STRING=<...>`) in `.env`
and restart the bot. In Docker, prefer the session string, or mount the
`user.session` file into the container.

> Use a dedicated Telegram account for the assistant if you don't want your
> personal account joining voice chats.

## Commands

```
/play <query>        Search and queue music; pick a result when several match
/playnext <query>    Queue a track to play right after the current one
/queue               Show the queue (paginated)
/nowplaying          Show the current track
/download <query>    Send the audio file into the chat (alias /dl)
/lyrics [title]      Show lyrics; no args uses the current track (alias /ly)
/fav <query>         Save a track to your favorites
/favs                List your favorites with play/remove buttons
/unfav <number>      Remove a favorite by its number
/join                Join the voice chat
/leave               Leave the voice chat
/health              Report bot subsystem health (client/db/redis/voice/player)
```

Playlist/album URLs (e.g. a YouTube playlist) are expanded automatically and
queued as a batch by `/play`; `/download` shows the usual result picker when a
search matches several tracks.

### URL validation & queue health

Non-audio junk can't reach playback: pasting a plain website URL (e.g.
`https://techaistudy.com/...`) is refused up front, and `.mp3`/`.m4a`/HLS/DASH
playlist URLs or known media hosts (YouTube, SoundCloud, Bandcamp, Spotify…)
pass validation. Player resumes and search results are unaffected.

The per-chat queue is capped (`QUEUE_MAX_SIZE`, default 200) — going over is
reported to the user instead of wedging the player — persisted rows are pruned
after `QUEUE_MAX_AGE_SECONDS` (default 24 h) on startup so stale state can't
poison a restart, corrupt `queue_entries` rows are skipped and deleted on read,
and a track whose stream repeatedly fails to start is retried up to
`PLAY_MAX_RETRIES` (default 2) before being dropped.

Admin commands (chat admins or `BOT_OWNER_ID`):

```
/pause /resume /volume <0-200> /skip
/rm <position> /move <from> <to> /shuffle
/stop /clear
```

## Development

```powershell
docker compose up -d redis        # one integration test needs Redis
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check .
.venv\Scripts\mypy app
```

## Project layout

```
app/
  bot/             client/assistant factories, handlers
  player/          voice manager (py-tgcalls), audio engine, queue, player state
  sources/         music providers (yt-dlp, platform search, URL passthrough)
  services/        rate limiter, Redis locks, metrics
  db/, alembic/    SQLAlchemy models + migrations
scripts/           dev tools (e.g. userbot session creator)
tests/             pytest suite (unit + one Docker-backed integration test)
```