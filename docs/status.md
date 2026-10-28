# Status

Updated on every merge. The roadmap is [roadmap.md](roadmap.md); the milestones and issues on
GitHub hold the detail.

## Milestones

| # | Milestone | State |
|---|---|---|
| M0 | Repo, protocol, CI, skeleton, compose, worktrees | done when #32 merges ("done when" verified 2026-10-28) |
| F | Provider-permission questions (ADR 0010) | questions ready; waiting for the owner to send them |
| M1 | Accounts | next |
| M2 | `mowl-workouts` v0.4.0, isolation, encryption, connect MOWL | not started |
| M3a | Jobs, Anthropic loop, design on existing music | not started |
| M3b | Class page, revisions, save to MOWL, usage and cost | not started |
| M4 | Song picking (beta), Spotify BYOK | not started |
| M3c | Eval suite, OpenAI and DeepSeek adapters | not started |
| M5 | Tiers, quotas, codes, admin | not started |
| M6 | Security review, backups, GDPR, VPS | not started |
| M7 | Commercial (deferred) | deferred |

## Now

- M0 "done when", checked on 2026-10-28:
  - The skeleton runs: the compose stack (db, migrate, web, worker, mailpit) comes up healthy,
    `/api/ready` answers and the Playwright smoke test passes, locally and in CI.
  - The autonomous PR loop works: every PR since the bootstrap went PR → CI → fresh-Opus
    `PASS` on its head SHA → `--match-head-commit` squash merge, including FAIL → fix →
    new review cycles (#30 took four) and re-reviews after a branch update.
  - Two worktrees run stacks side by side: the main checkout (port 8000) and a worktree
    (port 20090) ran at once with their own projects and volumes.
- Released v0.1.0 (image `ghcr.io/magpern/classbeat`, private).
- Open in M0: #32, isolation follow-ups from the review of #31 (non-blocking; each fails
  closed today).
- Next: M1 (accounts), split into issues per [roadmap.md](roadmap.md) §3.

## Waiting for the owner

- **Send the provider questions** in [providers/questions.md](providers/questions.md) to Spotify
  developer support and to MOWL, and say when they were sent. Development doesn't wait on the
  answers; live activation does ([ADR 0010](adr/0010-live-activation-permissions.md)).
- **Note (product):** until Spotify answers S2 and S3, live runs can't send Spotify-derived
  music (including Spotify playlists imported into MOWL) to Claude. Live beta use with Claude
  therefore waits for Spotify, unless you choose otherwise.

## Live activation log

No live runs. Every live run against a real MOWL, Spotify or Swedebeat account is listed here
with the owner's authorization and what it created.

## Exceptions

- The first commit on `main` was pushed directly, before the ruleset existed (bootstrap).
  It holds only the protocol, CI gate and docs. Everything after it goes through PRs.
