FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.12-slim AS base

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./

FROM base AS runtime

RUN uv sync --locked --no-dev

COPY app ./app
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS browser-tests

RUN uv sync --locked --all-groups \
    && uv run playwright install --with-deps chromium

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY tests ./tests
RUN mkdir -p /app/data

CMD ["uv", "run", "pytest", "--basetemp=data/.pytest-container"]

# Keep a plain `docker build .` production-safe while exposing browser-tests
# as an explicit Compose/CI target above.
FROM runtime AS final
