Alembic migrations for the Telegram Music bot

Quick start

- Install dependencies (use your virtualenv):

```bash
pip install alembic sqlalchemy asyncpg
```

- Configure `alembic.ini` or set `sqlalchemy.url` env var to your database URL, for example:

```bash
export SQLALCHEMY_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/telegram_music
```

- Run migrations:

```bash
alembic upgrade head
```

- To create a new revision (autogenerate):

```bash
alembic revision --autogenerate -m "add xyz"
```

Note: This project uses the model metadata in `app.db.models.Base` for autogenerate.
