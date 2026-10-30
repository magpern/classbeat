# AGENTS.md: how agents build classbeat

classbeat is a website where indoor-cycling instructors describe a class, Claude picks the
songs and designs it, and the class is saved to their Intelligent Cycling (MOWL) account.
It is built autonomously by agents. The owner (`@magpern`) makes product decisions and gives
external approvals; agents do everything else.

**[docs/roadmap.md](docs/roadmap.md) is the frozen plan (Revision 6, with its dated amendments at the top). If this file and the
roadmap differ, the roadmap wins.** Section numbers (§1, §4, M2 …) refer to it.

## Roles

| Role | Model class | Owns | Must not |
|---|---|---|---|
| Architect / Orchestrator | Opus | The next milestone, its PR split and acceptance criteria, dependencies, scheduling, merge order, ADRs, track F, `docs/status.md` | Normally write production code |
| Developer | Sonnet; Opus for security-critical PRs (below) | One PR in its own branch and worktree: code, tests, CI fixes, **all** reviewer findings | Change architecture or acceptance criteria without the Architect |
| Explorer | Haiku | Bounded work: searches, evidence, fixtures, docs, extra tests | Own security, auth, isolation, crypto or billing decisions |
| Reviewer | a **fresh** context per review (Opus; Sonnet for non-security PRs, see the review gate), sharing no context with the Architect or Developer | The diff, the acceptance criteria, tests, CI output, ADR compliance → `PASS` or `FAIL` with findings | Fix its own findings; review an ADR it wrote |

Model *classes*, not versions: use the current generation of each tier. A model substitution
never widens a role's authority. Agent definitions live in `.claude/agents/`.

**Security-critical PRs get an Opus Developer** (owner decision, 2026-10-28): encryption and key
handling, jobs with leases and fencing, Spotify OAuth, and approvals and external writes. The
issue says so in its "Owner:" line; everything else keeps a Sonnet Developer. The Reviewer is a
fresh context either way, never the Developer's: Opus for those PRs and the others listed in
the review gate.

## Unit of work: one issue, one PR

- Every milestone is a GitHub milestone. The Architect splits it into issues, one per PR,
  each with scope, acceptance criteria and dependencies ("Depends on #n").
- A PR closes one issue (`Closes #n` in the body) and states its scope, its acceptance
  criteria and how they were tested.
- A milestone is done when all its issues are closed and its "done when" in the roadmap holds.

## PR loop

```
issue → Developer (+ Explorer) in its own worktree → PR → CI
      → fresh Reviewer → PASS → agent merges
                       → FAIL → Developer fixes every blocking finding → CI → fresh delta Reviewer
```

If the same blocking finding survives three cycles, the Architect re-assesses the root cause,
revises the issue or ADR, and the loop restarts with a fresh Reviewer.

The Orchestrator keeps going without routine human input: it does not stop for a `FAIL` or at
the end of a milestone. It escalates only genuine blockers (see "Escalation" below).

## Review gate (mandatory for every PR an agent merges)

1. The PR has a Reviewer `PASS` for its **current head commit**, recorded on the PR as a
   comment that starts `Agent review (<model>): PASS <head SHA>` followed by the non-blocking
   findings. A `FAIL` is recorded the same way: `Agent review (<model>): FAIL <head SHA>`.
2. The Reviewer was a fresh context: **Opus** for security-critical PRs (encryption and keys,
   auth and sessions, tenant isolation and RLS, guards and roles, jobs with leases and fencing
   and the queue SQL, approvals and external writes, Spotify OAuth, egress and model keys,
   billing); **Sonnet** is enough for the rest (UI, tests, docs, tooling). When in doubt, Opus.
