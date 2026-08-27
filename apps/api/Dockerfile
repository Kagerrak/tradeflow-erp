FROM ghcr.io/astral-sh/uv:0.8.13-python3.13-bookworm-slim

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY apps/api apps/api
COPY apps/worker apps/worker
COPY scripts scripts

RUN uv sync --frozen --package tradeflow-api --no-dev

EXPOSE 8000

CMD ["sh", "-c", "alembic -c apps/api/alembic.ini upgrade head && uvicorn tradeflow_api.main:app --app-dir apps/api/src --host 0.0.0.0 --port 8000"]
