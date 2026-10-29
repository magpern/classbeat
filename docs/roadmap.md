# classbeat roadmap: a class designer for Intelligent Cycling (MOWL) as a web app

## Amendment 1 (2026-10-28, owner decisions)

Revision 6 below stays as written; where it differs, this amendment wins. Recorded in
[ADR 0010](adr/0010-live-activation-permissions.md) and issue #61.

- **Providers not contacted; beta on the owner's risk acceptance.** The beta circle is the
  owner and friends the owner invites (invite-only, free, non-commercial). For it:
  - **A5** ("permitted by the terms of every provider") is replaced by the owner's acceptance
    of the risks listed in ADR 0010. A1 to A4 are unchanged. A5 applies again to anything
    beyond the circle and to M7.
  - **Verification, live activation gate:** condition (2) reads ADR 0010 as amended, which
    allows every feature except Swedebeat for the circle once technically complete.
    Condition (1), the owner's authorization of each agent-run live test, is unchanged, and
    the owner switches features on for the testers.
  - **§5 model data:** for the circle, Spotify-derived track metadata and MOWL's analysis may
    be sent to the model; tokens, emails and account data never are.
  - **§10, track F, milestone F:** the questions are not sent; F is closed as not pursued.
    The questions are kept for a later public or paid phase.
  - **§6 retention:** a user's own Spotify-derived track lists and timing are deleted with
    their class or account; a shared music analysis is deleted once no class of any user
    refers to it (a periodic purge, M3b). Nothing Spotify-derived is kept indefinitely (ADR 0010).
  - **Superseded elsewhere:** the Revision 6 preamble's "live Spotify/AI use gated on the
    permission assessment" and "Before M0 (owner)"'s "put the track F questions to Spotify
    and MOWL" no longer apply to the circle.
- **Developers by risk.** Security-critical PRs (encryption and keys, jobs with leases and
  fencing, Spotify OAuth, approvals and external writes) get an Opus Developer; the rest a
  Sonnet Developer ("Development process", Roles). The Reviewer is a fresh context either
  way; its model is set by Amendment 2.

## Amendment 2 (2026-10-29, owner decision: review cost)

Where it differs, this amendment wins over Revision 6's review gate ("Development process").

- **Merge-only updates** need no new review: when the only change since a reviewed head is a
  merge of `main` with no hand-resolved conflict, and CI is green, the Architect's mechanical
  merge check carries the verdict. A scoped fresh review is needed if `main` changed a module
  the PR changes or calls, or a conflict was resolved by hand.
- **Fix rounds** after a `FAIL` get a fresh **delta review**: the diff since the last reviewed
  head, each earlier finding verified, and whatever the delta touches.
- **Reviewers rely on CI** for the full suite; they run targeted tests and mutation checks of
  the guarantees that changed.
- **Follow-up findings are batched** into few hardening PRs, one review each.
- **Reviewer model by risk:** security-critical PRs (the Opus-Developer list in Amendment 1,
  plus auth and sessions, tenant isolation, guards and roles, the queue SQL, egress, model
  keys and billing) keep a fresh Opus Reviewer; other PRs (UI, tests, docs, tooling) may have
  a fresh Sonnet Reviewer. Whether a change touches a security-critical area, or a module a PR
  changes or calls, is the Architect's call; when in doubt, Opus and a review.