3. **A new head SHA needs a new review**, scoped as follows (roadmap Amendment 2):
   - *Merge-only update:* the only change since the reviewed head is a merge of `main` with
     no hand-resolved conflict (`git diff <reviewed>..<head>` equals `main`'s own change), and
     CI is green. The Architect records `Agent review (merge check): PASS <head SHA> (carries
     <reviewed SHA>)`. If `main` changed a module the PR changes or calls, or a conflict was
     resolved by hand, a fresh reviewer checks the merge (scoped review).
   - *Fix round after a `FAIL`:* a **delta review** of the diff since the last reviewed head,
     verifying each earlier finding and whatever the delta touches. The same Reviewer is resumed
     for it on non-security PRs and for test-only fixes on security-critical PRs; a fresh Opus
     Reviewer when a security fix changes behaviour, or after a second `FAIL` (owner, 2026-10-29).
     **Delta reviews by Sonnet (roadmap Amendment 4, owner, 2026-10-30):** a delta that only
     adds or changes tests or docs, loosens no guard test, and has no hand-resolved conflict in
     production code, may be reviewed by a fresh Sonnet even on a security-critical PR, but not
     after a second `FAIL`; it must still check each earlier finding by a mutation of the code
     the new test protects (by reading, for a docs-only finding).
   Reviewers rely on CI for the full suite and run only the targeted test files and mutation
   checks of the guarantees that changed: at most about 8 mutations per review, chosen by
   risk (the guarantees the PR adds; the mutations earlier findings need are extra), not a sweep
   of every line. Non-blocking findings are
   batched into few hardening PRs.
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

## Releasing

Agents may cut releases without the owner (safety envelope above). Deploying is not part of
a release and still needs the owner's authorization.

1. `main` is green and holds everything the release should contain. Pick the next SemVer
   version `vX.Y.Z` (a pre-release is `vX.Y.Z-rc.1`).
2. Tag the current tip of `main` and push the tag:

   ```bash
   git fetch origin main
   git tag -a vX.Y.Z -m "vX.Y.Z" origin/main
   git push origin vX.Y.Z
   ```

3. `.github/workflows/release.yml` then verifies the tag is SemVer and on `main`, builds
   `linux/amd64` and `linux/arm64`, pushes `ghcr.io/magpern/classbeat` with tags `X.Y.Z`,
   `X.Y` (not for pre-releases) and `sha-<short>` plus provenance and SBOM attestations, and
   creates the GitHub Release with generated notes (marked pre-release if the tag has a
   hyphen). There is no `latest` tag; deployments pin a version.
4. Check the run with `gh run list --workflow release.yml`. The package stays private; never
   change its visibility. Never move or delete a pushed tag: fix forward with a new version.

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
  The backend test suite enforces it: an autouse fixture (`tests/netguard.py`) makes every
  non-loopback `connect` and name lookup raise before anything is sent; only a test marked
  `allow_network` (none today) is exempt.
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
uv run python -m classbeat invite create [--expires 7d]    # operator commands (ADR 0003 section 7);
uv run python -m classbeat admin grant|revoke|mfa-reset EMAIL   # in the stack: docker compose exec web ...
uv run python -m classbeat jobs stats   # job counts per state, uncertain external operations (no ids)
uv run python -m classbeat keys rewrap [--totp]   # after a KEK rotation (ADR 0004 section 2); --totp once
docker compose --profile maint run --rm maint python -m classbeat keys wipe --confirm|--resend   # KEK compromise only
uv run alembic upgrade head    # migrations as the owner role, CLASSBEAT_MIGRATIONS_DATABASE_URL
```

The domain library `mowl` (`mowl-workouts`) is pinned as `backend/vendor/mowl_workouts-0.6.0-py3-none-any.whl`,
built from the v0.6.0 tag (commit `0319fcb8`; vendored wheel SHA-256 `d3957a80…`, release asset
`5ea21460…`) **without** the bundled Swedebeat app key by `scripts/build-mowl-wheel.sh`
(needs `gh` access to the private repo; it checks the tag's commit, the missing key file and that
every other file and the `Requires-Dist` lines match the release wheel). After a rebuild run `uv lock`. classbeat leaves out
the library's `mcp` dependency (`exclude-dependencies`): it never runs the MCP server, and `mcp`'s
`httpx2` would replace httpx inside Authlib. Backend tests fake MOWL in-process (`tests/mowlfake.py`).
Because `mcp` is left out, the image's `mowl` and `mowl-mcp` console scripts fail on import: use only the library's modules `api`, `service`, `library`, `spotify_web`
and `swedebeat` (never the CLI or MCP entry points). The tag commit, the release asset's digest and
the vendored wheel's SHA-256 are recorded in full in the header of `scripts/build-mowl-wheel.sh`.
0.6.0 adds what Spotify song picking needs (ADR 0009 §12): ID-based `spotify_web` calls
(`create_playlist`, `add_items`, `replace_playlist_items`, `get_playlist`, `playlist_items` on
`/items`, `me`, `my_playlists`), `search_tracks(market=)`, `spotify.track_uri`, `SCOPES_PRIVATE`,
`MowlError.status`/`.retry_after`, and the `Credentials` options `spotify_refresh_enabled` (classbeat
always sets `False`: the library never reaches the token endpoint) and `spotify_max_retry_wait`
(classbeat sets `0`: the library never sleeps on a 429). Never use its name-based `save_playlist` or
`find_playlist_tracks` (CLI only).

Database tests need a throwaway Postgres whose application database has the roles of
`docker/postgres/init/10-roles.sql` (ADR 0002 section 4). Give it a name and port of its own
(one per worktree), create the roles, point `CLASSBEAT_TEST_DATABASE_URL` (the superuser, used
for test setup only) at it and stop it when done:

```bash
export MSYS_NO_PATHCONV=1   # Git Bash on Windows only
docker run -d --rm --name classbeat-5-pg -e POSTGRES_PASSWORD=test -p 127.0.0.1:55405:5432 postgres:17.11
docker exec classbeat-5-pg psql -U postgres -c 'CREATE DATABASE classbeat'
docker exec classbeat-5-pg sh -c 'echo owner-pw > /tmp/owner; echo app-pw > /tmp/app'
docker cp ../docker/postgres/init/10-roles.sql classbeat-5-pg:/tmp/10-roles.sql
docker exec -e DATABASE_OWNER_PASSWORD_FILE=/tmp/owner -e DATABASE_PASSWORD_FILE=/tmp/app \
  classbeat-5-pg psql -v ON_ERROR_STOP=1 -U postgres -d classbeat -f /tmp/10-roles.sql
export CLASSBEAT_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:55405/classbeat
uv run pytest
docker stop classbeat-5-pg
```

The tests give `classbeat_owner` and `classbeat_app` random passwords for the run, connect as
them, and create their own test-only owned tables. The script is idempotent. Tests that migrate
to head also run `docker/postgres/queue/20-queue.sql` as the superuser, as `db-functions` does;
use the image tag compose pins (`postgres:17.11`), because the queue check compares each
function's `md5(pg_get_functiondef(oid))` with `classbeat/db/queue_digests.py` (README.md says
how to refresh it). Web, worker and the CLI also refuse to start on a drifted job queue.

Settings come from `CLASSBEAT_*` environment variables. Secrets come only from files named
after the field, without the prefix, in `CLASSBEAT_SECRETS_DIR` (default `/run/secrets`):
`database_password` replaces the password in `CLASSBEAT_DATABASE_URL` (the app role, for web
and worker) and `database_owner_password` replaces the one in
`CLASSBEAT_MIGRATIONS_DATABASE_URL` (the owner role, for Alembic); `app_secret_key` (at least
32 bytes, created by `scripts/worktree.sh init`; the web app refuses to start without it) feeds
the derived keys of ADR 0003 section 8; `connection_kek` (exactly 64 lowercase hex characters, created by `scripts/worktree.sh init`;
web and worker refuse to start without it) is the current key-encryption key of ADR 0004 section 2,
its id `CLASSBEAT_CONNECTION_KEK_ID` (default `kek-1`; compose takes `CONNECTION_KEK_ID` from `.env`),
and retired ones are `connection_kek_<id>`; `smtp_password` (optional) is the SMTP login password
(`CLASSBEAT_SMTP_HOST`, `_PORT`, `_SENDER`, `_USERNAME` and `_TLS` = `starttls`, `tls` or `none`;
`none` only for loopback or the compose `mailpit`; compose points them at mailpit); the matching
`CLASSBEAT_DATABASE_PASSWORD` and `CLASSBEAT_DATABASE_OWNER_PASSWORD` variables are ignored.
The model (ADR 0006 section 9): `CLASSBEAT_MODEL_PROVIDER` is `anthropic` (the default) or `fake`
(compose takes `MODEL_PROVIDER` from `.env`, `fake` in `.env.example`, so the dev stack and CI never
call a model). `anthropic` needs the `anthropic_api_key` secret file, mounted on the **worker only**
(an empty file counts as none; `scripts/worktree.sh init` creates it empty and it is never a real
key); the worker refuses to start with `anthropic` and no key and with `fake` when
`CLASSBEAT_ENVIRONMENT=prod`, and the web app refuses to start when it can read the key. The
`anthropic` provider (`classbeat.agent.providers.anthropic`) uses the official `anthropic` SDK
pinned at 0.125.0. That is the last release on `httpx` and it gets no further fixes, since 1.x and
2.x need `httpx2`; we stay on it for now because the egress allowlist and its guard are built on
httpx and `httpx2` stays out of the image (ADR 0006 section 2, #120). Review by 2027-01-31, or
earlier if pip-audit reports an advisory against 0.125.0, the API needs something 0.x lacks, or
Authlib needs `httpx2` itself; Dependabot ignores `anthropic` majors until then, and pip-audit
must stay clean. With `anthropic` the worker refuses to start (and the provider to build) when
`ANTHROPIC_LOG`, `ANTHROPIC_CUSTOM_HEADERS` or `SSLKEYLOGFILE` is set; the provider's price entries in `classbeat.agent.prices` are to be verified before
live use. The worker's outbound requests go only to `https://api.mowl.com`, `https://api.anthropic.com`,
`https://api.spotify.com` and `https://accounts.spotify.com` (`WORKER_HOSTS` in `classbeat.egress`, ADR 0009
section 10; the model client alone is limited to the model host). The Spotify token endpoint and `GET /me`
(`classbeat.spotify.http`) use `egress.spotify_async_client()`, in the web process (connect) and the
worker (refresh) alike, whose list is the two Spotify hosts and nothing else. The list is code, not a
setting. `classbeat.egress` is the only module allowed to build an HTTP client or `urllib` opener;
guarded by `tests/test_egress_guard.py`; its TLS contexts never write a key
log (`egress.tls_client_context`). Every worker urllib request has a **total** deadline (its timeout,
60 s without one, never past the thread's `egress.call_deadline`): a watchdog cuts a response that
trickles in, and a request due after the deadline is never sent (ADR 0007 section 6).
Save (`save_workout`, `classbeat.saving`, ADR 0007 sections 5, 6 and 8): no credit, no model; the
write runs inside `call_deadline_until` of an instant taken before its `pending` row, plus `bound_seconds`. `CLASSBEAT_MOWL_LISTING_LAG_SECONDS` (unset by
default, at most 86400) lets the reconciler mark an uncertain save `failed` when a covered listing
shows no new program and the call returned at least that long ago; leave it unset until the owner
has measured MOWL's listing delay at live activation.
Update saved (`update_workout`, `classbeat.saving.update`, ADR 0007 sections 7 and 8): the program id
comes only from the user's own live `saved_workouts` row of the class; the program is read first
(`program_changed` / `program_missing`, nothing written, unless the page sends `overwrite`), and its
reconciler marks an uncertain update `failed` only when the target is unchanged (`before_hash`) and a
covered listing shows no new program since the baseline (no lag setting involved).
The agent loop (`classbeat.agent`, ADR 0006 sections 2, 5 and 8): `CLASSBEAT_AGENT_MODEL` (default
`claude-opus-5-5`), `_EFFORT` (`medium`), `_MAX_TOKENS` (32000), the per-stage limits `_MAX_TURNS` (12),
`_MAX_TOOL_CALLS` (40), `_MAX_CORRECTIONS` (3), `_STAGE_SECONDS` (300), the per-job ceiling
`_JOB_COST_MICROS` (1500000, $1.50) and `CLASSBEAT_AGENT_TURN_OUTPUT_RESERVE` (8000);
`CLASSBEAT_FAKE_MODEL_SCRIPT` is the `FakeProvider` script with `fake` (`backend/tests/agent_scripts/`).
The smoke set (ADR 0006 section 8) runs in CI against `FakeProvider`
(`backend/tests/test_evals_smoke.py`); the owner's live run is
`docker compose exec worker python -m classbeat.evals smoke --live` (it prints its cost ceiling and
stops unless `--max-usd N` is also given; agents and CI never run it). Options: `--model` (a priced
one: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`), `--effort` (default
`CLASSBEAT_AGENT_EFFORT`), `--repeat N` (each case N times, at most 10), `--case`, `--out FILE`
(JSON with the metrics, the checks with their details and the shape of each class); failed checks
print with their detail. A model comparison is one such run per model, and **each run needs the
owner's go-ahead**; `python -m classbeat.evals compare RESULT.json ... --out DIR` then writes
`compare.md` and `compare.html` offline (no model, no network, no settings).
Song picking and the Spotify connection (ADR 0008, ADR 0009): `CLASSBEAT_SONG_PICKING` (default off; off,
every Spotify route answers 404 `feature_off`; the owner switches it on, roadmap Amendment 1; with it on,
`CLASSBEAT_PUBLIC_BASE_URL` may not be `localhost`, use `127.0.0.1`, because Spotify does not accept
`localhost` in a redirect URI), `CLASSBEAT_SONG_JOB_COST_MICROS` (3000000, $3; the song-picking kinds' own
ceiling across their stages and attempts, 0 to 100000000), `CLASSBEAT_SPOTIFY_FLOW_TTL_SECONDS` (600, 60 to 600;
how long a connect flow lives), `CLASSBEAT_SPOTIFY_REFRESH_MARGIN_SECONDS` (120, 0 to 1800; a token with less
than this left is refreshed), `CLASSBEAT_SPOTIFY_REFRESH_TIMEOUT_SECONDS` (10, above 0 and at most 20; the
refresh request's total deadline) and `CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS` (unset, at most 86400; lets the
reconciler mark an uncertain playlist create `failed` when the user's listing shows no playlist with its `ref`
and the call returned at least that long ago; leave it unset until the owner has measured Spotify's listing
delay at live activation). There is no Client ID setting and no Client Secret: each user enters their own
Spotify app's Client ID on the account page, and the callback is the fixed
`<CLASSBEAT_PUBLIC_BASE_URL>/oauth/spotify/callback`.
The Spotify playlist writes live in `classbeat.playlists` (ADR 0008 section 2): the external-operation
kinds `spotify_create_playlist` and `spotify_replace_items` (`create`, `replace`, with `common` for the
owner check, read-back and the one way they call Spotify), the pool steps (`pool`: `write_pool`) and the
final-playlist steps (`final`: `create_final_playlist`, `fill_final_playlist`). Importing the package
registers both kinds (worker and web app both import it); every write goes through
`classbeat.operations.perform` and the job's fence, and nothing in it opens a `user_session`.
Jobs (ADR 0005 sections 3 and 4): `CLASSBEAT_JOB_LEASE_SECONDS` (default 120, 30 to 600),
`CLASSBEAT_WORKER_CONCURRENCY` (2, at most 16; the worker's pool is `2n + 2` connections plus
an overflow of `n`: a pipeline and a heartbeat per run, the `LISTEN` connection and one for claims
and maintenance, so at most 50), `CLASSBEAT_WORKER_POLL_SECONDS` (2; the claim poll behind
`LISTEN jobs_queued`), `CLASSBEAT_WORKER_MAINTENANCE_SECONDS` (30; reap, expire, settle),
`CLASSBEAT_WORKER_SHUTDOWN_WAIT_SECONDS` (25, inside compose's `stop_grace_period: 30s`),
`CLASSBEAT_APPROVAL_TTL_HOURS` (168), `CLASSBEAT_JOB_BUSY_REQUEUE_LIMIT` (5 `operation_busy`
requeues per phase without an attempt) and `CLASSBEAT_JOBS_PER_USER` (2 queued or running jobs;
the jobs API answers 429 above it).
Song picking (`design_with_songs`, `classbeat.songs`, ADR 0008 section 2): `CLASSBEAT_SPOTIFY_POOL_SYNC_WAIT_SECONDS`
(120, at most 600) is how long a pick waits for MOWL's track list of the pool playlist to show exactly
the candidates just written; after it the pick fails `pool_not_synced` (retried once per phase). Worker and agent code write job data only through
`classbeat.jobs.fenced_session`; only `classbeat.jobs.fence`, `classbeat.jobs.credits`,
`classbeat.jobs.service` (the jobs API), `classbeat.jobs.progress` (the SSE stream's reads),
`classbeat.operations.user` (the user's Check again / Do it anyway), `classbeat.operations.orphans`
(marking orphaned class writes `uncertain` before a view or action), `classbeat.saving.reads` (the
save and update reconcilers' read-only read of the saved revision, SV1), `classbeat.keys_cli` and the
`classbeat.connections`, `classbeat.auth`, `classbeat.classes` (the class API), `classbeat.usage`
(the usage reads, US1) and `classbeat.db` packages may open a `user_session`
(`tests/test_session_guard.py`); job kinds live outside `classbeat.jobs`.
Revising by chat (ADR 0007 section 4): `CLASSBEAT_REVISIONS_PER_CLASS` (25 chat revisions per class, the first
design not counted), `CLASSBEAT_CHAT_MAX_REQUESTS` (50 user requests per class) and
`CLASSBEAT_CHAT_CONTEXT_REQUESTS` (10 earlier requests sent to the model); the smoke set has 18 cases
(15 designs, 3 revisions). Beside them are three song-picking smoke cases (ADR 0008 section 3,
`classbeat.evals.pick`; files in `classbeat/evals/smoke_pick/`): `pick_30`, `replace_two` and
`injected_title`, each run through the real `pick_pool` and `choose_songs` stages against a recorded
`FakeProvider` script, a synthetic Spotify catalogue and a synthetic MOWL analysis (no Spotify, MOWL or
model contact). They run in CI (`backend/tests/test_evals_pick_smoke.py`) and are not part of
`smoke --live`.
Progress (ADR 0005 section 6): `GET /api/jobs/{id}/events` streams a job's events as SSE (`Last-Event-ID`,
5 open streams per user and web process); each web process keeps one `LISTEN job_events` connection.
OAuth sign-in (ADR 0003 section 5): `CLASSBEAT_OAUTH_<PROVIDER>_CLIENT_ID` and the
`oauth_<provider>_client_secret` secret file switch on `google`, `github` or `microsoft`; a provider
with only one of them is off. Microsoft setup: add the `auth_time` optional claim (ID token) to
the app registration, or re-authentication with Microsoft always fails closed (ADR 0003 section 5);
Google needs no registration change (the app asks for `auth_time` itself).
Web and worker refuse to start as a superuser, a `BYPASSRLS` role or a table owner (no
override). In CI (`CI=true`) a missing
`CLASSBEAT_TEST_DATABASE_URL` or missing roles fail the database tests instead of skipping them.

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
- `/account/spotify` (`pages/SpotifyPage.tsx`, `components/account/Spotify.tsx`; signed-in users only) is
  where the Spotify callback ends: `?result=connected`, or a failure category shown as a sentence
  (`account_changed` asks the user to confirm the switch). It holds the connect wizard (the user's Client ID,
  32 hex characters; never a field for a Client Secret), the connection card (status, Connect Spotify again, change
  Client ID, Disconnect with its dialogs) and the "Working playlist" consent (give or revoke). With song
  picking off the API answers 404 `feature_off` and the page says "Song picking is not available right now."
- Colours, spacing and type live only in `frontend/src/styles/tokens.css` (light and dark).
- System fonts and local assets only; nothing is requested from a third-party host at runtime
  (`src/no-third-party-hosts.test.ts` guards the sources).
- CI runs the same five commands in the `frontend` job.

### Stack and worktrees

Docker Desktop (or Docker Engine with Compose v2) runs the whole stack: `db` (Postgres 17),
`migrate` (one-shot `alembic upgrade head`), `db-functions` (one-shot: the job queue's role and
`SECURITY DEFINER` functions, `docker/postgres/queue/20-queue.sql`, as the superuser; ADR 0005
section 3), `web`, `worker` and `mailpit`; web, worker, migrate and maint from one image
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

### End-to-end tests

Playwright (Chromium only) in `e2e/`, run against a running compose stack. The base URL is
`E2E_BASE_URL`, else `http://127.0.0.1:$WEB_PORT`; `WEB_PORT` is read from the worktree's `.env`,
so from a worktree no setup is needed:

```bash
# The OAuth specs need the test-only fake OpenID provider (compose.e2e.yaml, profile `e2e`):
alias dc='docker compose -f compose.yaml -f compose.e2e.yaml --profile e2e'
dc up -d --build --wait                  # the worktree's own stack plus the fake provider
cd e2e
npm ci
npx playwright install chromium          # once (CI: --with-deps)
npm test                                 # or E2E_BASE_URL=http://127.0.0.1:20007 npm test
npx playwright show-report               # HTML report after a failure
cd .. && dc down -v
```

The fake OpenID provider (`e2e/fake-oidc/server.py`) answers the providers' real back-channel
hostnames inside the compose network and issues ID tokens for Google and Microsoft; Playwright plays
the browser's visit to the provider.

The fake MOWL (`e2e/fake-mowl/server.py`) answers as `api.mowl.com` (the `mowl-workouts` base URL)
for the MOWL sign-in, the playlist list, the attach and the reads of a design run (invented music;
test playlists that hold a read or lose an attach answer, steered by `/__e2e/` calls the tests make
from the web container).

It also keeps programs for Save (create, segments, intervals, My Workouts and one program).
`/__e2e/save-arm?fail=<n>:<refuse|drop|noid>` injects a failure at a write request number,
`/__e2e/save-state` reports the counts, `save-programs` shows what each program holds and
`save-listing?hidden=1` hides them from My Workouts.

Web and worker trust one bundle of both fakes' throwaway CAs. The worker replays a
`backend/tests/agent_scripts/` script with the `fake` model provider: it reads the symlink
`/fake-run/script.json`, which starts on `good_first_draft.json` and which a test repoints with
`e2e/tests/model-script.ts` for the revise runs.

The fake Spotify (`e2e/fake-spotify/server.py`, service `spotify-fake`) answers as `api.spotify.com` and
`accounts.spotify.com` through compose network aliases and publishes no port. Its logic is
`backend/tests/spotifycore.py`, mounted read-only, the same code the backend tests run in-process
(`tests/spotifyfake.py`), so the two cannot drift; the `/__e2e/` control is reached from the web container.
Web and worker trust the same CA bundle (`SSL_CERT_FILE`), now including the Spotify fake's CA.

The specs: `design-draft.spec.ts` (draft, cancel while running, uncertain attach),
`class-save.spec.ts` (change, save, update, uncertain writes, another MOWL account),
`class-usage.spec.ts` and `fake-spotify.spec.ts` (web connects through the fake with PKCE, the worker
reaches it too, it publishes no port).

The fakes' state is shared, so Playwright runs one worker. The Playwright context blocks every
origin but the stack's. They use public test values only and are never part of the default stack.

The e2e stack trusts the docker network as proxy so each test presents its own `X-Forwarded-For`
address (separate rate limits). Sign-up is limited to 5 per IP and hour: use `down -v` between full
runs of a stack that is not given per-test addresses.

CI runs the same in the `e2e` job (when the stack changed) and uploads `playwright-report` only on failure.

### CI: what runs, and when

CI is expensive (every job bills at least a minute, ~15 minutes for a full run), so a pull request
only runs what its change needs. `ci.yml` has no `push` trigger: `main` only receives squashes of
an up-to-date, already-tested branch. It runs on `pull_request`, weekly (Monday 04:00 UTC, which
picks up new advisories) and on `workflow_dispatch`; the last two run everything.

| Job | Runs | What it does |
|---|---|---|
| `changes` | always | `scripts/ci-changes.sh`: `git diff` against the PR base gives the outputs `backend` (`backend/`, `docker/postgres/`), `frontend` (`frontend/`), `stack` (`backend/`, `frontend/`, `e2e/`, `docker/`, `Dockerfile`, `compose*.yaml`, `.dockerignore`, `.env.example`, `.trivyignore`, `scripts/worktree.sh`) and `all`. Renames count at both paths; names are read NUL-separated; if the diff cannot be computed everything runs. `all` is true on schedule/dispatch and when `.github/`, `.gitattributes` or `scripts/ci-changes.sh`, `ci-gate.sh` or `test-ci-gate.sh` change (CI changes test themselves) and makes the others true |
| `checks` | always | the cheap checks as steps of one job (below) |
| `backend` | `backend` | ruff, format, pyright, the queue functions' md5 regenerated on the pinned image (fails when `queue_digests.py` is stale), pytest against Postgres |
| `frontend` | `frontend` | lint, typecheck, Vitest, build |
| `e2e` | `stack` | the stack, the checks below, the Trivy image scan, then Playwright |
| `ci` | always | the required check; see the gate below |

A docs-only PR therefore runs `changes`, `checks` and `ci` (plus `pr-title`).

**The `ci` gate** (`scripts/ci-gate.sh`, tested by `scripts/test-ci-gate.sh` in `checks`) fails
unless `changes` and `checks` succeeded and every other job either succeeded or was skipped while
its filter output is `false`. A job that is needed (filter `true`) but skipped, cancelled or failed
fails the gate, and so does a job missing from `needs`, a short result list or malformed input (it fails closed). Add a new job to
`needs` of `ci`, to `ci-gate.sh` (and its filter) and to the tests.

### Security checks (CI)

The `checks` job runs these as steps; the image scan is a step of `e2e`:

| Step | What it checks |
|---|---|
| Secret scan | gitleaks over the pull request's own commits; the weekly and manual runs scan the whole history |
| bash -n, shellcheck | every `scripts/*.sh` |
| CI gate and change-filter tests | `scripts/test-ci-gate.sh` |
| Postgres image pinned alike | compose's `db` and `db-functions` and the `backend` job's service use the same exact `postgres` tag (the queue functions' md5 depends on it) |
| uv version, actionlint | `setup-uv`'s version equals the Dockerfile's uv tag (`scripts/ci-uv-version.sh`); actionlint (with shellcheck) on every workflow |
| pip-audit | `uv export --frozen` (production, then all groups) through `pip-audit` (pinned) |
| npm audit | `npm audit --audit-level=high` in `frontend/` and `e2e/` |
| Trivy repository scan | Trivy on the repository (vulnerabilities, Dockerfile misconfiguration; compose is not scanned) |
| `e2e`: stack checks | builds the stack once: `/api/ready` and the SPA answer, `web` and `worker` run as non-root, no secret value appears in `docker inspect` (the empty model key is replaced by a random canary first), only `worker` mounts `anthropic_api_key` and runs `fake`, no container has a model key variable, and `postgres_password` is mounted on `db` and `db-functions` only (never web, worker, migrate or maint) |
| `e2e`: Trivy image scan | Trivy on the image the running stack was built from (never pushed); then Playwright runs |

Trivy and gitleaks are checksum-verified release binaries. `pip-audit` (exact version) lives in
the `audit` dependency group of `backend/pyproject.toml`, so its own dependencies are hash-locked in
`uv.lock` and CI installs it with `uv sync --frozen --only-group audit`; the runtime image
(`uv sync --locked --no-dev`) never contains it. The audited requirements are exported with hashes
and checked with `--require-hashes`, for every group (`--all-groups`, so `audit` itself too).
`setup-uv` is pinned to the same uv version as the Dockerfile; the `checks` job fails if they
differ.

Trivy fails on HIGH and CRITICAL findings that have a fix available (`--ignore-unfixed`).
**Accepted risk:** unfixed HIGH/CRITICAL findings are not gated. They are revisited at the M6
security review, and once a fix appears Trivy starts failing on them. The repository scan covers lockfiles and
the Dockerfile (misconfiguration); it does not cover `compose.yaml`.

Every GitHub Action is pinned by full commit SHA with the version in a trailing comment.
Dependabot (`.github/dependabot.yml`, weekly, two groups per ecosystem: one PR for minor/patch updates and a separate one for majors) keeps Actions, uv, npm and the Dockerfile and compose
images current, with `build(deps)` and `ci(deps)` commit prefixes. It does not track Debian packages
inside the base image: when Trivy fails on one, bump the base image tag in the Dockerfile.

New advisory: **upgrade first** (bump the dependency or base image, refresh the lockfile). Only if
no fix exists or the code path is unreachable, add a dated ignore: `.trivyignore` (one ID per line,
with a `# reason` comment above and Trivy's `exp:YYYY-MM-DD` expiry, e.g. `CVE-2025-0001 exp:2026-12-31`)
or `--ignore-vuln ID` for pip-audit with the same comment. An expired ignore is removed in the next
PR that touches it. Never ignore without a reason.
