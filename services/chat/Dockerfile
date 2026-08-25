# Build context is the repo root so the uv workspace resolves weave-common.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY shared/pyproject.toml shared/
COPY services/ingestion/pyproject.toml services/ingestion/
COPY services/chat/pyproject.toml services/chat/
RUN mkdir -p shared/weave_common services/ingestion/weave_ingestion services/chat/weave_chat agent \
    && touch shared/weave_common/__init__.py services/ingestion/weave_ingestion/__init__.py \
       services/chat/weave_chat/__init__.py agent/__init__.py \
    && uv sync --package weave-chat --no-dev --frozen

COPY shared/ shared/
COPY services/ingestion/ services/ingestion/
COPY services/chat/ services/chat/
RUN uv sync --package weave-chat --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

CMD exec uvicorn weave_chat.main:app --host 0.0.0.0 --port ${PORT:-8080}