*Revision 6 — frozen for implementation. Song picking is a **core** feature with Claude
choosing the final songs from real BPM and sections (the pool stage from `mowl-workouts`);
the instructor approves once, then the class is created hands-off. **Spotify is
bring-your-own Client ID (BYOK):** each user connects their own Spotify developer app, as
`mowl-workouts` does today. BYOK is a technical design, **not** a confirmed compliant model
for a hosted service (Spotify's terms prohibit multiple Client IDs for one application): it
stays a track F risk and broad public or paid use waits for permission. Also: live
Spotify/AI use gated on the permission assessment, Spotify `/items` API migration, managed
playlists by ID, approval-wait semantics, technical completion separate from live
activation, explicit playlist privacy. Agents start M0 now; no transfer services, no
redesign of the playlist workflow.*

## Context

`mowl-workouts` (v0.3.2, private) works as an MCP server, CLI and Claude Code plugin: an
instructor asks Claude for a class, Claude picks songs, times intervals to the music and saves
the class to their Intelligent Cycling (MOWL) account. It needs Claude Code on the user's own
computer, which non-technical instructors won't install.

Goal: a website that does the same for many users: sign up (email, Google, GitHub,
Microsoft), connect MOWL (required) and Spotify, optionally Swedebeat; describe a class with
guided options; Claude picks the songs and designs the class; see it drawn, revise it by
chatting, save it to MOWL. Paid tiers and Paddle payments are prepared but stay off until
agreements exist. Docker (app + separate Postgres), first Docker Desktop, later a VPS.
Developed **autonomously** by agents in a new private repo under `magpern`; the owner handles
product decisions and genuine external approvals, not code review or routine merges.

Phases, kept apart:
1. **Beta (now):** invite-only, free, non-commercial: owner + a few testers, each with their
   own MOWL account and their own Spotify developer app (BYOK, Development Mode).
2. **Commercial (later):** public paid SaaS. Needs a verified song-picking route at scale
   (track F) and agreements with MOWL (and Swedebeat for its features). M7 is gated on both.

## Product acceptance criteria for song picking (mandatory)

| # | Criterion |
|---|---|
| A1 | The instructor approves the final song list **once**; the playlist is created and the class design continues without leaving the app or copying tracks |
| A2 | Claude chooses the final songs and their order from **real** BPM, length and sections, using a larger candidate pool (as `mowl-workouts` does) |
| A3 | Created playlists are private; after creation the backend verifies the exact tracks and order |
| A4 | Works for every user the phase serves (beta: every tester with their own Spotify app; commercial: all subscribers, once permitted) |
| A5 | Permitted by the terms of every provider involved, for that phase |

## Architecture

```
browser ── Caddy (TLS, VPS only) ── web (FastAPI + built React app) ── Postgres
                                        │  jobs + job_events tables
                                     worker(s) (agent runs, music analysis)
```

- Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Pydantic v2. React + TypeScript + Vite SPA
  served by `web`. Containers: `web`, `worker`, `db` (postgres:17), `mailpit` (dev); prod
  adds `caddy`. No Redis, no Kubernetes, no microservices.
- The `mowl` package from `mowl-workouts` is the domain library (spec, validation, TSS,
  librosa analysis, beat sync, MOWL/Swedebeat/Spotify clients), pinned to a release wheel,
  kept backwards-compatible for the CLI/MCP/plugin.

## 1. Tenant isolation (P0) — contract in M0, implemented in M2

- **`UserContext`** (request- or job-scoped, immutable): authenticated `user_id`, role,
  entitlements, connection references. Built only from the session (web) or the job row
  (worker). Every service function takes it explicitly.
- **Every row owned:** `owner_id` on classes, revisions, jobs, job events, credentials,
  conversations, saved workouts, MOWL playlist links, usage. Repositories always filter by
  `ctx.user_id`; IDs from the browser or a model are looked up *within* the owner's rows.
- **Postgres Row-Level Security** as defence in depth: non-owner app role with RLS forced;
  `SET LOCAL app.user_id = …` per transaction; migrations as a separate owner role;
  admin/maintenance paths on a distinct, audited role.
- **`mowl-workouts` v0.4.0:** no module-level credential state or shared authenticated
  clients; a `Credentials` object (MOWL token, Swedebeat token + device id, Spotify **Client
  ID** + access/refresh token with a refresh callback) is passed in; file-based loading
  (`~/.mowl/spotify_app.json`, `spotify_token.json`) stays only in the CLI/MCP entry points. Gaps:
  `api.load_token()` callers, `swedebeat.load_token()`, `spotify_web` token/client-id files,
  `connect.*`, `library` cache paths. **Split the music map** (today `library.spotify_map()`
  stores the analysis and the user's MOWL `playlist_id` together): shared *music analysis*
  vs per-user *MOWL playlist link* (§6). **ID-based playlist writes:** today
  `spotify_web.save_playlist()` finds a playlist *by name* and replaces it; add
  `create_playlist(...) -> id` (always a new resource) and `replace_playlist_items(id, ...)`
  (only an exact ID), keeping the name-based helper for the CLI only.
  **Authority:** agents may branch, open PRs, merge (same review protocol) and cut releases
  in `magpern/mowl-workouts`, keeping its `CLAUDE.md` rules and the plugin/CLI working.
- **Test:** two users' jobs concurrently with different fake credentials and fake upstreams
  recording which token each call carried; nothing crosses users (calls, rows, cache,
  events, playlist links). Owned endpoints return 404 for another user's IDs; tool handlers
  reject a model-supplied foreign ID.

## 2. Writes and approvals (P0)

| Category | Tools / actions | Who runs it |
|---|---|---|
| Read | search songs, list/read playlists and mixes, read zones, read existing workout | Model |
| Compute | preview/validate spec, TSS, sync, analyse an attached source | Model |
| Managed working write | replace the user's single private **pool playlist** with Claude's candidates, attach/refresh it in MOWL | Backend, under the user's standing consent given when connecting Spotify ("we manage one private working playlist for song picking"); logged, revocable |
| User write | create the class's final playlist, attach it in MOWL, save or update the MOWL workout | Backend, only after the user's approval |

Model output is a proposal; approvals are the authorization; every write is revalidated
server-side against the authenticated user and the approved revision.

**Managed playlists by ID, never by name.** The user's pool playlist ID is stored per user
when first created; later writes touch only that exact ID, after checking the current user
owns it (Spotify `owner.id` = the connected account). Final class playlists are always new
resources, never a name match. Each create/replace is an `external_operations` row, so a
partial failure (created but not filled, filled but not attached in MOWL) is visible and
reconciled.

**Privacy.** All playlists are created with explicit `public: false` and
`collaborative: false`; MOWL workouts private. Playlist URLs are never shown to or shared
with other users. The privacy wording says what Spotify's setting means: a non-public
playlist doesn't appear on the profile or in search, but anyone with its link can open it.

**Song-picking flow (meets A1–A3):**
1. Claude drafts a **candidate pool** (~3× the songs needed, each with a role) from the
   brief, resolving tracks with search (read).
2. Backend writes them to the user's managed pool playlist and has MOWL analyse it → real BPM,
   length, sections per track (as `mowl-workouts` does today).
3. Claude chooses and orders the final songs from that data → **song list for approval**
   (titles, roles, BPM, length, total time).
4. User presses **Approve songs** (the one approval) → backend creates the final private
   playlist, verifies tracks and order, attaches and analyses it in MOWL, and the job
   continues by itself.
5. Claude designs the intervals on the analysed music → **draft revision** shown.
6. Revisions by chat; a revision that changes songs repeats 3–4 for the changed songs.
   **Save to MOWL** saves a chosen revision.

Music the user already has (a playlist in MOWL or a Swedebeat mix) skips 1–4.

## 3. Accounts and identity (P0)

- Identity key = `(provider, provider_subject)`, never email. A first OAuth login whose email
  matches an existing user must sign in to that account first and confirm linking; providers
  without a verified email likewise.
- Authlib with state, nonce (OIDC), PKCE; single-use callbacks; login-CSRF protection.
  Google, GitHub, Microsoft; Apple and passkeys later.
- Email + password (argon2id), verification, reset. Server-side sessions, HttpOnly Secure
  SameSite=Lax cookie, rotation on login, idle + absolute expiry, CSRF tokens, rate limits.
- **Recent authentication** (within 10 min) to link/unlink identities, change email/password,
  connect/disconnect MOWL/Spotify/Swedebeat, delete the account.
- **Admins:** TOTP MFA mandatory; step-up MFA for sensitive actions. First admin from
  `ADMIN_EMAILS` or `docker compose exec web app admin grant <email>`.
- **MOWL connect:** email/password on our page → token → password discarded. Swedebeat the
  same, one device id per user.
- **Spotify connect (BYOK)**, a wizard like today's local one (`connect.py`
  `_spotify_form` / `_save_spotify_app`):
  1. The user opens the Spotify Developer Dashboard and creates their own app.
  2. We show our exact, fixed callback URL with a copy button
     (`https://<host>/oauth/spotify/callback`; locally `http://127.0.0.1:<port>/…`, the
     loopback form Spotify accepts).
  3. The user registers it in their app's settings.
  4. The user pastes the Client ID (validated as 32 hex characters). The callback is shown
     for confirmation only; it is never taken from user input.
  5. **Connect Spotify** runs OAuth 2.0 Authorization Code with PKCE against that Client ID.
  6. We verify the connection (`GET /me`), then store the Client ID and the encrypted tokens
     against the user. **No Client Secret is ever requested or stored.**
  The wizard says plainly: the app's owner needs Spotify Premium; Development Mode apps have
  an allow-list, endpoint and quota limits.
- **Spotify OAuth rules:** state and PKCE verifier stored server-side, bound to the
  authenticated user + Client ID + redirect URI, single-use, short-lived; the callback
  rejects any mismatch, so a callback can't connect a different account or app. Refresh
  handles rotation (a new refresh token replaces the old one atomically). Changing the Client
  ID deletes the old connection's tokens and forces a reconnect. Explicit **Disconnect**
  revokes locally and deletes the tokens. Errors are told apart and shown in plain words:
  expired or revoked authorization (`invalid_grant` → reconnect), account not on the app's
  allow-list, other 403s, rate limits (429 with `Retry-After` → backoff), quota exhaustion.

## 4. Jobs (P1)

- States `queued → running → waiting_for_approval → running → completed | failed | cancelled
  | expired`.
- **Waiting for approval:** the job releases its worker and concurrency slot; the exact
  proposed song list is persisted as a `song_list` revision with a content hash; the state
  survives restarts. **Approve songs** carries the revision ID + hash and is accepted only if
  it is the latest list for that class, the hash matches, the user is authorized (session,
  owner), the Spotify and MOWL connections are valid and quota allows; a superseded or
  expired list is refused and never starts playlist creation. Unapproved lists expire after
  7 days (configurable) with no further model or API calls; the reserved credit is released.
- `SELECT … FOR UPDATE SKIP LOCKED`; lease with heartbeat; reaper requeues expired leases;
  **fencing token** (`lease_version`) on every write so a stale worker can't commit.
- Attempts with exponential backoff for retryable errors; immediate fail otherwise.
- **Idempotency:** browser idempotency keys; external writes through `external_operations`
  (key, kind, request hash, `pending | succeeded | failed | uncertain`, external ID). An
  `uncertain` write is reconciled first; if a duplicate can't be ruled out it is **not
  retried automatically** — shown to the user and in admin.
- Cancellation between model turns and tool calls. Progress in `job_events` (per-job `seq`),
  SSE with `Last-Event-ID`, `LISTEN/NOTIFY` as a wake-up, polling as fallback.
- Class credit **reserved** at start, **committed** on success, **refunded** on system failure
  or early cancellation; per-user concurrent-job limit; per-job cost ceiling.

## 5. Agent contract (P1)

- Provider-neutral loop over a `ModelProvider` interface; thin adapters: `AnthropicProvider`
  (official `anthropic` SDK, tool use, `cache_control` on stable system prompt and tools),
  `OpenAIProvider` (official `openai` SDK), DeepSeek via the OpenAI adapter. **Anthropic
  first; others in M3c.** API keys only; no server-side CLI, no shell or file tools.
- Stages, each with an allowlist: `pick_pool` (search), `choose_songs` (read the analysed
  pool; ends with `submit_song_list`), `design` (read + compute on the attached music; ends
  with `submit_draft`). No stage gets write tools.
- `WorkoutDraft` and `SongList` are strict Pydantic schemas; tool arguments validated;
  `validate()` results returned for correction; final server-side validation before storing
  and before any save.
- Hard limits per run (turns, tool calls, time, cost). Revisions start from the current
  accepted draft. Provenance per revision (provider, model, effort, prompt/tool-schema/library
  and music-map versions).
- Provider/model/effort per tier in admin, editable price table; default `claude-opus-5-5`,
  effort `medium`. DeepSeek disabled until privacy, terms and eval are met. Tokens never go to
  a model; Spotify-derived data sent to a model is limited to what track F confirms allowed.

## 6. Classes, revisions, music

- `class → revision 1, 2, 3 …` immutable (spec, music version, provenance). `saved_workouts`
  keeps the MOWL program ID + revision last saved; **Save** creates a new private MOWL
  workout; **Update saved** re-saves that program after reading it.
- **Music analysis** (shareable): immutable versions keyed by source + fingerprint (Spotify
  `snapshot_id` + track list; Swedebeat mix ID + indexed duration), analysis version
  (`music.MAP_VERSION`), created time. Reused across users only after an access check with the
  requester's credentials, and kept only as long as the providers' terms allow (track F).
- **MOWL playlist link** (per user, never shared): the user's MOWL `PlaylistID` for a source.
- **Beat-one corrections** per user on top of an analysis. The class page shows timing
  warnings and a per-song correction control.

## 7. Security by milestone (P1)

| Milestone | Security work |
|---|---|
| M0 | Threat model, `UserContext`/RLS contract, secrets handling, pip-audit/npm audit/Trivy/secret scanning, agent review protocol, agent safety envelope |
| M1 | Auth per §3, rate limits, CSRF, sessions, admin MFA, authorization tests |
| M2 | Isolation per §1, envelope encryption (AES-256-GCM, AAD `user_id\|provider`, wrapped data keys, `key_id` rotation), log-redaction tests |
| M3 | Agent limits/allowlists, fencing, worker outbound allowlist (MOWL, Spotify, Swedebeat, model APIs), budgets, cancellation |
| M5 | Atomic quota reservations, admin audit log |
| M6 | Independent security review, backup restore drill, GDPR export/delete verified |

## 8. Product

| Page | What it does |
|---|---|
| Landing | What it does, example class, sign up (invite code) |
| Onboarding | Connect MOWL; Spotify setup wizard (own app, copy callback, paste Client ID, connect; Premium and Development Mode notes; pool-playlist consent in one sentence); Swedebeat optional |
| New class | Guided builder: length, music (**let Claude pick** / my playlist / my mix), style or era, intensity, climbs, sprints, races, FTP test, free text |
| Designing… | Live progress (SSE, resumable): picking candidates, analysing, choosing |
| Songs for approval | Final songs with role, BPM, length, total time; **Approve songs** (one press) or ask for changes |
| Class | Timeline chart (own palette), segments with songs, duration, TSS, warnings, beat-one corrections; **Change it** chat; revisions; **Save to MOWL** / **Update saved** |
| My classes | Classes with revisions and save state |
| Account | Connections (incl. revoking pool consent), plan and usage, redeem code, export, delete |
| Admin | See below |

Own name, colours, fonts and icons; no MOWL logo/owl or copied assets.

## 9. Tiers and billing (prepared, off)

Metered in **classes**; tokens and cost tracked internally. Song picking is part of the
product; paid tiers that include it wait for a commercial route that meets A1–A5 (before M7).

| Tier | Price (draft) | Limits |
|---|---|---|
| Free | €0 | 3 classes/month, 3 revisions each |
| Instructor | ~€9/mo, ~€90/yr | 30 classes/month, 10 revisions each, races/games/FTP tests, history, Swedebeat features when enabled |
| Pro | ~€19/mo, ~€190/yr | 100 classes/month, priority queue, best model, multi-week series |
| Club (later) | custom | Seats, shared library, Swedebeat-only mode |

Entitlements = subscription **or** admin grant **or** code **or** trial. **Paddle** (merchant
of record) in M7 behind `BILLING_ENABLED=false`. Prices follow measured cost per class.

## 10. External dependencies and track F (feasibility)

- **Spotify (BYOK):** each user's own app runs in Development Mode (allow-list, Premium for
  the app owner, endpoint and quota limits). **Risk kept open:** Spotify's Developer Terms
  prohibit using multiple Client IDs for a single software application, so BYOK for a
  hosted service is not confirmed compliant, and it does **not** resolve the commercial user
  limit. Separately, the Developer Policy prohibits ingesting Spotify Content (including
  metadata and playlists) into AI models and restricts apps aimed at business use (it names
  dance studios); getting the data via MOWL is no exemption. → Live Spotify + AI use waits
  for the permission assessment below; no broad public or paid BYOK use without permission
  for this deployment model.
