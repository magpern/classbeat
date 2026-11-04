# AGENTS.md: how agents build classbeat

classbeat is a website where indoor-cycling instructors describe a class, Claude picks the
songs and designs it, and the class is saved to their Intelligent Cycling (MOWL) account.
It is built autonomously by agents. The owner (`@magpern`) makes product decisions and gives
external approvals; agents do everything else.

**[docs/roadmap.md](docs/roadmap.md) is the frozen plan (Revision 6, with its dated amendments at the top). If this file and the
roadmap differ, the roadmap wins.** Section numbers (§1, §4, M2 …) refer to it.

## Codex entry point

Codex must read this file **in full**, then [docs/codex.md](docs/codex.md), the roadmap
and status before work. The Codex model mapping and PR attribution are authorized by
roadmap Amendment 10; the Claude role definitions below remain valid for Claude Code.
Codex role definitions are in `.codex/agents/*.toml`. Use the same issue, worktree,
CI, independent current-head review and merge gates. Never treat an in-session
`/review` as the independent Reviewer. If a fresh reviewer cannot be launched,
leave the PR open and report that review is pending.

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
the review gate. **Tooling is not security-critical by default** (roadmap Amendment 13, owner,
2026-11-03): compose files, deployment and CI/guard scripts, runbooks and the like have a Sonnet
Developer and a Sonnet Reviewer, except that a change to how secrets are mounted, exposed or
read (file modes or groups, secret handling in config) gets one final Opus review of the final
head after the Sonnet rounds, never an Opus Developer.

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
   billing); **Sonnet** is enough for the rest (UI, tests, docs, tooling; Amendment 13 for
   tooling that touches secret handling). The first review of any new security design is Opus.
   When in doubt, Opus.
