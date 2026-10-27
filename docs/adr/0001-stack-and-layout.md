# ADR 0001: Stack and repository layout

Status: Accepted

## Context

The roadmap ("Architecture") fixes the broad shape: FastAPI and Postgres, a React SPA, a
worker, Docker Compose, no Redis or Kubernetes. Several developers start in parallel (#2, #3,
#4), so the details they share must be decided once: where code lives, which libraries and
versions, how settings and secrets are read, and how the pieces are packaged.

## Decision

### Layout

| Path | Holds |
|---|---|
| `backend/` | uv project; package `classbeat` in `backend/src/classbeat/`; tests in `backend/tests/`; Alembic in `backend/migrations/` with `backend/alembic.ini` |
| `frontend/` | Vite + React + TypeScript SPA; built to `frontend/dist` |
| `e2e/` | Playwright tests against the compose stack |
| `Dockerfile`, `.dockerignore` | the one image (below) |
| `compose.yaml` | the local stack; a production override arrives in M6 |
| `docker/` | files mounted into third-party containers (for example the Postgres init script) |
| `scripts/` | developer scripts (`worktree.sh`) |
| `secrets/` | local secret files, gitignored |
| `docs/` | roadmap, status, ADRs, security |

### Backend

- Python **3.12**, dependencies managed by **uv** with `uv.lock` committed.
- **FastAPI** with uvicorn; an app factory `classbeat.web:create_app(settings)`.
- **SQLAlchemy 2, async**, with **psycopg 3** (`postgresql+psycopg://`). Async keeps
  server-sent events and `LISTEN/NOTIFY` (roadmap §4) on one event loop; psycopg 3 supports
  both. No engine or session at import time: the app's lifespan and the worker's start create
  them.
- **Alembic** for migrations (async env).
- **pydantic v2** and **pydantic-settings**; **httpx** for outbound HTTP.
- Logging: stdlib `logging`, one JSON object per line on stdout.
- Quality: **ruff** (lint and format), **pyright** (standard mode), **pytest** with
  pytest-asyncio (`asyncio_mode = "auto"`). Database tests run against a real Postgres
  (`CLASSBEAT_TEST_DATABASE_URL`); they skip locally when it's unset and always run in CI.
- From M2 the domain library `mowl` comes from a pinned `mowl-workouts` release wheel.

### Frontend

- **React 19**, **TypeScript** (`strict`), **Vite**, npm with `package-lock.json`, Node **24**.
- ESLint flat config (typescript-eslint, react-hooks); **Vitest** with jsdom and Testing Library.
- Our own design tokens (CSS custom properties, light and dark) in one file. System fonts; no
  runtime requests to third-party hosts. No MOWL logo, owl, colours or copied assets.
- Talks only to `/api` on its own origin. The Vite dev server proxies `/api` to the backend.

### HTTP surface

- JSON API under `/api`. `GET /api/health` (process is up, no database) and `GET /api/ready`
  (database reachable; 503 otherwise, with no exception text).
- The web process serves the built SPA from `CLASSBEAT_STATIC_DIR`, answering any non-`/api`
  GET with `index.html`. Same origin, so no CORS.

### Packaging and runtime

- **One image** for web and worker. A Node stage builds the SPA; a `python:3.12-slim` stage
  installs the backend with uv and copies the SPA to `/app/static`. Runs as a non-root user.
  - web: uvicorn on port 8000 (default command);
  - worker: `python -m classbeat.worker`;
  - migrations: a one-shot `migrate` service running `alembic upgrade head` before web and
    worker start.
- `compose.yaml` services: `db` (`postgres:17`), `migrate`, `web`, `worker`, `mailpit` (dev
  mail). Production adds `caddy` for TLS (M6). Published ports bind `127.0.0.1`.
- Postgres is the only stateful service: the job queue and events live in it (roadmap §4).

### Settings and secrets

- Environment variables prefixed `CLASSBEAT_` for non-secret settings; `.env` holds only
  non-secret local values (ports, compose project name) and is gitignored; `.env.example` is
  committed with placeholders.
- **Secrets are files**, never environment variables: Docker secrets mounted at
  `/run/secrets/<name>`, read by pydantic-settings (`secrets_dir`). Locally they live in
  `secrets/` (gitignored). In code they are `SecretStr` and never logged or shown in a repr.
- A database password is a separate secret that replaces the password part of the database
  URL, so URLs in settings carry no secret.

## Alternatives

- **Django**: a built-in admin and auth, but heavier, sync-first, and its ORM fits RLS
  per-transaction settings less directly. FastAPI with SQLAlchemy keeps the `mowl` library
  and async model/upstream calls simple.
- **Sync SQLAlchemy**: simpler, but SSE streams and `LISTEN/NOTIFY` would need threads.
- **Next.js or another SSR framework**: a second server runtime for no gain; the app is behind
  sign-in and doesn't need SEO beyond a landing page.
- **Redis/Celery** for jobs: more infrastructure; Postgres with `SKIP LOCKED` is enough at beta
  scale and keeps jobs transactional with their data.
- **Poetry or pip-tools**: uv is faster and gives one lockfile across platforms.
- **Separate web and worker images**: two builds to keep in step for no isolation benefit;
  they run the same code.

## Consequences

- Every PR builds on this layout; changing it needs a new ADR.
- The backend's async stack means tests are async and database access goes through
  `AsyncSession`.
- Secrets as files require the compose stack and the worktree script to create
  `secrets/*` before the first start.