- **Spotify API version:** the Feb 2026 Web API changes replace `GET /playlists/{id}/tracks`
  with `GET /playlists/{id}/items` (different schema and pagination). `spotify_web` still
  reads `/tracks` (`find_playlist_tracks`); M4 migrates every call to the current endpoints
  with tests on exact track IDs and order.
- **MOWL:** reverse-engineered API; the beta uses each tester's own MOWL account; wider or
  paid use needs MOWL's authorization.
- **Swedebeat:** behind a disabled flag with offline fixtures; server-side audio analysis only
  after Swedebeat authorizes it.

**Track F — provider-permission assessment** (Architect + Explorer, in parallel with M0–M2;
desk research only, no live calls, no alternative integrations explored or built):
1. Write up the questions for the owner to put to the providers: Spotify — is a hosted site
   where each user connects their own Client ID (BYOK) permitted given the
   multiple-Client-ID rule; is sending track metadata/analysis to an AI model to choose songs
   permitted; does the business-use clause apply to instructors; retention limits for
   Spotify-derived data. MOWL — use of its API by a multi-user beta site.
2. Record in `docs/adr/0010-live-activation-permissions.md` what each answer permits; the
   live activation gate (below) reads from it.
3. The commercial song-picking route (Spotify scaling, partnerships) is **out of scope now**;
   it's revisited before M7.

