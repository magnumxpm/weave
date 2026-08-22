# Build context is the repo root so the uv workspace resolves weave-common.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer first: unchanged sources keep this cached.
COPY pyproject.toml uv.lock ./
COPY shared/pyproject.toml shared/
COPY services/ingestion/pyproject.toml services/ingestion/
RUN mkdir -p shared/weave_common services/ingestion/weave_ingestion agent \
    && touch shared/weave_common/__init__.py services/ingestion/weave_ingestion/__init__.py agent/__init__.py \
    && uv sync --package weave-ingestion --no-dev --frozen

COPY shared/ shared/
COPY services/ingestion/ services/ingestion/
RUN uv sync --package weave-ingestion --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

# Cloud Run sets PORT; concurrency and timeout come from the service config.
CMD exec uvicorn weave_ingestion.main:app --host 0.0.0.0 --port ${PORT:-8080}
