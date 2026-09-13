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

RUN uv sync --frozen --package tradeflow-worker --no-dev

CMD ["python", "-m", "tradeflow_worker.local_worker"]
