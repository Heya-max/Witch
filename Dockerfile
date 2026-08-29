FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 1000 --create-home app
WORKDIR /home/app

COPY pyproject.toml requirements.txt ./
COPY .env.example ./

RUN pip install --no-cache-dir -U pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

USER app
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "alembic upgrade head && exec python -m app.main"]
