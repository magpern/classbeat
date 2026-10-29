# Status

Updated on every merge. The roadmap is [roadmap.md](roadmap.md); the milestones and issues on
GitHub hold the detail.

## Milestones

| # | Milestone | State |
|---|---|---|
| M0 | Repo, protocol, CI, skeleton, compose, worktrees | done (milestone closed 2026-10-28) |
| F | Provider-permission questions (ADR 0010) | closed, not pursued (owner's risk acceptance, 2026-10-28) |
| M1 | Accounts | done (milestone closed 2026-10-28) |
| M2 | `mowl-workouts` v0.4.0, isolation, encryption, connect MOWL | done, except the token-lifetime check (waits for the owner) |
| M3a | Jobs, Anthropic loop, design on existing music | next |
| M3b | Class page, revisions, save to MOWL, usage and cost | not started |
| M4 | Song picking (beta), Spotify BYOK | not started |
| M3c | Eval suite, OpenAI and DeepSeek adapters | not started |
| M5 | Tiers, quotas, codes, admin | not started |
| M6 | Security review, backups, GDPR, VPS | not started |
| M7 | Commercial (deferred) | deferred |

## Now

- **M0 done** (milestone closed 2026-10-28). Its "done when" was checked on 2026-10-28:
  - The skeleton runs: the compose stack (db, migrate, web, worker, mailpit) comes up healthy,
    `/api/ready` answers and the Playwright smoke test passes, locally and in CI.
  - The autonomous PR loop works: every merged PR since the bootstrap went PR → CI →
    fresh-Opus `PASS` on its head SHA → `--match-head-commit` squash merge, including
    FAIL → fix → new review cycles (#30 took four) and re-reviews after a branch update.
  - Two worktrees run stacks side by side: the main checkout (port 8000) and a worktree
    (port 20090) ran at once with their own projects and volumes.
- **M1 done** (milestone closed 2026-10-28). Its "done when", checked on `main` at bb14e9f:
  - All sign-in paths: email and password, Google, GitHub and Microsoft, each tested against an
    in-process fake provider; Google and Microsoft also end to end through the fake OIDC
    service (`compose.e2e.yaml`, profile `e2e`).
  - Safe linking: identities keyed by provider and subject, never email; unverified provider
    emails go through the hand-off; linking needs the user's own session and recent auth.
  - Admin MFA: TOTP, recovery codes, step-up, the 12 h admin window.
  - Auth tests green in CI (backend, Vitest and Playwright).
- **M1, what merged:**
  - Design: [ADR 0003](adr/0003-accounts-and-sessions.md) (#43, four reviews; #45). Threat
    model T33 updated, T34 to T39 new, plus accepted risks.
  - #46: account tables, server-side sessions, CSRF, rate limits, CLI, guard 8.
  - #48: TOTP MFA, recovery codes, step-up, `GET /api/admin/whoami`.
  - #49 delivers #37: email and password sign-up, sign-in, reset, email change, deletion, SMTP.
  - #53: Google, GitHub and Microsoft sign-in, safe linking, OAuth re-authentication.
  - #54, #57: the frontend pages, the API client (CSRF; after a 401 only safe requests are
    repeated) and the E2E flows.
  - #55: follow-ups (JWKS cache and refetch, `azp`, Google `auth_time` request, guard 8 as a
    superset of its first version with explicit exemptions, MFA and session fixes).
- **M2 done technically, except the token-lifetime fact.** Its "done when" (roadmap: concurrent
  two-user isolation test green; ciphertext only; no tokens in logs; MOWL token lifetime known)
  holds for the first three. **The MOWL token lifetime is not known**; the owner-authorized
  check is under "Waiting for the owner". It does not block M3a.
- **M2, what merged:**
  - Design: [ADR 0004](adr/0004-connections-and-encryption.md) (#69). #70: ADR 0010 and
    roadmap Amendment 1 (beta on the owner's risk acceptance). #68: CI runs only what a change
    needs, no CI on push to `main`.
  - `mowl-workouts` v0.4.0 (#64, closed; delivered in the `mowl-workouts` repo).
  - #74 (closes #65): connections table, envelope encryption, log redaction.
  - #76 (closes #66): connect MOWL, backend and page.
  - #78 (closes #67): concurrent two-user isolation test with fake upstreams.
  - #72 (closes #58): guard 8 marker as a comment token, count per string.
- **Follow-ups** (open, non-blocking review findings):
  - #75: hardening from #74 (`connection_keks` privileges, deferred KEK check, dump test hex,
    `keys rewrap` exit code). Items 3 and 4 should be done before the beta is used by testers.
  - #77: hardening and test gaps from #76.
  - #79: app-layer owner filter and isolation test follow-ups from #78.
  - #73: guard 8 f-string marker edge cases.
  - #71: design of the purge of Spotify-derived data (roadmap Amendment 1, §6); due before M3b/M4.
- **Next: M3a** (jobs, Anthropic loop, design on existing music).
- Releases: v0.1.0 (cut partway through M0); v0.2.0 is not cut yet (deferred to save Actions quota). Image
  `ghcr.io/magpern/classbeat`, private.

## Waiting for the owner

- Nothing to decide now. (2026-10-28: the providers are not contacted; the beta runs on the
  owner's risk acceptance, [ADR 0010](adr/0010-live-activation-permissions.md).)
- **MOWL connect (#66) has merged, so the owner can now authorize: a check of the MOWL token's lifetime**
  against the owner's own account (M2 "done when"; [ADR 0004](adr/0004-connections-and-encryption.md) §6).
  What is known (#66): the token is a 36-character GUID, not a JWT, so it carries no expiry
  claim; MOWL sends no lifetime with it, and `mowl-workouts` (v0.4.0, `docs/mowl-api.md`) knows
  none either. **The lifetime is unknown.** classbeat keeps `expires_at` `NULL` and flips the
  connection to `needs_reconnect` on the first 401 or 403 (a reconnect prompt on the account
  page). The proposed check, read-only: the owner connects their own MOWL account on a local
  stack; once a day an agent runs one read-only call (`list_workouts`) with the stored token
  and records the date of the first 401 or 403 (or that none came within 30 days); no write to
  the account, nothing else signed in. Agents run it only with the owner's go-ahead for that run,
  logged under "Live activation log".

## Live activation log

No live runs. Every live run against a real MOWL, Spotify or Swedebeat account is listed here
with the owner's authorization and what it created.

- 2026-10-28: unauthorized live read. During a test preview, the flywheel-analysis session made
  one live MOWL playlist-name search (read-only, no writes) without the owner's prior go-ahead.
  Nothing was created or changed. Agents never contact providers without authorization (AGENTS.md
  safety envelope); the owner has been told.
- 2026-10-29: unauthorized provider contact. While developing the live-command tests of the smoke
  set (PR #124), one test run executed the real live path without a guard and connected to
  `api.anthropic.com:443`: about fifteen requests (one per smoke case, as many failures logged).
  Each carried its request body (the stage's system prompt, the synthetic brief and synthetic
  music) and an `x-api-key` header holding a placeholder key that was never a real key. No
  user data, no real account; no answer beyond an error (authentication failed). Nothing was
  created. A later mutation check of the new network guard also opened a few TCP connections to
  documentation addresses and `93.184.216.34` (example.com) with no payload. The owner has been
  told. Guards added: the whole backend test suite now refuses non-loopback sockets
  (`backend/tests/netguard.py`), and that test file's provider stub fails without an in-process
  fake.

## Exceptions

- The first commit on `main` was pushed directly, before the ruleset existed (bootstrap).
  It holds only the protocol, CI gate and docs. Everything after it goes through PRs.
