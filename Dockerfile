FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev

COPY app ./app
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
