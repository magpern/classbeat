# syntax=docker/dockerfile:1
# One image for the web server and the worker. Base images are pinned by exact tag.

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

# --- frontend build ---------------------------------------------------------------------
FROM node:24.22.2-bookworm-slim AS frontend
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
# The supported-language manifest the backend reads (ADR 0023 section 1): one file, two readers.
COPY --from=frontend /build/frontend/src/i18n/locales.json /app/locales.json
# The public pages (ADR 0030 sections 2 and 4): the one list of paths and the build's prerendered
# title, description and body of each. Web refuses to start if index.html exists and the second
# is missing, so an incomplete image fails at once instead of serving empty pages.
COPY --from=frontend /build/frontend/src/publicSite/publicPaths.json /app/publicPaths.json
COPY --from=frontend /build/frontend/build/prerender.json /app/prerender.json
# The image holds no secret: no build secret and no build argument (ADR 0035 section 8). Every
# secret is a runtime secret file under /run/secrets.

USER classbeat
EXPOSE 8000
CMD ["python", "-m", "classbeat", "--host", "0.0.0.0", "--port", "8000"]
