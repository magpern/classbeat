# AGENTS.md: how agents build classbeat

classbeat is a website where indoor-cycling instructors describe a class, Claude picks the
songs and designs it, and the class is saved to their Intelligent Cycling (MOWL) account.
It is built autonomously by agents. The owner (`@magpern`) makes product decisions and gives
external approvals; agents do everything else.

**[docs/roadmap.md](docs/roadmap.md) is the frozen plan (Revision 6). If this file and the
roadmap differ, the roadmap wins.** Section numbers (§1, §4, M2 …) refer to it.

## Roles

| Role | Model class | Owns | Must not |
|---|---|---|---|
| Architect / Orchestrator | Opus | The next milestone, its PR split and acceptance criteria, dependencies, scheduling, merge order, ADRs, track F, `docs/status.md` | Normally write production code |
| Developer | Sonnet | One PR in its own branch and worktree: code, tests, CI fixes, **all** reviewer findings | Change architecture or acceptance criteria without the Architect |
| Explorer | Haiku | Bounded work: searches, evidence, fixtures, docs, extra tests | Own security, auth, isolation, crypto or billing decisions |
| Reviewer | a **fresh** Opus context per review, sharing no context with the Architect or Developer | The diff, the acceptance criteria, tests, CI output, ADR compliance → `PASS` or `FAIL` with findings | Fix its own findings; review an ADR it wrote |

Model *classes*, not versions: use the current generation of each tier. A model substitution
never widens a role's authority. Agent definitions live in `.claude/agents/`.

## Unit of work: one issue, one PR

- Every milestone is a GitHub milestone. The Architect splits it into issues, one per PR,
  each with scope, acceptance criteria and dependencies ("Depends on #n").
- A PR closes one issue (`Closes #n` in the body) and states its scope, its acceptance
  criteria and how they were tested.
- A milestone is done when all its issues are closed and its "done when" in the roadmap holds.

## PR loop

```
issue → Developer (+ Explorer) in its own worktree → PR → CI
      → fresh Opus Reviewer → PASS → agent merges
                            → FAIL → Developer fixes every blocking finding → CI → new fresh Reviewer
```

If the same blocking finding survives three cycles, the Architect re-assesses the root cause,
revises the issue or ADR, and the loop restarts with a fresh Reviewer.

The Orchestrator keeps going without routine human input: it does not stop for a `FAIL` or at
the end of a milestone. It escalates only genuine blockers (see "Escalation" below).

## Review gate (mandatory for every PR an agent merges)

1. The PR has a Reviewer `PASS` for its **current head commit**, recorded on the PR as a
   comment that starts `Agent review (Opus): PASS <head SHA>` followed by the non-blocking
   findings. A `FAIL` is recorded the same way: `Agent review (Opus): FAIL <head SHA>`.
2. The Reviewer was a fresh Opus context. A fresh Sonnet context may add a supplementary
   light review (`Agent review (Sonnet, supplementary): …`); it never replaces the Opus `PASS`.
3. **Any change of the head SHA needs a new full review.** No carry-over, including after a
   branch update. The Orchestrator orders merges so a PR is brought up to date *before* it is
   reviewed.
4. Not a `PASS`: the implementing session's own check, a review skill run in the same context,
   green CI, passing tests, or "it's fine" in chat. A human saying so in chat doesn't replace
   the review for an agent merge either.
5. Agents record verdicts only as comments. They never submit approving reviews and never
   write a comment starting `Human review:`.
6. A `FAIL` is never overridden by an agent.

**A human may merge any PR on GitHub regardless of the verdict.** That is the owner's
decision, never an agent's: an agent never merges without the gate, even when told to skip it.

## Merging

Agents merge only with:

```bash
gh pr merge <n> --squash --match-head-commit <reviewed-sha> --subject "<PR title> (#<n>)"
```

and only when CI is green, the `PASS` is for that SHA, no blocking comment is open and the
branch is up to date. Never `--auto`, never `--admin`, never an approving review, never a
change to the ruleset to get a merge through.

The ruleset on `main` (GitHub Pro): pull request required, required checks `ci` and
`pr-title`, branch up to date, no direct pushes, squash only, zero required approvals.
`CODEOWNERS` (`* @magpern`) is informational.

## Branches, worktrees and commits

- Every task is a feature branch `<issue>-<slug>` and a PR. One writing agent per branch.
- Parallel developers each work in their own worktree: `scripts/worktree.sh new <issue> <slug>`
  creates `../classbeat-wt/<issue>-<slug>` with its own `.env`, so each runs its own stack
  (`COMPOSE_PROJECT_NAME=classbeat-<issue>`, own Postgres volume and ports). Remove the
  worktree after the merge (`scripts/worktree.sh rm <issue>`).
- When a dependency merges first, the dependent branch is updated from `main` and reviewed
  again (rule 3 above).
