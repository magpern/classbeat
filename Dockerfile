# syntax=docker/dockerfile:1
# One image for the web server and the worker. Base images are pinned by exact tag.

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

# --- frontend build ---------------------------------------------------------------------
FROM node:24.22.1-bookworm-slim AS frontend
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
# The pinned mowl-workouts wheel, built without the Swedebeat app key (scripts/build-mowl-wheel.sh).
COPY backend/vendor ./vendor
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
# Swedebeat's app key (ADR 0015 section 4, owner decision 2026-10-31). Only the release workflow
# passes the BuildKit secret `swedebeat_app` (the repository secret SWEDEBEAT_APP) and sets
# REQUIRE_SWEDEBEAT_APP=1, which makes a missing or malformed key fail the build. A pull
# request, CI or e2e build passes neither: no file, and connecting reports that Swedebeat sign-in
# is not available. A secret mount is never a layer of its own; the file is. It is not an ARG,
# an ENV or a COPY, and the release build exports no layer cache.
ARG REQUIRE_SWEDEBEAT_APP=0
RUN --mount=type=secret,id=swedebeat_app,required=false \
    set -eu; \
    if [ -s /run/secrets/swedebeat_app ]; then \
      python -c "import json; d = json.load(open('/run/secrets/swedebeat_app')); assert set(d) == {'key', 'iv'} and len(d['key']) == 32 and len(d['iv']) == 16" 2>/dev/null \
        || { echo "the swedebeat_app build secret is malformed" >&2; exit 1; }; \
      install -m 0444 /run/secrets/swedebeat_app /app/swedebeat_app.json; \
    elif [ "$REQUIRE_SWEDEBEAT_APP" = 1 ]; then \
      echo "the swedebeat_app build secret is missing" >&2; exit 1; \
    fi

USER classbeat
EXPOSE 8000
CMD ["python", "-m", "classbeat", "--host", "0.0.0.0", "--port", "8000"]
