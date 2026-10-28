# Status

Updated on every merge. The roadmap is [roadmap.md](roadmap.md); the milestones and issues on
GitHub hold the detail.

## Milestones

| # | Milestone | State |
|---|---|---|
| M0 | Repo, protocol, CI, skeleton, compose, worktrees | done (milestone closed 2026-10-28) |
| F | Provider-permission questions (ADR 0010) | closed, not pursued (owner's risk acceptance, 2026-10-28) |
| M1 | Accounts | done (milestone closed 2026-10-28) |
| M2 | `mowl-workouts` v0.4.0, isolation, encryption, connect MOWL | next |
| M3a | Jobs, Anthropic loop, design on existing music | not started |
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
- **Next: M2** (isolation and MOWL), split into issues per [roadmap.md](roadmap.md) §1 and M2.
  Open in M2 so far: #58 (guard 8 marker and count) and #59 (this update).
- Releases: v0.1.0 (cut partway through M0); v0.2.0 follows this update (end of M1). Image
  `ghcr.io/magpern/classbeat`, private.

## Waiting for the owner

- Nothing to decide now. (2026-10-28: the providers are not contacted; the beta runs on the
  owner's risk acceptance, [ADR 0010](adr/0010-live-activation-permissions.md).)
- **After MOWL connect (#66) merges: an owner-authorized check of the MOWL token's lifetime**
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

## Exceptions

- The first commit on `main` was pushed directly, before the ruleset existed (bootstrap).
  It holds only the protocol, CI gate and docs. Everything after it goes through PRs.
