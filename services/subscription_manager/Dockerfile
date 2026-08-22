# Build context is the repo root so the uv workspace resolves its siblings.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY shared/ shared/
COPY services/ingestion/ services/ingestion/
COPY services/subscription_manager/ services/subscription_manager/
RUN mkdir -p agent && touch agent/__init__.py \
    && uv sync --package weave-subscriptions --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "weave_subscriptions.main"]