## Admin area

Users (connection status only, never tokens: MOWL, Spotify with whether a Client ID is set
and the last Spotify error category, Swedebeat; tier, usage, grants, disable, delete), codes, usage and cost, jobs (incl. waiting approvals and
uncertain external operations), settings and flags (signups, song picking, Swedebeat,
billing, provider/model/effort per tier, price table, quotas, banner), audit log. Admin role in
`users.role`; MFA + step-up per §3.

## Development process (autonomous)

New private repo `magpern/classbeat` (name chosen by the owner; must not suggest it's made by
MOWL). `AGENTS.md` holds the protocol, adapted from an earlier project's agent protocol;
`CLAUDE.md` = `@AGENTS.md` + the merge rules up front + the security rules from
`mowl-workouts` (including: no `Co-Authored-By` trailer).

**Roles** (by model class):

| Role | Class | Owns | Must not |
|---|---|---|---|
| Architect / Orchestrator | Opus | Drives the project: next milestone, PR split with acceptance criteria, dependency graph, scheduling, merge order, ADRs (`docs/adr/NNNN-title.md`), track F | Normally write production code |
| Developer | Sonnet | One PR in its own branch and worktree: code, tests, CI fixes, all reviewer findings | Change architecture or acceptance criteria |
| Explorer | Haiku | Bounded work: searches, evidence, fixtures, docs, extra tests | Own security, auth, isolation, crypto or billing decisions |
| Reviewer | a **fresh** Opus context per review, no shared context | Diff, criteria, tests, CI output, ADR compliance → `PASS` or `FAIL` + findings | Fix its own findings |

