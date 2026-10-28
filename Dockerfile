# syntax=docker/dockerfile:1
# One image for the web server and the worker. Base images are pinned by exact tag.

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

# --- frontend build ---------------------------------------------------------------------
FROM node:26.12.1-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- runtime ----------------------------------------------------------------------------
FROM python:3.12.16-slim-bookworm AS runtime

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    CLASSBEAT_STATIC_DIR=/app/static

RUN groupadd --system --gid 10001 classbeat \
    && useradd --system --uid 10001 --gid classbeat --no-create-home --shell /usr/sbin/nologin classbeat

WORKDIR /app
# Dependencies first so this layer is cached until the lockfile changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/src ./src
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev
COPY --from=frontend /build/frontend/dist /app/static

USER classbeat
EXPOSE 8000
CMD ["python", "-m", "classbeat", "--host", "0.0.0.0", "--port", "8000"]
