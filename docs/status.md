# Status

Updated on every merge. The roadmap is [roadmap.md](roadmap.md); the milestones and issues on
GitHub hold the detail.

## Milestones

| # | Milestone | State |
|---|---|---|
| M0 | Repo, protocol, CI, skeleton, compose, worktrees | in progress |
| F | Provider-permission questions (ADR 0010) | not started |
| M1 | Accounts | not started |
| M2 | `mowl-workouts` v0.4.0, isolation, encryption, connect MOWL | not started |
| M3a | Jobs, Anthropic loop, design on existing music | not started |
| M3b | Class page, revisions, save to MOWL, usage and cost | not started |
| M4 | Song picking (beta), Spotify BYOK | not started |
| M3c | Eval suite, OpenAI and DeepSeek adapters | not started |
| M5 | Tiers, quotas, codes, admin | not started |
| M6 | Security review, backups, GDPR, VPS | not started |
| M7 | Commercial (deferred) | deferred |

## Now

- M0: bootstrap commit pushed; ruleset, milestones and M0 issues next.

## Waiting for the owner

Nothing yet.

## Live activation log

No live runs. Every live run against a real MOWL, Spotify or Swedebeat account is listed here
with the owner's authorization and what it created.

## Exceptions

- The first commit on `main` was pushed directly, before the ruleset existed (bootstrap).
  It holds only the protocol, CI gate and docs. Everything after it goes through PRs.