**Orchestration:** continues without routine human input. Progress lives in GitHub milestones
and issues (one issue per PR, with dependencies) plus `docs/status.md`, updated on every merge,
so work resumes after a context reset. It doesn't stop for a `FAIL` or at a milestone's end.
It escalates only genuine blockers: external permissions or agreements, credentials it can't
provision safely, product decisions, live-account tests needing authorization.

**Safety envelope (unattended operations):**
- Allowed autonomously: branches, PRs, reviews, merges per the gate, releases and images to
  GHCR, local Docker Desktop stacks, read-only checks needed by track F.
- Owner-authorized only: deploying to the VPS, DNS/TLS, paid services and subscriptions,
  provider dashboards (Spotify app settings and allow-list, Paddle, Google/GitHub OAuth apps),
  emails to real people, live-account test runs, anything that writes to a real MOWL,
  Spotify or Swedebeat account, and contacting providers.

**PR loop:** issue → Developer (+ Explorer) in its worktree → PR → CI → fresh Opus Reviewer →
`PASS` → agent merges; `FAIL` → fix all blocking findings → CI → new fresh Reviewer. Same
blocking finding three cycles running → Orchestrator re-assesses.

**Review gate — mandatory for agents, soft for humans and GitHub:**
1. Every agent-merged PR needs a fresh-Opus `PASS` for its **current head commit**, recorded
   as `Agent review (Opus): PASS|FAIL <head SHA>` with non-blocking findings. A fresh Sonnet
   may add a supplementary light review; it never replaces the Opus `PASS`.