- **Conventional Commits 1.0.0** for commits, PR titles and squash subjects:
  `feat(auth): …`, `fix(worker): …`, `refactor(db): …`, `test(security): …`, `ci: …`,
  `docs: …`, `build: …`, `chore: …`. Breaking changes use `!` or a `BREAKING CHANGE:` footer.
  CI checks the PR title with commitlint (`commitlint.config.cjs`).
- No `Co-Authored-By` trailer in commits. PR descriptions end with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Safety envelope

**Agents may, without asking:** create branches, PRs and issues; review and merge per the gate;
cut releases and push images to `ghcr.io/magpern/classbeat`; run local Docker Desktop stacks;
make read-only checks needed by track F; branch, PR, merge (same gate) and release in
`magpern/mowl-workouts`, keeping its `CLAUDE.md` rules and its CLI, MCP server and plugin working.

**Only with the owner's authorization for that specific action:** deploying to the VPS;
DNS and TLS; paid services and subscriptions; provider dashboards (Spotify apps and
allow-lists, Paddle, Google/GitHub/Microsoft OAuth apps); emails to real people; any test run
against a live account; anything that writes to a real MOWL, Spotify or Swedebeat account;
contacting providers.

## Security rules

- Never commit, print or log secrets: API keys, OAuth client secrets, session cookies, MOWL,
  Spotify or Swedebeat tokens, encryption keys, signed URLs. `.env` files are never committed;
  `.env.example` holds placeholders only.
- Passwords for MOWL and Swedebeat are typed by the user on our own page, used once to get a
  token and discarded. Never stored, logged, put in a URL or sent to a model.