3. **A new head SHA needs a new review**, scoped as follows (roadmap Amendment 2):
   - *Merge-only update:* the only change since the reviewed head is a merge of `main` with
     no hand-resolved conflict (`git diff <reviewed>..<head>` equals `main`'s own change), and
     CI is green. The Architect records `Agent review (merge check): PASS <head SHA> (carries
     <reviewed SHA>)`. If `main` changed a module the PR changes or calls, or a conflict was
     resolved by hand, a fresh reviewer checks the merge (scoped review).
   - *Fix round after a `FAIL`:* a **delta review** of the diff since the last reviewed head,
     verifying each earlier finding and whatever the delta touches. The same Reviewer is resumed
     for it on non-security PRs and for test-only fixes on security-critical PRs; a fresh Opus
     Reviewer only when the fix changes behaviour of security-critical application code (owner,
     2026-10-29; Amendment 13, 2026-11-03). After a second `FAIL` the Architect re-assesses the
     design (for guard checks, allowlists over blocklists) before another review.
     **Delta reviews by Sonnet (roadmap Amendment 4, owner, 2026-10-30):** a delta that only
     adds or changes tests or docs, loosens no guard test, and has no hand-resolved conflict in
     production code, may be reviewed by a fresh Sonnet even on a security-critical PR; it must still check each earlier finding by a mutation of the code
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
gh pr merge <n> --squash --match-head-commit <reviewed-sha>
```

and only when the PR's **current head** has green required checks (`ci` and `pr-title`), the
`PASS` is for that SHA and no blocking comment is open. `--match-head-commit` makes GitHub refuse
the merge if the head moved after the review. The squash commit's title is the PR title (with
` (#<n>)`), so there is no `--subject`, and the PR title must be the Conventional Commits subject
(`pr-title` checks it on the PR). Never `--auto`, never `--admin`, never an approving review,
never a change to the ruleset to get a merge through.

The ruleset on `main` (GitHub Pro): pull request required, required checks `ci` and `pr-title`,
squash only, no direct pushes, zero required approvals, and **no requirement that the branch is up
to date** (owner, 2026-11-04: every merge made every other open PR "behind" and cost it a full CI
run). There is **no merge queue** (not available on this repository). `CODEOWNERS` (`* @magpern`)
is informational.

So a PR is merged on its own green head and its `PASS`, without updating the branch. Merge `main`
into a PR branch only for a real conflict (GitHub says the PR is "dirty") or when `main` changed
files the PR changes or calls in a way that needs checking; that new head is then reviewed again
(review gate, item 3). The safety net for a PR that was green alone but breaks with something
merged earlier is the full CI run on `main` after every merge (see "CI: what runs, and when"):
a red `main` is fixed forward first, before other merges.

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
   also pushes `ghcr.io/magpern/classbeat-db` (Postgres with the init, queue and maint SQL; same
   tags), and creates the GitHub Release with generated notes (marked pre-release if the tag has a
   hyphen) and the deploy files as assets (`scripts/release-assets.sh`, `SHA256SUMS`; ADR 0020:
   a server needs no clone, `docs/runbooks/vps-deploy.md`). There is no `latest` tag; deployments
   pin a version.
4. Check the run with `gh run list --workflow release.yml`. The packages stay private; never
   change their visibility. Never move or delete a pushed tag: fix forward with a new version.

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

## Cursor alternative

For Cursor sessions, read [docs/cursor.md](docs/cursor.md) and use the explicit
`cursor-*` roles in `.cursor/agents/`. Roadmap Amendment 11 authorizes Composer
for routine implementation and Grok 4.7 High for security-critical implementation,
coordination, and every review. Claude Code and Codex keep Opus for security-critical
work. The shared
worktree, CI, independent current-head review, merge and safety gates still apply.
Verify the actual model used; a client substitution blocks that role. Claude Code
and Codex keep their own profiles.

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

The domain library `mowl` (`mowl-workouts`) is pinned as `backend/vendor/mowl_workouts-0.10.0-py3-none-any.whl`,
built from the v0.10.0 tag (commit `74e8c91c`; vendored wheel SHA-256 `c3188d13…`, release asset
`03024fe8…`, 67137 bytes) **without** the bundled Swedebeat app key by `scripts/build-mowl-wheel.sh`
(needs `gh` access to the private repo; it checks the tag's commit, the missing key file, the release
asset's pinned size and SHA-256, that the release wheel holds the key file by name (never read), and that
every other file and the `Requires-Dist` lines match the release wheel). After a rebuild run `uv lock`. classbeat leaves out
the library's `mcp` dependency (`exclude-dependencies`): it never runs the MCP server, and `mcp`'s
`httpx2` would replace httpx inside Authlib. Backend tests fake MOWL in-process (`tests/mowlfake.py`).
Because `mcp` is left out, the image's `mowl` and `mowl-mcp` console scripts fail on import: use only the library's modules `api`, `service`, `library`, `spotify_web`
and `swedebeat` (never the CLI or MCP entry points). The tag commit, the release asset's size and digest and
the vendored wheel's SHA-256 are recorded in full in the header of `scripts/build-mowl-wheel.sh`.
0.6.0 adds what Spotify song picking needs (ADR 0009 §12): ID-based `spotify_web` calls
(`create_playlist`, `add_items`, `replace_playlist_items`, `get_playlist`, `playlist_items` on
`/items`, `me`, `my_playlists`), `search_tracks(market=)`, `spotify.track_uri`, `SCOPES_PRIVATE`,
`MowlError.status`/`.retry_after`, and the `Credentials` options `spotify_refresh_enabled` (classbeat
always sets `False`: the library never reaches the token endpoint) and `spotify_max_retry_wait`
(classbeat sets `0`: the library never sleeps on a 429). Never use its name-based `save_playlist` or
`find_playlist_tracks` (CLI only).
0.7.0 adds `image` to the `playlist_items` and `search_tracks` track entries (ADR 0013 §9): the
URL of the album's smallest image at least 64 px wide, or `None`, never requested by the library
and not host-checked there. classbeat keeps it only as a song's `cover` in `song_list` content
`v: 2` (UI7, #251), and only when `classbeat.covers.cover_url` accepts it (a whole-string match of
`https://i\.scdn\.co/image/[0-9a-f]{40}`, checked before storing and again before returning;
anything else is `null`); it never goes into model input or comes from model output.
0.8.0 adds running (ADR 0018, M8 L1a/L1b, #462): the read side (`"sport": "running"` on a spec,
running validation, `tss` and `summarize` of a program with `ActivityTypeID: 1`; `workout.listed_sport`, which the
MCP `list_workouts` uses to mark non-cycling entries with `sport`) and a create-only running save (program
`ActivityTypeID: 1`, segments always `ActivityTypeID: 0`, running interval bodies with a float `Gradient`).
Updating a running program and giving one music are refused before any request, and a cycling save refuses a
spec whose intervals carry `gradient`. Cycling is otherwise unchanged; classbeat does not use the running calls yet.
0.9.0 adds running update and an existing playlist (ADR 0018, #482, #485): `workout.save` with `program_id`
updates an existing running program (it reads the target first and writes nothing unless it is that program with
its own `ActivityTypeID: 1`), and a running save may carry a positive `playlist_id` of a playlist already in MOWL
(-1 is none). `service.save` (`update_workout`, `mowl update`) reads every target first and refuses a sport mismatch
in either direction (`TARGET_SPORT`) and a program id that is not a positive int (`PROGRAM_ID`). Still refused before
any request: a new mix or playlist for running (`RUNNING_MUSIC`) and segment `ActivityTypeID: 1`; `RUNNING_UPDATE`
is gone. `workout.save`'s cycling requests are unchanged (a cycling update without `target` sends no read), and
classbeat does not use the running calls yet.
0.10.0 adds `image` to `spotify_web.my_playlists` entries (#537, ADR 0013 §9 amendment): the URL of the playlist's
smallest image at least 64 px wide by the 0.7.0 rule, except that when no image in the list carries a width (an
uploaded cover, listed with `width: null`) it is the first image with a non-empty string URL; never requested by the
library and not host-checked there. Track entries and every request are unchanged. classbeat uses it only in `GET /api/music/playlist-covers`
(`classbeat.design.routes`, #537, ADR 0013 §9 amendment): the New class picker's covers, read per request through
`spotify_call`'s read-only mode (no `record_result`, a 401 never flips, a refresh shielded and never cancelled; 8 s on
the listing requests, at most 10 pages), only when song picking admits the user, the Spotify connection is `connected`,
no `design_with_songs` or `revise_songs` job of theirs is queued or running (`jobs.service.cover_read_admitted`) and no
cooldown runs (a Spotify 429 starts one for its `Retry-After`, 60 s to 1 hour, `RateLimiter.start_cooldown`); its own
rate limit is 10 per user and 30 per IP per 10 minutes. It answers `{covers: [{spotify_playlist_id, cover}]}` with
`Cache-Control: no-store`, each cover a whole-string match of `classbeat.covers.playlist_cover_url`, never stored, never
model input; every failure is `{covers: []}`. The fake Spotify's playlists carry `images` from
`spotifycore.playlist_images` (the e2e control `playlist?...&id=&images=`).

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
`none` only for loopback or the compose `mailpit`; compose points them at mailpit;
`CLASSBEAT_SMTP_CA_FILE` (optional, not with `none`) is a readable PEM certificate file the server's
certificate must match instead of the system store, for Proton Mail Bridge; verification and the hostname
check stay on, see `docs/runbooks/proton-bridge-smtp.md`); the matching
`CLASSBEAT_DATABASE_PASSWORD` and `CLASSBEAT_DATABASE_OWNER_PASSWORD` variables are ignored.
The admin area (ADR 0011 section 5): `CLASSBEAT_MAINT_DATABASE_URL` (compose sets it on web) and the
`database_maint_password` secret file (web and `db-functions` only, which creates the role
`classbeat_maint` from `docker/postgres/maint/`) connect the admin routes as that role; without either
they answer 503 `admin_unavailable` and the rest of the app works, and web refuses to start when the
role is not exactly `classbeat_maint` (guard 11, `tests/test_maint_privileges.py`, checks its grants).
Admin Codes (`classbeat.admin.codes`, AD2): `/api/admin/invites` and `/api/admin/redeem-codes` list, create
and `.../{id}/disable`; a new invite or code is in its create response only (never a list, audit row or log),
disabling an invite expires it, and the invite statements live in `classbeat.auth` (guard 8).
Admin Settings (`classbeat.admin.settings`, AD4): `GET /api/admin/settings` (every flag with its effective value and source, the plans, the environment's bounds, the read-only settings), `PUT /api/admin/settings/{key}` (a flag through `classbeat.flags.core.validate_for_write`: `open` sign-up, an invalid value, a trial over `CLASSBEAT_TRIAL_DAYS_MAX` or above `instructor`, and any other key are 422 and a `refused` audit row), `DELETE /api/admin/settings/{key}` (clears the value to the JSON `null`, because maint has no `DELETE` on `app_settings`) and `PATCH /api/admin/plans/{id}` (the four limits and `max_class_minutes`, under the table's range and the environment's bounds; the gates `games_allowed`, `races_allowed`, `ftp_tests_allowed`; the prices `monthly_price_cents`, `intro_price_cents`, `intro_price_until`, `currency`, where `null` clears a price and an incomplete one is 422 `price_incomplete`; held credits and created jobs are untouched; #496). Audit params hold the key and what the value means, never banner text or a malformed currency. Prices are named, and `plans` read whole, only in `classbeat.plans.tables`, `classbeat.admin.settings` and `classbeat.admin.audit` (billing is off; `tests/test_plan_gates.py` pins it, `SELECT *`, `p.*` and `select(plans)` included).
Admin Model routes (`classbeat.admin.models`, AD5): `GET /api/admin/models` (plans with their `route_key`, routes with the result they stand on and why they are stale, results, the catalog), `PUT`/`DELETE /api/admin/models/routes/{route_key}/{stage_group}` (ADR 0012's `set_route`/`clear_route` inside the audited maint transaction; a `RouteRefused` is 409 with its reason, `no_route` 404). A model job's `route_key` is the effective plan's `plans.route_key` (migration 0014: the plan's own id, so routes are set per plan key with the models CLI or this API; a plan without a route uses the bootstrap choice, and a route on `default` covers no seeded plan), set by `classbeat.quota.route.with_plan` in each of the four kinds' `admit` and never taken from the browser. maint holds `SELECT` on `model_eval_results` and `SELECT, INSERT, UPDATE, DELETE` on `model_routes` (guard 11).
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
live use. Once an Anthropic stream has begun, any end without a readable answer (read timeout, the turn bound, an error event, a cut connection, no stop reason) is recorded at the turn's ceiling, not 0; only an error status or a failure before the stream is unbilled. The worker's outbound requests go only to `https://api.mowl.com`, `https://api.spotify.com` and
`https://accounts.spotify.com` plus the host of each model provider whose key file holds a key (`api.anthropic.com`,
`api.openai.com`; `api.deepseek.com` only with `CLASSBEAT_DEEPSEEK_ENABLED` too; `worker_model_providers`, `worker_hosts`
in `classbeat.egress`, ADR 0009 section 10, ADR 0012 section 2); each model client alone is limited to its own provider's host. The temporary-link media host of a Swedebeat mix (ADR 0016 §4) is on no urllib list: it has its own scoped list, the literal `MOWL_MEDIA_HOSTS` (empty until the owner confirms the host), and its own no-redirect client, `egress.mowl_media_async_client()`; its fakes are `tests/mediafake.py` and the Swedebeat calls of `tests/mowlfake.py`. The one exception to the DeepSeek rule: the owner's live eval of a disabled DeepSeek model installs that host for that command only (`install_choice_allowlist`); jobs never get it. The Spotify token endpoint and `GET /me`
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
The regression suite's live run (ADR 0012 §4.4, `classbeat.evals.suite_live`): `docker compose exec worker
python -m classbeat.evals suite --live --provider P --model M --effort E --repeat 3 --max-usd N --out FILE
[--record --authorization TEXT]`. It refuses (exit 2, rule only) without `--live`, under `CI`, for a model or
effort outside `classbeat.agent.catalog` (so `fake`), without that provider's key file and with a refused SDK
variable; prints the plan and cost ceiling and stops without `--max-usd`; installs the worker allowlist with only
that provider's model host before building it (`provider_for_choice`) and refuses a built adapter that is not the
choice's provider. It runs both stage groups (design cases, and song cases on the synthetic catalogue), writes
format 2 and prints the verdict per group (`gate.evaluate` against the digest-verified manifest). `--record`
writes one `model_eval_results` row per group through `classbeat.models.store.record_result`; the verdict
`enable` relies on. Each run needs the owner's go-ahead; agents and CI never run it (the offline test is
`backend/tests/test_evals_suite_live.py`).
Models, routes and the enable gate (ADR 0012 sections 2 and 5, M3c): `docker compose exec worker python -m classbeat models
list | results [--provider P] [--model M] | enable KEY GROUP PROVIDER MODEL EFFORT [--yes] | disable KEY GROUP`. A route
(`default` is the only key until M5) sends a stage group (`design`, `songs`) to a model and exists only on a recorded passing
`suite --live --record` result of the running suite version, manifest digest and prompt versions; `enable` prints the result it
relies on and needs `--yes`. Secret files (worker only): `anthropic_api_key`, `openai_api_key`, `deepseek_api_key`; settings:
`CLASSBEAT_MODEL_PROVIDER` (`anthropic` or `fake`), `CLASSBEAT_AGENT_MODEL` and `_EFFORT` (the bootstrap, used with no route),
`CLASSBEAT_DEEPSEEK_ENABLED` (false). OpenAI and DeepSeek catalog entries ship `data_policy: owner_pending`, so no route to them
can be set until the owner's answers (a reviewed catalog change); DeepSeek also needs the setting. The owner's steps for live evals
and enabling are `docs/runbooks/m3c-model-enable.md`; the end-to-end offline proof is `backend/tests/test_m3c_done_when.py` and
`test_m3c_done_when_jobs.py` (replayed provider transcripts via `tests/m3ctools.py`).
Swedebeat (ADR 0015, S1): `POST`/`DELETE /api/connections/swedebeat` (recent authentication, step-up; `GET /api/connections` shows the row, the email as `account` and a `swedebeat` block `{enabled, available, uses_swedebeat}`). Per-user "I use Swedebeat" (#525, ADR 0015 amendment of 2026-11-03): `PUT /api/connections/swedebeat/preference {uses_swedebeat}` stores it in `user_preferences` (migration 0028, owner RLS, one row per user, no row is off; `classbeat.connections.preferences`); it only narrows the flag. Off: connect, the mixes listing, a new design on a mix (the hand-off's design included), a `swedebeat_match` job, a catalogue replace and starting the hand-off answer `feature_off` before any call, and such work already queued (a new design, a match, a replace's re-match) ends `feature_off` before any request; Disconnect, export and deletion never read it; a revise of a class already on a mix and the steps of a hand-off already started do not either (ADR 0015 amendment). Swedebeat tests turn it on with `tests/preftools.py`, e2e specs with `useSwedebeatThroughUi`. It is off unless `CLASSBEAT_SWEDEBEAT_ENABLED` is true (compose `SWEDEBEAT_ENABLED`, default false, web and worker: a design job on a mix checks it again when it runs) and the admin flag `swedebeat_enabled` (`off`/`on`, can only tighten it) allows it; off, connect answers 404 `feature_off`. Swedebeat's AES app key is **never in git, a PR or CI image or a log**: the release workflow (tags only) writes it from the owner's repository secret `SWEDEBEAT_APP` into `/app/swedebeat_app.json` of the image through a BuildKit secret (`REQUIRE_SWEDEBEAT_APP=1` fails the build when it is missing or not 32/16 characters), and the optional Docker secret file `swedebeat_app` (web only; empty is none; `scripts/worktree.sh init` creates it empty) overrides it at runtime; with neither, connect answers 503 `app_key_unavailable`. The worker refuses to start if it can read the secret file. Each user has one device id (an HMAC of the user id under `app_secret_key`); `force` is sent only after the user confirms signing another device out. Tests use the fake in `backend/tests/swedebeatcore.py` (it decodes the library's AES fields with a throwaway TEST key made per run); the e2e stack runs it as `www.swedebeat.se` with the public test key `e2e/fake-swedebeat/app.json`. `backend/tests/test_swedebeat_key_guard.py` pins where the key may and may not be. The web's urllib list has `www.swedebeat.se` (not `cloud.swedebeat.se`). Owner steps: `docs/runbooks/swedebeat-connect.md`. Swedebeat mixes (ADR 0016, A2; same flag, 404 `feature_off` when off, MOWL connection only, no Swedebeat token): `GET /api/music/swedebeat-mixes` (`classbeat.design.mixes`; 30 per user and 60 per IP per 10 minutes; ids, names, lengths and an `analysed` badge) and the external operation `mowl_attach_swedebeat_mix` (`classbeat.operations.swedebeat`: the id must be in the caller's own fresh listing, the link is a `mowl_playlist_links` row with `source = 'swedebeat'`, the reconciler reads the listing's `PlaylistID`, unverified live). `swedebeat_mix_maps` (migration 0018) holds the user's analyses; Disconnect MOWL and an account change delete them with the links. A3 (`classbeat.design.swedebeat_music.load_swedebeat_mix`, wired into `design_draft` by A4): a cache hit (under 30 days, this MOWL connection and playlist, the library's current map version) makes no request; otherwise the listing, attach, indexing, `TemporaryLinks`, the fetch (`mixfetch`: raw bytes, identity encoding only, 250 MB, 300 s, a private directory swept at worker start) and the analysis in a child process (`mixanalysis`/`mixchild`: 1.5 GiB RSS, 180 s, decoded length at most the listed length plus 5 minutes and 120 minutes); job categories `media_host_not_allowed` (the production `MOWL_MEDIA_HOSTS` is still empty), `media_refused` and `analysis_failed` (migration 0019); at most 20 maps per user. A4 (`design_draft` and `revise_draft` music source `{source: "mowl_swedebeat_mix", mix_id}`): `admit` (`classbeat.design.mixadmit`) refuses `feature_off`, `not_connected`, `invalid_input` (not in the user's own fresh listing) and `analysis_limit` (10 new analyses per user in 24 hours, `swedebeat_analyses`, migration 0020; a cache hit costs none); the worker ends a queued job `feature_off` when the flag is off by then and takes the daily slot before any attach or fetch; the model sees only song numbers (`model_input.mix_summary`); Save reads the playlist from the revision's `swedebeat` link. Matching (ADR 0016 B1 to B5): the fake Swedebeat answers catalogue searches from the e2e control `catalogue` (`found` and `unsure` titles; any other title has no hit), the e2e worker trusts its CA, and `e2e/tests/swedebeat-match.spec.ts` runs match, replace, approve, the hand-off and its override with axe on the match view; the owner's live checks are `docs/runbooks/swedebeat-match.md`.
Running classes (ADR 0018, M8 R1a, #463): `Brief.sport` is `cycling` (absent, every older brief, and left out of a cycling brief's `model_dump`, so evals and the size guard are byte-identical) or `running`; a new class's `classes.brief` writes it explicitly (`Brief.stored()`), and it never changes once the class has a revision or a stored brief (`classbeat.design.sport`: `invalid_input` at creation, rechecked in the design's final store). A running brief refuses `climbs`, `sprints`, `races`, `ftp_test` (even at their defaults) and the `ftp_test` class type; it designs on an existing MOWL playlist only (no Swedebeat mix, no `design_with_songs`: 422). Off unless `CLASSBEAT_RUNNING_ENABLED` (compose `RUNNING_ENABLED`, default false, web and worker) and the admin flag `running_enabled` (`off`/`on`, can only tighten it) allow it: off, a running design or revise is refused 409 `feature_off` and a queued one ends `feature_off` before any call. `design_draft` and `revise_draft` branch on the sport (`classbeat.agent.running`: its own system prompts and prompt versions, `RunningDraft` of `sec`, `zone` 1 to 5, `gradient` 0 or 1 and `cue`, `workout.validate_running`, TSS always approximate; `running_input` is the model input, the cycling brief's allowlist minus the bike knobs plus `sport`); the running stages use the `design` route, and only a route whose recorded result covers running: E1 (#465, ADR 0012 amendment of 2026-11-03) ends a running design or revise `model_config` before any call unless its `design` route is not stale for any reason (`ModelChoice.running_covered`; the bootstrap choice never covers running, `fake` does), and the admin refuses to store `running_enabled` `on` (422 `running_not_evaluated`) unless every plan's `route_key` has such a route (`classbeat.models.routes.covers_running`); the design group's prompt versions are `design`, `revise`, `running_design` and `running_revise` (`classbeat.models.versions`). Rules: `classbeat.design.sport_rules` (running profiles only, `info` only; weak cells give none; a suite `RULE_SOURCES` entry with `agent/running_input.py` since suite version 10). The owner's live running eval: `docs/runbooks/running-evals.md`. A running revision's provenance has `sport: "running"`. Save and Update (M8 R1b, #490, ADR 0007's R1b amendment) take the class's sport from its stored brief only (absent is cycling): a running class is written by the same `workout.save` call (program `ActivityTypeID` 1, segments 0, float `Gradient`, the class's existing playlist, never new music; a revision on a mix or picked songs is `invalid_input`), Update never crosses sports (`program_other_sport` both ways, `overwrite` included), `running_enabled` off is `feature_off` at creation and in the worker, and `mowl_state`'s running form leaves every cycling `state_hash` unchanged. The class list and detail carry `sport` for every class (`cycling` for older rows).
Soundless classes (ADR 0007 amendment of 2026-11-03, SL1 #527, SL2 #528, SL3 #529, SL4 #530; SL5 to SL7 follow): `design_draft` takes the music `{source: "none"}` (`NoMusicChoice`, cycling only: a running brief with it is 422) and designs with **no** `music` stage and no MOWL or Spotify call: `classbeat.agent.soundless` (own system prompt, tools `preview_draft` and `submit_draft` only, `SoundlessDraft` whose cadence is a range only, so `"beat"` is refused by the schema and again by the check; `workout.validate` of the draft as written, no fitting, no quality report, no music warning, no rules section; prompt version key `soundless_design`), model input `agent/soundless_input.py` (the brief and one fixed sentence, no `music` key; **not** in the suite's `RULE_SOURCES`, so `suite_version` stays 10 until SL5), the revision's music `{"source": "none"}` (`NoRevisionMusic`), provenance `map_version` and `crossfade_sec` `null`, and the class's first `music_source` `{"source": "none"}`. A class's music kind never changes (`classbeat.design.music_kind`: `admit_music` 409 `invalid_input` at creation, `check_stored` in the final store). The gate has three conditions, all checked on every read: `CLASSBEAT_SOUNDLESS_ENABLED` (compose `SOUNDLESS_ENABLED`, default false, web and worker), the admin flag `soundless_enabled` stored exactly `on` (migration 0027; missing, `null` after DELETE or invalid is `off`, unlike `running_enabled`), and every plan's `route_key` having a `design` route that covers soundless (`classbeat.models.routes.covers(db, key, "soundless")`: not stale for any reason and `suite_version >= SOUNDLESS_SUITE_VERSION`, which is `None` in SL1, so nothing covers until SL5). `GET /api/connections` returns the effective `soundless_enabled`. `admit` refuses, before any credit is reserved and in this order, 409 `feature_off` (the switch), `soundless_not_evaluated` (the job's own plan's route) and `feature_off` (every plan's route); the worker ends a queued job `feature_off` (switch off) or `model_config` (`GroupModel.soundless_covered` false) before any call. The admin refuses to store `on` without coverage (422 `soundless_not_evaluated`, `refused` audit row). The web counts `fake` as covering only when its own `CLASSBEAT_MODEL_PROVIDER` is `fake` and `CLASSBEAT_ENVIRONMENT` is not `prod`; `compose.yaml` passes no model provider to web, so **a plain dev stack cannot switch soundless on** (only a stack that sets it for web, the e2e stack of SL7). `revise_draft` of a soundless class (SL2, `design.revise._run_soundless`) goes through the same gate at `admit` (`music_kind.admit_class`: the three conditions in the same order, before the job row, nothing written) and in the worker (`still_on`, `require_covered`, before any call); it enters no `music` stage and makes no MOWL or Spotify call, runs the soundless `revise` stage (`classbeat.agent.soundless.soundless_revise_stage`: own prompt, `preview_draft` and `submit_draft` with an optional note, `SoundlessDraft`, **no** `songs_requested`, so a request about other songs can never end `use_change_songs`; prompt version key `soundless_revise`, the sixth of the `design` group), its message `soundless_input.revise_message` (brief, base draft, earlier requests, request: no `music` key, no playlist sentence, no `MUSIC_CHANGED`/`SONGS_CHANGED`), and stores a child revision with music `{"source": "none"}`, `origin = revise_draft`, the parent and compare-and-set as for any revise, after `check_stored` in the final store. Save and Update (SL3, #529, ADR 0007 amendment item 8): the approval refuses `invalid_input` unless the revision's music is `none` exactly when the class's `music_source` is `none` (a running class with `none` too); a soundless revision reads no link (`approval.playlist_for`, at creation, in `prepare` and in the guard under `hold`; `music_unlinked` cannot happen) and is written by `workout.save(spec, creds, -1)` (`PlaylistID: -1`, Update the same with `program_id`); the request hash and context carry `-1` (a playlist class's are unchanged), `update_operation._known` accepts `-1` as the playlist only, and the saved row's `playlist_id` is NULL (`operation.stored_playlist`; the column's `CHECK (playlist_id > 0)` stays). `mowl_state` writes "no music" as `playlist_id: null` and reads a `PlaylistID` of `-1`, `null` or absent as `null`; any other value never matches (verify leaves `state_hash` NULL, a reconciler is inconclusive); every stored `state_hash` is unchanged (golden hashes in the test). The switch (environment and stored value; no coverage, no model call) is 409 `feature_off` at creation and `feature_off` in `prepare`; the reconcilers never read it. **Unverified live** (the owner's steps in the amendment): which of the three forms MOWL reads back, and whether a cycling Update with `-1` clears a playlist attached in the app; the fakes serve each case (`savetools.FakeMowlPrograms.no_music_read`, `keep_playlist`; the e2e fake's `/__e2e/save-no-music?read=-1|null|absent&keep=0|1` and `save-playlist`). SL4 (#530): the class list and detail carry `soundless` (true only for a stored `music_source` `{"source": "none"}`, false for every older class); song picking (`design_with_songs` in every mode, `revise_songs`), `swedebeat_match`, every Swedebeat hand-off step (422) and `swedebeat_verify_mix` refuse a soundless class `invalid_input` at creation by reading `music_source` (`music_kind.refuse_soundless`), and their final fenced stores refuse to write over a `none` source (`check_stored(fs, soundless=False)` in `songs.phase2` before `adopt_first`, in `songs.revise` before its compare-and-set, in the match store and the verify record; the hand-off adopt's compare-and-set skips a `none` source), so nothing is written; the class page hides Change songs and the Swedebeat section for it. Tests: `tests/test_agent_soundless.py`, `tests/test_soundless_jobs.py` (script `soundless_first_draft`), `tests/test_soundless_revise.py` (scripts `soundless_revise_good`, `soundless_revise_second`, `soundless_revise_songs_requested`), `tests/test_save_soundless.py`, `tests/test_soundless_songs.py` and the soundless cases in `tests/test_swedebeat_match.py` and `tests/test_swedebeat_verify.py`, `frontend/src/pages/soundlessClass.test.tsx`.
Song picking and the Spotify connection (ADR 0008, ADR 0009): `CLASSBEAT_SONG_PICKING` (default off; off,
every Spotify route answers 404 `feature_off`; the owner switches it on, roadmap Amendment 1; with it on,
`CLASSBEAT_PUBLIC_BASE_URL` may not be `localhost`, use `127.0.0.1`, because Spotify does not accept
`localhost` in a redirect URI), `CLASSBEAT_SONG_JOB_COST_MICROS` (3000000, $3; the song-picking kinds' own
ceiling across their stages and attempts, 0 to 100000000), `CLASSBEAT_SPOTIFY_FLOW_TTL_SECONDS` (600, 60 to 600;
how long a connect flow lives), `CLASSBEAT_SPOTIFY_REFRESH_MARGIN_SECONDS` (120, 0 to 1800; a token with less
than this left is refreshed), `CLASSBEAT_SPOTIFY_REFRESH_TIMEOUT_SECONDS` (10, above 0 and at most 20; the
refresh request's total deadline), `CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS` (unset, at most 86400; lets the
reconciler mark an uncertain playlist create `failed` when the user's listing shows no playlist with its `ref`
and the call returned at least that long ago; leave it unset until the owner has measured Spotify's listing
delay at live activation) and `CLASSBEAT_SPOTIFY_CALLS_PER_JOB` (300, 1 to 10000; the Spotify requests one job may
send across its stages and attempts, token refreshes included, counted per request and redirect hop by
`egress.RequestBudget` and stored in `jobs.spotify_calls` with each stage and each attempt's ending; the request over it is never
sent and the job ends `limit_exceeded`). Three attempts in a row ending with a Spotify 429 end the job
`quota_exhausted` (a MOWL 429 does not count; `jobs.spotify_rate_limits`), and the job keeps the last Spotify
`Retry-After` (`jobs.spotify_retry_after`, cleared when an attempt finishes or fails without a Spotify 429; a cancel, an `operation_busy` requeue, a hand-back or a failure before the kind runs leaves it as it is) so the page's sentence names the minutes or hours (ADR 0009 section 8;
migration 0013). There is no Client ID setting and no Client Secret: each user enters their own
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
save and update reconcilers' read-only read of the saved revision, SV1), `classbeat.plans.account`,
`classbeat.plans.redeem` and `classbeat.plans.trial` (the user's plan read, redeem and trial, PL1), `classbeat.keys_cli` and the
`classbeat.connections`, `classbeat.auth`, `classbeat.classes` (the class API), `classbeat.usage`
(the usage reads, US1), `classbeat.account` (the user's export and deletion, ADR 0014) and `classbeat.db` packages may open a `user_session`
(`tests/test_session_guard.py`); job kinds live outside `classbeat.jobs`.
Revising by chat (ADR 0007 section 4): `CLASSBEAT_REVISIONS_PER_CLASS` (25 chat revisions per class, the first
design not counted), `CLASSBEAT_CHAT_MAX_REQUESTS` (50 user requests per class) and
`CLASSBEAT_CHAT_CONTEXT_REQUESTS` (10 earlier requests sent to the model); the smoke set has 26 cases
(21 designs, 5 revisions; 7 of them running classes, `smoke/run_*.json`, scripts in `tests/runningscripts.py`). Beside them are three song-picking smoke cases (ADR 0008 section 3,
`classbeat.evals.pick`; files in `classbeat/evals/smoke_pick/`): `pick_30`, `replace_two` and
`injected_title`, each run through the real `pick_pool` and `choose_songs` stages against a recorded
`FakeProvider` script, a synthetic Spotify catalogue and a synthetic MOWL analysis (no Spotify, MOWL or
model contact). They run in CI (`backend/tests/test_evals_pick_smoke.py`) and are not part of
`smoke --live`.
Account data (ADR 0014, #337, #349): `POST /api/account/export` (one JSON file, 5 per user and hour) and `POST /api/account/delete {confirm_email}`, both behind recent authentication and the step-up; deleting cancels open jobs and settles their credits first, then the cascade runs. Guard 12 (`tests/gdpr_guard.py`, lists in `classbeat.account.registry` and `classbeat.account.coverage`) fails CI for an owned table with no export entry and for a foreign key to `users` or a user-looking `uuid` column with no deletion decision.
Plans (ADR 0011 sections 2 and 4, PL1): `GET /api/account/plan`, `POST /api/account/redeem` (10 per user and 30 per IP
and hour) and `POST /api/account/trial`; `CLASSBEAT_TRIAL_DAYS_MAX` (30, 0 to 366; 0 turns trials off) clamps the
stored `trial` setting's days when a trial starts, and a trial's plan is clamped to at most `instructor`.
Quotas (ADR 0011 sections 3 and 4, QT1): the jobs API reserves a credit through `classbeat.quota.MeteredLedger`
(the period's `quota_periods` row locked, then the held count) and counts the plan's `concurrent_jobs`; every limit
is the plan's under an environment bound: `CLASSBEAT_CLASSES_PER_MONTH_MAX` (200, 0 to 10000),
`CLASSBEAT_MONTHLY_COST_MICROS_MAX` (300000000, $300), `CLASSBEAT_JOBS_PER_USER` and `CLASSBEAT_REVISIONS_PER_CLASS`.
Refusals: 409 `quota` (`quota` `classes` or `cost`, `limit`, `used`, `period_end`, `plan`; no money for `cost`) and 429
`busy` with the plan's `limit`.
Class length and feature gates (#496, ADR 0011 amendment 2026-11-03, migration 0026): each plan's `max_class_minutes` (under
`CLASSBEAT_CLASS_MINUTES_MAX`, 180, 10 to 180) and `games_allowed`, `races_allowed`, `ftp_tests_allowed`; seeded Free 30 without
games, Instructor 60, Pro 90. `classbeat.quota.route.with_plan` (every model kind's `admit`) refuses a brief over the cap (409
`quota` `length`, `limit` and `used` in minutes) or naming races or an FTP test the plan lacks (`feature`, with `feature`),
checking a revise kind's stored class brief, and stores the plan's `plan_gates` (`classbeat.plan_gates`, a server field) with the
job; the design and revise stages refuse a draft with a game, an FTP test or a length (plus 5 %) the job's gates do not allow
(`not_allowed`, for the model to fix). A job stored without `plan_gates` (before #496) fails closed: `with_plan_run` runs it under the user's current plan (`invalid_input` for a brief over it), and its song-list approval checks the current plan too (409 `quota`). Make current and the `admit` of Save and Update check the stored draft against the current plan (409 `quota` `feature` or `length`; `classbeat.quota.gates.check_stored_draft`). `GET /api/account/plan` adds `limits.max_class_minutes` and `gates`.
Flags (ADR 0011 section 7, FL1): `classbeat.flags` reads the `app_settings` rows per request and joins them with the environment, stricter wins: the stored `signup_mode` (`invite` or `closed`) can tighten `CLASSBEAT_SIGNUP_MODE` and never open it; `song_picking` (`off`, `granted` (default), `on`) is `off` while `CLASSBEAT_SONG_PICKING` is off, and `granted` admits only users whose effective features include `song_picking`; `trial` and `banner` (text of at most 280 characters, level `info` or `warning`) are validated. An invalid stored value falls back to the key's stricter default and is logged by key only. `GET /api/settings/public` (no sign-in) returns `{banner, signup_mode}` and nothing else.
Progress (ADR 0005 section 6): `GET /api/jobs/{id}/events` streams a job's events as SSE (`Last-Event-ID`,
5 open streams per user and web process); each web process keeps one `LISTEN job_events` connection.
OAuth sign-in (ADR 0003 section 5): `CLASSBEAT_OAUTH_<PROVIDER>_CLIENT_ID` and the
`oauth_<provider>_client_secret` secret file switch on `google`, `github` or `microsoft`; a provider
with only one of them is off. Microsoft setup: add the `auth_time` optional claim (ID token) to
the app registration, or re-authentication with Microsoft always fails closed (ADR 0003 section 5);
Google needs no registration change (the app asks for `auth_time` itself). An OIDC re-authentication
counts only with an `auth_time` of the last 5 minutes (60 s skew); a fresh `iat` never does, and Google
gets no `prompt=login` (ADR 0003 §5 amendment of 2026-11-03, #522: Google may send no `auth_time`, and
then its re-authentication fails closed and the page offers the password). Every callback that does
not end `ok` logs one WARNING `oauth callback refused: provider=… purpose=… outcome=… why=…` (never a
code, state, nonce, token or email); a failed re-authentication's `auth_events` row has `why`
`auth_time_missing`, `auth_time_old` or `auth_time_future`.
`CLASSBEAT_OAUTH_VISIBLE_PROVIDERS` (default `google`; a comma-separated list of `google`, `github`,
`microsoft`) is which of the enabled providers the sign-in and sign-up screens offer; the others stay
enabled and work, only their buttons are hidden. A name that is none of the three (a typo) is logged
once as a warning (the name only) and offers nothing. `compose.prod.yaml` passes them to **web only**
(#507): `CLASSBEAT_OAUTH_<PROVIDER>_CLIENT_ID` from `OAUTH_<PROVIDER>_CLIENT_ID` (default empty, off) and
`CLASSBEAT_OAUTH_VISIBLE_PROVIDERS` from `OAUTH_VISIBLE_PROVIDERS` (default `google`), and mounts the three
`secrets/oauth_<provider>_client_secret` files on web only (they must exist; empty is off; the redirect URI
is `<CLASSBEAT_PUBLIC_BASE_URL>/oauth/<provider>/callback`; `scripts/check-prod-compose.sh` checks the
placement and that no client secret is a variable; owner steps: `docs/runbooks/vps-deploy.md` section 5a).
The dev `compose.yaml` passes them the same way, with every secret file empty from `scripts/worktree.sh init` (every provider off until set); `compose.e2e.yaml` sets them for the e2e stack.
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
- Colours, spacing and type live only in `frontend/src/styles/tokens.css` (Night Studio, ADR 0013 section 2:
  dark by default, light with `data-theme="light"`, `data-theme="system"` follows the device). No colour literal
  (hex, `rgb(`, `hsl(`) may appear anywhere else in `src` (`src/styles/tokens.test.ts`).
- Tailwind CSS v4 (`@tailwindcss/vite`, no preflight): `src/styles/tailwind.css` maps `@theme` to the token
  variables, so utilities are `bg-surface`, `text-muted`, `border-line`, `bg-brand` and so on. Reduced motion
  turns animations and transitions off there.
- Components: shadcn/ui-style wrappers on Radix primitives in `src/components/ui/` (Button, Card, Chip and
  ChipButton, ToggleGroup, Dialog, DropdownMenu, Tabs, Switch, Tooltip, Toast with `sonner`, Progress, Select),
  merged with `cn()` from `src/lib/cn.ts`. Icons are `lucide-react`. New UI uses these, not new hand-written CSS.
- Theme: `src/theme/theme.ts` and `ThemeSwitch` (Dark default, Light, Same as device) keep the choice in
  `localStorage` (try/catch, the page works without it); `public/theme-init.js` applies it before first paint.
- Fonts are bundled (`@fontsource-variable/inter`, `@fontsource/archivo` 700 and 800, SIL OFL) and served from
  our origin; nothing is requested from a third-party host at runtime (`src/no-third-party-hosts.test.ts`
  guards the sources), with **one allowed exception**: album and playlist covers from Spotify's image hosts
  `https://i.scdn.co`, `https://mosaic.scdn.co`, `https://image-cdn-ak.spotifycdn.com` and
  `https://image-cdn-fa.spotifycdn.com` (ADR 0013 §9 and its amendment of 2026-11-03), named and shown only
  in `components/songs/CoverArt.tsx` (`<img referrerpolicy="no-referrer" loading="lazy">`, initials tile
  otherwise; `kind="album"` takes the album pin only, `kind="playlist"` the three playlist shapes; the guard
  exempts those hosts in that file only, and only `CoverArt` renders an `<img>`).
- The web process sends the app's Content-Security-Policy as a response header on every response
  (`classbeat.headers`, never a `<meta>`): `'self'` for everything, `img-src 'self'` plus the path-scoped cover
  sources of `classbeat.covers.IMG_SOURCES` (`https://i.scdn.co/image/`, `https://mosaic.scdn.co/300/` and
  `/640/`, `https://image-cdn-ak.spotifycdn.com/image/`, `https://image-cdn-fa.spotifycdn.com/image/`), no
  inline script or style, no `data:`, `object-src`/`base-uri`/`frame-ancestors 'none'`; plus
  `Referrer-Policy: no-referrer`. New UI must work under it: no inline `<script>` or `<style>`, no `data:`
  images (the TOTP QR code is drawn as inline SVG for this reason), no other host.
- Pin exact versions of every package added; licences must be MIT, ISC, Apache-2.0 or OFL.
  Accepted exception (Architect, #258): `lightningcss` (MPL-2.0), a build-time dependency of Tailwind's Vite
  plugin; unmodified and not shipped in the bundle. Second, named by ADR 0013 section 10 and issue #255:
  `@axe-core/playwright` and its `axe-core` (both MPL-2.0), dev dependencies of `e2e/` only, unmodified, never
  built into the image or the bundle.
- Bundle budget (ADR 0013 section 11): `npm run check:bundle` (`scripts/bundle-size.ts`, after `npm run build`)
  gzips the scripts `dist/index.html` loads first (entry and static preloads, not lazy chunks like the admin
  area) and fails above 250 kB (1000 bytes per kB, as Vite reports); `src/bundleSize.test.ts` tests the rules.
- CI runs the same commands in the `frontend` job, then `check:bundle`.

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
Compose bind-mounts a file secret with its host owner and mode (outside Swarm it ignores `uid`,
`gid` and `mode`), so `scripts/worktree.sh init` makes the dev files 644 inside a 700 folder. In
production (`compose.prod.yaml`, #511) every file is the deploy user's, group 10010, mode 640, and
every service that mounts a secret has `group_add: ["10010"]` and runs as a non-root user (`db` and
`db-functions` as `postgres` from the start: the image's gosu drops supplementary groups);
`scripts/check-prod-compose.sh` checks both (a best-effort lint against honest mistakes in the
committed compose files, not a security boundary; a default-deny key allow-list is #517), and the
real files' mode and group on the server (runbook section 5), which is the actual boundary. An unreadable optional secret file (`smtp_password`, `oauth_*_client_secret`,
`swedebeat_app`) counts as not set with a warning naming the field; any other unreadable one is
fatal; a file of blanks is empty, except the three database passwords (read byte for byte).

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
# fake-spotify.spec.ts runs `docker compose config`: set COMPOSE_FILE=compose.yaml:compose.e2e.yaml (`;` on Windows)
# and COMPOSE_PROFILES=e2e as CI does, or it does not see the fake services.
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

Song picking is on in the e2e stack only (`CLASSBEAT_SONG_PICKING: "true"` on web and worker in
`compose.e2e.yaml`); the defaults keep it off. `e2e/tests/spotify-helpers.ts` holds the fake Spotify's public
test data (`CLIENT_A` allows `USER_A`, `CLIENT_B` allows `USER_B`, `CLIENT_UNKNOWN` is no app), its control
calls from the web container (`/__e2e/reset`, `sign-in?user=`, `deny`, `inject?...`, `state`: every request
with the app and user of its token, never a token, code or verifier) and `playSpotify(page)`, which plays the
browser's visit to `accounts.spotify.com/authorize` (the browser cannot reach the fake: the helper asks it
from the web container without following the redirect and hands the page the answer). The fake's signed-in
user is one global setting, so a spec sets `signInAtSpotify(user)` before each connect and the specs run one
after the other. The live-activation steps for the owner are `docs/runbooks/m4-live-activation.md`.

Song picks run end to end in the e2e stack. The fake MOWL wraps any Spotify playlist classbeat attaches and,
on every read, lists what the fake Spotify's playlist holds now (MOWL follows edits, ADR 0008 section 2): the
fake Spotify computes that list (`FakeSpotify.playlist_track_ids` and `mowl_entries` in `spotifycore.py`, also
used by the in-process `FakeMowlPool`, so the two fakes cannot drift) and serves it on its test control
(`GET /__e2e/mowl-tracks?playlist=<id>`); the fake MOWL asks that from the compose network with the Spotify
fake's certificate unchecked (the shared CA bundle is written after the MOWL fake starts; the call is
test-only and internal). The seeded fake Spotify gives every catalogue track four minutes
(`/__e2e/durations?ms=`), which the recorded design is written for. The pick model is `e2e_pick.json` (search by
artist name, `the invented band` then `placeholder collective`, then the 14-candidate pool and the 8-song
list) and the change model `e2e_change.json` (slot 3 replaced); a spec points the worker's script link at
one before each phase (`useModelScript`), and back at `good_first_draft` (or `revise_good` for a change)
before the approval, because the second phase is a new run of the model. `e2e/tests/song-helpers.ts` has
`readyToPick`, `startPick`, `untilState`, `fakePlaylists` and `sql` (a statement as the database superuser in
the stack's db container, for what a test cannot wait for, such as an approval past its date: constants only).

The specs: `design-draft.spec.ts` (draft, cancel while running, uncertain attach),
`spotify-connect.spec.ts` (the Spotify wizard, consent, Disconnect, two users on different Client IDs and
every error category's page text), `song-pick.spec.ts` (a pick and one Approve to a private playlist of exactly
the approved songs, a stale tab after a newer pick, an expired list, an account switch while waiting, a partial
write and Create the playlist again, a lost create answer reconciled, an undecided write, Change songs and its
resume, two users on different Client IDs picking at once, a working playlist that is not the user's own),
`class-save.spec.ts` (change, save, update, uncertain writes, another MOWL account),
`m5-quota.spec.ts` and `m5-admin.spec.ts` (M5 done-when: parallel creates from several browser contexts of one session, the
plan limit, refunds, an admin with TOTP granting a plan and refunding a credit, a redeem code; helpers in `m5-helpers.ts`,
and `nextTotp` in `helpers.ts`; the session shows an admin as a user until two-factor is on, so enrolment is on
`/account#security`),
`class-usage.spec.ts` and `fake-spotify.spec.ts` (web connects through the fake with PKCE, the worker
reaches it too and reads a 120-track playlist back in order over three pages, A3 at the playlist-read
level since a pick's final list is one page, it publishes no port).

Running is on in the e2e stack only (`CLASSBEAT_RUNNING_ENABLED: "true"` on web and worker in `compose.e2e.yaml`; the
defaults keep it off). `running-class.spec.ts` (M8 F1, ADR 0018, #466) runs the form with Running chosen (the bike-only
knobs and song choices are gone, Cycling brings them back), a design with the `running_first_draft` script (the Running
chip, the Incline column instead of Cadence and Position, five zones, no Change songs), Save (the fake MOWL stores
`ActivityTypeID` 1, segments 0, zones 1 to 5, a `Gradient` of 0 or 1 per interval, private, on the playlist), a
`running_revise` change and Update of the same program, the `program_other_sport` refusal (the fake's
`save-sport?program=<id>&sport=0|1` flips the stored program's sport; no Update anyway), and the admin flag
`running_enabled` stored `off` (an `app_settings` row through `sql`) hiding the control. `mu-pages.spec.ts` also audits
the New class form with Running chosen and a ready running class (26 pages).

The MU done-when specs (ADR 0013 sections 9 and 10, `mu-helpers.ts`): `mu-pages.spec.ts` walks one signed-in
browser through 24 pages (landing, sign in/up, reset, Home, My classes, New class, Designing held mid-run, ready,
Class, the five Account tabs, Music, Spotify, the six admin sections) and audits each at 390 x 844 and 1440 x 900
in the dark and the light theme: no sideways scroll, the primary action visible and inside the window, no serious
or critical `@axe-core/playwright` violation (WCAG 2.2 A/AA tags), and no console error. The admin is made with
`admin grant`; turning on two-factor on the account page with a code computed from the shown key (`totpCode`)
passes the admin's TOTP check for the session. `mu-covers.spec.ts` serves Songs for approval from API answers the
test makes (the stack's fakes run no song pick) over the real CSP header, and the cover host from a page route.
`playlist-covers.spec.ts` (#537) connects Spotify, gives the fake Spotify the fake MOWL's playlist ids with mosaic,
uploaded, album, foreign-host and query images, and checks the New class tiles (three covers, initials for the rest,
one `GET /v1/me/playlists` as the user, axe and both viewports and themes), and that without Spotify no covers are asked.

The fakes' state is shared, so Playwright runs one worker. The Playwright context blocks every
origin but the stack's. They use public test values only and are never part of the default stack.

The e2e stack trusts the docker network as proxy so each test presents its own `X-Forwarded-For`
address (separate rate limits). Sign-up is limited to 5 per IP and hour: use `down -v` between full
runs of a stack that is not given per-test addresses.

CI runs the same in the `e2e` job (when the stack changed) and uploads `playwright-report` only on failure.

### CI: what runs, and when

CI is expensive (every job bills at least a minute; a full run took 11 to 17 minutes before the
backend tests were sharded, see #307 for the timings now), so a pull request only runs what its
change needs. `ci.yml` runs on `pull_request`, on every `push` to `main`, weekly (Monday 04:00
UTC, which picks up new advisories) and on `workflow_dispatch`. It also still has a `merge_group`
trigger, unused because the repository has no merge queue.

The **push to `main`** (every squash merge) runs everything (`changes` sets `all=true` for any event
that is not a pull request or merge group, as for the weekly and manual runs): the ruleset does not
require a PR to be up to date, so this full run on the merged result is what catches a semantic
conflict between PRs that were each green alone. Its concurrency group is per event and ref with
`cancel-in-progress` true, so in a burst of merges the older runs are cancelled and the latest
one, which contains them all, completes. A newer push also cancels a pull request's older run; a
merge group's run is never cancelled. The secret scan of a push covers the pushed range
(`before..sha`) and falls back to the whole history when `before` is zero or unknown.
A red run on `main` is fixed forward first.

The `pr-title` workflow also runs on `merge_group`. A merge group has no PR title, so there it runs
commitlint on the subject of every commit the queue would land (`git log --format=%s
base_sha..head_sha`, the squash commits, each titled with its PR's title as the queue built it):
a title edited after the PR was enqueued is checked too. A missing SHA or an empty range fails it.
On the pull request it lints the title with ` (#<number>)` appended, the subject the squash gets,
so a title that fits the 100-character `header-max-length` only without the suffix fails there
and not in every merge group (keep titles to about 90 characters). Both events use
`scripts/commitlint-subject.sh`; a self-test step shows a 100-character title failing with the
suffix.

The backend test shard count has one source: the `shard` list of the `backend-tests` matrix.
`scripts/ci-shards.sh` (run in `changes`, tested in `checks`) reads it, refuses another axis, an
`include` or `exclude`, or anything but `1..N`, and passes N to the shard check and to every shard
(never `strategy.job-total`).

| Job | Runs | What it does |
|---|---|---|
| `changes` | always | `scripts/ci-changes.sh`: `git diff` of the PR's base and head (or the merge group's `base_sha` and `head_sha`) gives the outputs `backend` (`backend/`, `docker/postgres/`), `frontend` (`frontend/`), `stack` (`backend/`, `frontend/`, `e2e/`, `docker/`, `Dockerfile`, `compose*.yaml`, `.dockerignore`, `.env.example`, `.trivyignore`, `scripts/worktree.sh`) and `all`. Renames count at both paths; names are read NUL-separated; if the diff cannot be computed (an unknown commit, a missing SHA) everything runs. `all` is true on schedule/dispatch and when `.github/`, `.gitattributes` or `scripts/ci-changes.sh`, `ci-gate.sh`, `ci-shards.sh` or `test-ci-gate.sh` change (CI changes test themselves) and makes the others true |
| `checks` | always | the cheap checks as steps of one job (below) |
| `backend` | `backend` | once per run: ruff, format, pyright, the queue functions' md5 regenerated on the pinned image (fails when `queue_digests.py` is stale), and the shard check: `backend/tests/shards.py check N` compares pytest's own collection with the split, so every test file runs in exactly one shard and none is empty |
| `backend-tests` | `backend` | pytest in a matrix of 3 shards (`fail-fast: false`), each its own runner and Postgres service; a file's shard is `sha256(path) % N + 1` (`tests/shards.py list K N`), so the split needs no list and no dependency. Change the shard count only in the matrix (`shard: [1, 2, 3]`); `changes` reads it from there (`scripts/ci-shards.sh`) and passes it to the check and every shard |
| `frontend` | `frontend` | lint, typecheck, Vitest, build |
| `e2e` | `stack` | the stack, the checks below, the Trivy image scan, then Playwright |
| `ci` | always | the required check; see the gate below |

A docs-only PR therefore runs `changes`, `checks` and `ci` (plus `pr-title`).

**The `ci` gate** (`scripts/ci-gate.sh`, tested by `scripts/test-ci-gate.sh` in `checks`) fails
unless `changes` and `checks` succeeded and every other job either succeeded or was skipped while
its filter output is `false`. A job that is needed (filter `true`) but skipped, cancelled or failed
fails the gate, and so does a job missing from `needs`, a short result list or malformed input (it fails closed). The
`backend-tests` matrix has one result in `needs` (success only when every shard succeeded), gated
by the `backend` filter: a failed or cancelled shard, or the matrix skipped while `backend` is
true, fails the gate. Add a new job to `needs` of `ci`, to `ci-gate.sh` (and its filter) and to
the tests.

### Security checks (CI)

The `checks` job runs these as steps; the image scan is a step of `e2e`:

| Step | What it checks |
|---|---|
| Secret scan | gitleaks over the pull request's own commits (a merge group's: `base_sha..head_sha`); the weekly and manual runs scan the whole history |
| bash -n, shellcheck | every `scripts/*.sh` |
| CI gate and change-filter tests | `scripts/test-ci-gate.sh` |
| Postgres image pinned alike | compose's `db` and `db-functions` and the `backend` and `backend-tests` jobs' services use the same exact `postgres` tag, which is also the `FROM` of `docker/postgres/Dockerfile` (the queue functions' md5 depends on it); a further step builds that image and diffs its SQL with the repository's; `scripts/test-install-check.sh` builds the release assets and runs the released `check-prod-compose.sh` from an install folder |
| uv version, actionlint | `setup-uv`'s version equals the Dockerfile's uv tag (`scripts/ci-uv-version.sh`); actionlint (with shellcheck) on every workflow |
| pip-audit | `uv export --frozen` (production, then all groups) through `pip-audit` (pinned) |
| npm audit | `npm audit --audit-level=high` in `frontend/` and `e2e/` |
| Trivy repository scan | Trivy on the repository (vulnerabilities, Dockerfile misconfiguration; compose is not scanned) |
| `e2e`: stack checks | builds the stack once: `/api/ready` and the SPA answer, `web` and `worker` run as non-root, no secret value appears in `docker inspect` (the empty model key is replaced by a random canary first), only `worker` mounts the three model keys (`anthropic_api_key`, `openai_api_key`, `deepseek_api_key`, a random canary each) and runs `fake`, no container has a model key variable, `postgres_password` is mounted on `db` and `db-functions` only (never web, worker, migrate or maint), and `database_maint_password` on `db-functions` and `web` only |
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