2. Any head-SHA change needs a new full review. No carry-over.
3. Not a `PASS`: the implementing session's own check, a review skill in the same context,
   green CI, or "it's fine" in chat. Human authorization doesn't replace it for agent merges.
4. Agents merge only with
   `gh pr merge <n> --squash --match-head-commit <reviewed-sha> --subject "<type>(<scope>): <description>"`;
   never auto-merge, `--admin`, weakened branch rules or approving reviews.
5. A human may merge any PR on GitHub regardless of the verdict.

**GitHub settings (GitHub Pro):** ruleset on `main`: PR required, `ci` passing, branch up to
date, no direct pushes, squash only, **zero required approvals**. `CODEOWNERS` = `* @magpern`
(informational).

**Parallel work in worktrees:** every task is a feature branch + PR; each parallel developer
has its own `git worktree` (`../<repo>-wt/<issue>-<slug>`), one writing agent per branch;
each worktree runs its own stack (`COMPOSE_PROJECT_NAME=<repo>-<issue>`, own Postgres volume
and ports from a generated `.env`, `scripts/worktree.sh new <issue>`). The Orchestrator orders
merges; dependent branches are updated and re-reviewed; worktrees removed after merge.

**Conventional Commits 1.0.0** for commits, PR titles and squash subjects
(`feat(auth): …`, `fix(worker): …`, `refactor(mowl): …`, `test(security): …`, `ci: …`,
`docs: …`; `!` or `BREAKING CHANGE:` for breaking changes), checked by commitlint in CI.