- No Spotify Client Secret, ever: Spotify sign-in is Authorization Code with PKCE (§3).
- Never use credentials embedded in third-party apps (for example Swedebeat's AWS keys).
- Tokens never go to a model. Model output is a proposal; the backend authorizes every write
  against the authenticated user (§2).
- Everything we create in a user's accounts is private: playlists `public: false`,
  `collaborative: false`; MOWL workouts private.
- Tests and CI never touch the network or real accounts: they use fakes for MOWL, Spotify,
  Swedebeat and model APIs.
- Every owned row carries `owner_id` and is read through `UserContext` (§1, ADR 0002).

## Decisions and ADRs

Routine technical decisions never go to the owner. When a fact is unknown, the Architect picks
the safest reversible default, isolates it behind an interface or setting, and records it in
`docs/adr/NNNN-title.md` (Context, Decision, Alternatives, Consequences, Status). A fresh
Reviewer reviews the ADR in its PR.

## Escalation to the owner

Only for: external permissions or agreements (track F), credentials agents can't provision
safely, product decisions, and live-account tests needing authorization. Record the question
in `docs/status.md` under "Waiting for the owner", carry on with independent work, and resume
when it's answered.

## Durable state and resuming

The repository and GitHub are the source of truth, never chat history:
`docs/roadmap.md`, `docs/status.md` (updated by every merge), `docs/adr/`, GitHub milestones,
issues and PR descriptions.

To resume after a context reset:

1. read `CLAUDE.md`, `AGENTS.md`, `docs/roadmap.md` and `docs/status.md`;
2. inspect open PRs, their CI and review comments, open issues, `git worktree list`;
3. continue with the next unblocked issue without re-planning finished work.

## Developer commands

### Backend

Run from `backend/`. uv installs Python 3.12 by itself.

```bash
cd backend
uv sync                        # install from uv.lock (CI uses `uv sync --locked`)
uv run ruff check              # lint
uv run ruff format --check     # formatting (`uv run ruff format` fixes it)
uv run pyright                 # types
uv run pytest                  # tests; the database tests skip unless the Postgres below is up
uv run python -m classbeat     # the API on http://127.0.0.1:8000 (--host, --port); try /api/health
uv run python -m classbeat.worker   # the worker; stops on Ctrl+C or SIGTERM
uv run alembic upgrade head    # migrations, against the database in CLASSBEAT_DATABASE_URL
```

Database tests need a throwaway Postgres. Give it a name and port of its own (one per
worktree), point `CLASSBEAT_TEST_DATABASE_URL` at it and stop it when done:

```bash
docker run -d --rm --name classbeat-2-pg -e POSTGRES_PASSWORD=test -p 127.0.0.1:55402:5432 postgres:17
export CLASSBEAT_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:55402/postgres
uv run pytest
docker stop classbeat-2-pg
```

Settings come from `CLASSBEAT_*` environment variables. Secrets come only from files named
after the field, without the prefix, in `CLASSBEAT_SECRETS_DIR` (default `/run/secrets`):
`database_password` replaces the password in `CLASSBEAT_DATABASE_URL`
(`postgresql+psycopg://...`); `CLASSBEAT_DATABASE_PASSWORD` is ignored. In CI (`CI=true`) a
missing `CLASSBEAT_TEST_DATABASE_URL` fails the database tests instead of skipping them.

### Frontend

React 19, TypeScript and Vite in `frontend/` (Node 24, npm; `package-lock.json` is committed).
Run these from `frontend/`:

```bash
npm ci               # install exactly what the lockfile says
npm run dev          # dev server on :5173; proxies /api to http://localhost:8000
npm run lint         # ESLint (typescript-eslint, react-hooks)
npm run typecheck    # tsc -b, strict
npm test             # Vitest + Testing Library (jsdom), no network
npm run build        # typecheck, then Vite writes frontend/dist
```

- Proxy target: set `API_PROXY_TARGET` (shell or `frontend/.env`, git-ignored) to point the dev
  server at another backend, for example `API_PROXY_TARGET=http://localhost:8001 npm run dev`.
- Colours, spacing and type live only in `frontend/src/styles/tokens.css` (light and dark).
- System fonts and local assets only; nothing is requested from a third-party host at runtime
  (`src/no-third-party-hosts.test.ts` guards the sources).
- CI runs the same five commands in the `frontend` job.

### Stack and worktrees

Docker Desktop (or Docker Engine with Compose v2) runs the whole stack: `db` (Postgres 17),
`migrate` (one-shot `alembic upgrade head`), `web`, `worker` and `mailpit`, all from one image
(`Dockerfile`, `compose.yaml`). Ports bind `127.0.0.1` and come from `.env`.

```bash
scripts/worktree.sh init                 # .env and secrets/ (random passwords) if missing
docker compose up -d --build --wait      # web on http://127.0.0.1:$WEB_PORT (default 8000)
curl -i http://127.0.0.1:8000/api/ready  # 200 when the database answers; / serves the SPA
docker compose logs -f web worker
docker compose down -v                   # stop and delete the volume
```

Every worktree gets its own project name, volume and ports (web `20000+<issue>`, Postgres
`30000+<issue>`, Mailpit UI `40000+<issue>`; issue numbers 1-9999), disjoint from the defaults of
the main checkout (8000, 55432, 8025), so stacks never collide:

```bash
scripts/worktree.sh new 7 some-slug      # ../classbeat-wt/7-some-slug on branch 7-some-slug, then init
scripts/worktree.sh ls                   # worktrees with project name and ports
scripts/worktree.sh rm 7                 # compose down -v, remove the worktree (refuses if dirty), `git branch -d`
```

`secrets/` and `.env` are git-ignored. Passwords are mounted as Docker secret files, never
passed as environment variables.

### Security checks (CI)

Each is its own job in `ci.yml` and in the `ci` gate:

| Job | What it checks |
|---|---|
| `secrets` | gitleaks over the whole history |
| `pip-audit` | `uv export --frozen` (production, then with dev) through `pip-audit` (pinned) |
| `npm-audit` | `npm audit --audit-level=high` in `frontend/` |
| `trivy-fs` | Trivy on the repository (vulnerabilities, Dockerfile/compose misconfiguration) |
| `trivy-image` | Trivy on the image built in the job (never pushed) |
| `compose` | `web` and `worker` run as non-root; no secret value appears in `docker inspect` |

Trivy and gitleaks are checksum-verified release binaries. `pip-audit` (exact version) lives in
the `audit` dependency group of `backend/pyproject.toml`, so its own dependencies are hash-locked in
`uv.lock` and CI installs it with `uv sync --frozen --only-group audit`; the runtime image
(`uv sync --locked --no-dev`) never contains it. The audited requirements are exported with hashes
and checked with `--require-hashes`. `setup-uv` is pinned to the same uv version as the Dockerfile.

Trivy fails on HIGH and CRITICAL findings that have a fix available (`--ignore-unfixed`).
**Accepted risk:** unfixed HIGH/CRITICAL findings are not gated. They are revisited at the M6
security review, and once a fix appears Trivy starts failing on them. `trivy-fs` scans lockfiles and
the Dockerfile (misconfiguration); it does not cover `compose.yaml`.

Every GitHub Action is pinned by full commit SHA with the version in a trailing comment.
Dependabot (`.github/dependabot.yml`, weekly) keeps Actions, uv, npm and the Dockerfile and compose
images current, with `build(deps)` and `ci(deps)` commit prefixes. It does not track Debian packages
inside the base image: when Trivy fails on one, bump the base image tag in the Dockerfile.

New advisory: **upgrade first** (bump the dependency or base image, refresh the lockfile). Only if
no fix exists or the code path is unreachable, add a dated ignore: `.trivyignore` (one ID per line,
with a `# reason` comment above and Trivy's `exp:YYYY-MM-DD` expiry, e.g. `CVE-2025-0001 exp:2026-12-31`)
or `--ignore-vuln ID` for pip-audit with the same comment. An expired ignore is removed in the next
PR that touches it. Never ignore without a reason.