**CI:** ruff, pyright, pytest with Postgres, isolation/RLS tests, eslint, tsc, vitest,
Playwright E2E against the compose stack with fake MOWL/Spotify/Swedebeat/model APIs,
pip-audit, npm audit, Trivy, secret scanning, commitlint. **Release** on tag `v*`: images
(amd64 + arm64) to `ghcr.io/magpern/<name>`, GitHub Release with notes from the commits.

## Milestones

| # | Deliverable | Done when |
|---|---|---|
| M0 | Repo, `AGENTS.md`/`CLAUDE.md`, agents, safety envelope, `docs/status.md`, worktree script, threat model, `UserContext`/RLS contract, CI, ruleset + CODEOWNERS, compose, FastAPI + React skeleton, Alembic | Skeleton runs; one PR goes autonomously PR → CI → fresh-Opus `PASS` → `--match-head-commit` merge; two worktrees run stacks side by side |
| F | Track F (parallel to M0–M2): provider-permission questions, ADR 0010 | Questions handed to the owner; ADR records the answers as they arrive (feeds the live activation gate, doesn't block development) |
| M1 | Accounts per §3 | All sign-in paths; safe linking; admin MFA; auth tests green |
| M2 | `mowl-workouts` v0.4.0 (credentials object, no global state, analysis vs playlist-link split, pluggable store) released; isolation per §1; encryption; connect MOWL | Concurrent two-user isolation test green; ciphertext only; no tokens in logs; MOWL token lifetime known |
| M3a | Jobs per §4 (incl. `waiting_for_approval`); Anthropic loop with `design` stage on existing music; smoke regression set | Crash/lease/stale-worker tests green; E2E with fakes: draft from a MOWL playlist |
| M3b | Class page, revisions, Save/Update to MOWL via external operations, usage + cost | E2E with fakes: design → revise → save → update; uncertain-write path; token usage and cost recorded per job (real cost per class measured at live activation) |
| M4 | **Song picking (beta):** BYOK Spotify wizard and OAuth per §3 (adapting `spotify_web.py`, not a new client), pool consent, `pick_pool`/`choose_songs` stages, managed pool playlist by ID, approval semantics per §4, final playlist created new + verified → design continues; song changes in revisions; Spotify API migration to `/items` | E2E with fake Spotify/MOWL meets A1–A3 incl. superseded/expired approvals, ownership check, partial-failure reconcile and every error category; concurrent users with **different Client IDs**, tokens, music and playlists never mix; a callback can't connect the wrong account; one user's playlist can never be overwritten by another; exact IDs/order verified against the current API shape |
| M3c | Full eval suite (§11) incl. song-choice quality; OpenAI adapter; DeepSeek adapter (disabled) | Models enabled per tier only after passing |
| M5 | Tiers, quota reservation/refund, concurrency limits, codes, grants, trial; admin + audit | Limits hold under concurrency; refunds work |
| M6 | Security review, backups + restore drill, GDPR export/delete, Caddy + VPS (owner-authorized deploy) | Beta running on the VPS for the testers |
| M7 | *(deferred: needs a commercial song-picking route, researched before M7, + provider agreements)* Paddle, public signup, legal pages | A1–A5 met for all subscribers; agreements signed |

## 11. Model evaluation

Smoke set (~15 cases) from M3a on every PR touching prompts/agent; versioned regression suite
before enabling a model: standard classes (duration, zones, structure, TSS), **song choice**
(pool covers the roles, final picks match BPM/role/energy, total length), music sync (beat 1,
drops, breakdowns), revisions (change made, constraints kept), games and FTP rules, missing
music, adversarial tool output, cost/efficiency. Fake music data; results per model + prompt
version. Zone 7's TSS weight carries an "approximate" warning.

## Verification

- Every agent-merged PR: CI green + recorded fresh-Opus `PASS` on the head SHA + merged with
  `--match-head-commit`.
- **Technical completion:** a milestone is done when its "done when" passes with unit,
  integration and Playwright E2E tests against fakes (synthetic music data). Agents never
  wait on live accounts to close a milestone.
- **Live activation gate** (separate, owner-controlled): live MOWL/Spotify use with real
  model calls is switched on per feature only when (1) the owner authorizes the specific live
  test run, and (2) ADR 0010 shows the needed provider permissions (for Spotify + AI song
  picking: Spotify's answer). Live runs create only private resources and are listed in
  `docs/status.md`. Until then the beta runs on existing MOWL playlists/mixes, or stays off.
- M6: no tokens in logs, ciphertext-only DB dump, restore into a fresh stack, 403 on admin
  routes for non-admins, 404 on cross-user IDs.

## Before M0 (owner)

Enable GitHub Pro; choose the product/repo name. Later, at live activation: put the track F
questions to Spotify and MOWL; testers create their own Spotify developer apps (the app owner
needs Premium); provide an Anthropic API key as a Docker secret.
