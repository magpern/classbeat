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
| M3a | Jobs, Anthropic loop, design on existing music | done (2026-10-29); live smoke run once: 14 of 15 passed |
| M3b | Class page, revisions, save to MOWL, usage and cost | done technically (2026-10-30, on fakes): [ADR 0007](adr/0007-classes-revisions-and-saving.md), issues #141 to #151 |
| M4 | Song picking (beta), Spotify BYOK | in progress: design accepted 2026-10-31 ([ADR 0008](adr/0008-song-picking.md), [ADR 0009](adr/0009-spotify-connection.md), #198); issues in milestone "M4 Song picking and Spotify": #173 to #185 closed, #186 open until its part 2 merges; not marked done technically |
| M3c | Eval suite, OpenAI and DeepSeek adapters | done technically (on replayed providers, #219): [ADR 0012](adr/0012-eval-suite-and-model-providers.md); the owner's live steps are in [docs/runbooks/m3c-model-enable.md](runbooks/m3c-model-enable.md) |
| M5 | Tiers, quotas, codes, admin | done technically, on fakes, once this E2E PR (#228) is merged (the security suite #227 is in) ([ADR 0011](adr/0011-tiers-quotas-and-admin.md)); issues in milestone "M5 Tiers and admin". Live activation is the owner's |
| MU | Night Studio UI (ADR 0013) | design accepted 2026-10-30 ([ADR 0013](adr/0013-ui-design-night-studio.md), roadmap Amendment 8); issues #242, #243, #246 to #251, #253 to #255; runs beside M4 and M5, done before M6 |
| M6 | Security review, backups, GDPR, VPS | not started; starts after MU |
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
- **M3a done technically** (2026-10-29, on fakes). Its "done when": crash, lease and
  stale-worker tests green (J2 #103); E2E with fakes, a draft from a MOWL playlist, plus cancel
  and an uncertain attach (T1 #129). What merged: ADR 0005/0006 (#82); E1 egress allowlist
  (#96); mowl-workouts 0.5.0 wheel (#106, closes #105 with A1/D1/S1); J1 queue (#99) and its
  hardening (#104); J2 jobs core (#103); J3 jobs API (#111); J4 events and SSE (#112); X1
  external operations (#113) and the uncertain row following its job (#126); A1 agent core
  (#110); A2 AnthropicProvider (#118, `anthropic==0.125.0`); D1 `design_draft` (#119); U1 web
  pages (#123); S1 smoke set and the owner-run live command (#124); review gate scaled by risk
  (#128, roadmap Amendment 2). The live smoke run (2026-10-29, owner-authorized): 14 of 15
  passed, $2.59 in total; `long_60` failed its TSS check (#131).
- **M3a follow-ups.** Merged in hardening batches: #132 (#120, #121), #138 (#108, #109, #115,
  #116), #136 (model comparison, #134); live smoke findings #131 closed. Still open: #107 and #98
  (flaky auth tests), #114 (A1), #117 (X1, items 2 and 7), #122 (D1, item 4), #127 (U1), #135,
  #140 (from #138), #153 (from #136); #137 (UI and tests) and #139 (connections and egress) are in
  review.
- **M3b done technically** (2026-10-30, on fakes). Its "done when" (roadmap): an E2E run with
  fakes of design, revise, save and update; the uncertain-write path; token usage and cost
  recorded per job (the real cost per class is measured at live activation). What merged:
  [ADR 0007](adr/0007-classes-revisions-and-saving.md) (#152, closes #71) and roadmap
  Amendment 3 (the periodic purge moves to the first milestone that stores a shared analysis);
  C1 schema (#156); K1 job-kind seams (#158); C2 class API (#157); US1 usage (#159); X2
  operations seams (#160); R1 `revise_draft` (#161); SV1 Save (#162); FE1 class page (#163);
  SV2 Update (#164); FE2 Save/Update screens (#166) with the class API fields they needed
  (#167, #168); T2 E2E (#169). Process: cheaper reviews (#165, roadmap Amendment 4); the repo
  became public on 2026-10-29 (no secrets in it; fork-PR workflows need approval).
- **M3b follow-ups** (open, non-blocking review findings, to be batched into a hardening PR):
  #155 plus the findings of the M3b reviews: #157 (orphans shim now removed by #160; clamp
  `bound_seconds` to 0 to 1800, class list paging at 500, `POST /current` CSRF test), #158
  (second-class test for the pointer update, brief byte limit message in the form, approve
  uses the job's credit state), #159 (`jobs` counts a deleted job's runs, price-version test,
  saves and updates missing from the monthly view), #160 and #162 to #169 (see their review
  comments). The ADR 0007 amendments (brief limit, usage month before the account, counting
  rules, the class API's saved summary) are written in the ADR's "Amendments after
  implementation".
- **M4 in progress** (song picking, Spotify BYOK): ADR 0008 (song picking) and ADR 0009 (the Spotify
  connection) were accepted on 2026-10-31 (#198 item 6, with their dated amendments), and roadmap
  Amendment 5 is in effect. Of their 14 issues, #173 to #185 are closed and #186 stays open until its part 2 merges. Nothing live is run; the
  owner-authorized live steps are in ADR 0008 §11. The superseded-by notes that ADR 0009 owed are written:
  ADR 0004 §4, ADR 0005 §3 and §4's category table, and ADR 0007 §9 (the guard is `generation`,
  compared together with `id`; for Spotify only the flips of ADR 0009 §6). The M4 review follow-ups
  (#198) are done on `main`; item 19 (A3's paging, unreachable with 40 songs) is covered at the
  playlist-read level in `e2e/tests/fake-spotify.spec.ts`. None of #198's items needs a live step.
- **M4 T3 (#186), part 1:** the e2e stack turns song picking on (test-only); `spotify-connect.spec.ts`
  covers the wizard (callback copy, a Client ID that is not 32 hex characters, an unknown Client ID, cancel,
  allow-list refusal, a changed state), the working-playlist consent, Disconnect, two users on different
  Client IDs, a confirmed account switch and every error category's page text. The live-activation
  checklist is [docs/runbooks/m4-live-activation.md](runbooks/m4-live-activation.md) (ADR 0008 §11; agents
  run no step without the owner's go-ahead for that step). Part 2 (pick, approval, superseded and expired
  lists, partial failure and resume, Change songs) closes #186; M4 is not technically done until it
  merges and its "done when" is checked against the roadmap row.
- **M3c design** (eval suite, providers, the enable gate): ADR 0012 is Proposed, roadmap Amendment 6,
  threat model T79 to T84. Owner questions with defaults are in ADR 0012 (OpenAI data, DeepSeek kept
  disabled, stale routes, thresholds, a full-suite run for Opus, provider keys); agents build to the
  defaults. Nothing live is run; G2 and Z7 wait for M4's #182 to #184.
- **M5 design accepted, development started:** [ADR 0011](adr/0011-tiers-quotas-and-admin.md) (tiers, quota reservation and
  refund, concurrency limits and cost caps, codes, grants, trial, the `classbeat_maint` admin path with
  its audit log, flags), roadmap Amendment 7, threat model T85 to T96. Split into 15 issues in milestone
  "M5 Tiers and admin"; QT1, FL1, FE1 and T1 wait for
  their M4 dependencies. The owner's questions (limits, testers' plan, trial, refunds and more) are in
  ADR 0011 §11; development proceeds on its defaults.
- **M5 done technically** (#228, on fakes). Merged: tiers, plans and entitlements (migration 0007, #233), plan, redeem
  and trial API (#252), the `classbeat_maint` admin path with its audit log (#257), the metered quota ledger with plan
  limits and the cost cap (#294), the admin API for users, codes, usage, jobs and the refund (#277, #279, #281),
  hardening (#276), database flags (#304), the settings and plan-limit API (#314), the account and admin pages (#266,
  #289, #305, #317), model routes per plan (#321) and the security suites (#268, #303, #315). The done-when "limits hold
  under concurrency and refunds work" is tested end to end in `e2e/tests/m5-quota.spec.ts` (parallel creates from several
  contexts start exactly the plan's limit, the month then refuses every create with 409 `quota`, two users never mix;
  a failed and a cancelled run give the credit back) and `m5-admin.spec.ts` (an admin with TOTP grants Instructor, the
  limit rises at once, the admin refunds a used credit, both are in the audit log, a redeem code raises a second user's
  limit), and in the backend through the API in `test_m5_done_when.py` (parallel creates at the limit, 409 and 429
  bodies, a cancel and an admin refund settle once, a plan limit changed by the admin applies to the next reservation).
  An expired song-list approval releasing its credit is tested in the backend only (`test_quota_jobs.py`,
  `test_m5_security_concurrency.py`): E2E would need a Spotify-connected run to a waiting approval, and the approval
  lifetime has a one-hour floor. M5 follow-ups for the owner: the Q1 to Q10 answers (limits and prices change seeds
  or settings only), live activation (admin account with TOTP via `admin grant`, the testers' plan, the `song_picking`
  grant, the first live run of each model route) and enabling OpenAI or DeepSeek models ([m3c-model-enable.md](runbooks/m3c-model-enable.md)); none of these
  is done by agents.
- **MU Night Studio UI** (owner decisions 2026-10-30, roadmap Amendment 8). ADR 0013 was merged
  in #237 and accepted in #256 (closes #241), which also added the "amended by ADR 0013 §9" notes to:
  - ADR 0001 Frontend;
  - ADR 0008 §5 and §8;
  - ADR 0009 §10, M-1 and M-5;
  - threat-model T73.

  The issues and the order they open up in:
  - #242 L2: the `mowl-workouts` 0.7.0 library release, by the local Architect.
  - #243 UI1: the foundation, startable now.
  - #246 UI2: the shell.
  - #254 UI9: the landing and sign-in pages, after UI2.
  - #247 UI3, #248 UI4, #249 UI5, #250 UI6, #251 UI7, #253 UI8: also wait for the M4, M3c and M5
    issues named in them (#181 to #184, #217, #224).
  - #255 UI10: the done-when checks.

  Mockups of every main screen are on the owner's design canvas. The issues are in GitHub
  milestone "MU Night Studio UI" (#12).
- Releases: v0.1.0 (cut partway through M0); v0.2.0 is not cut yet (deferred to save Actions quota). Image
  `ghcr.io/magpern/classbeat`, private.

## Waiting for the owner

- **M5 product questions** ([ADR 0011](adr/0011-tiers-quotas-and-admin.md) §11, Q1 to Q10): limits and
  cost caps per tier, which plan the testers get, trial on or off, quota period, refund on cancel,
  overlapping codes, Free feature gates, the Pro priority queue, audit-log retention, prices. Agents
  build on the defaults listed there; an answer changes seeds or settings, not the design.
- Each further live smoke run needs the owner's go-ahead for that run (2026-10-29: "do not run
  live test every time").
- **M3c live steps** (runbook [m3c-model-enable.md](runbooks/m3c-model-enable.md)): create and place the OpenAI (and later
  DeepSeek) key; give a go-ahead and a `--max-usd` for each first live run per provider (an adapter check, no `--record`);
  review the fixture update from it; then a recorded run per model and group and `models enable`. Until a pass is recorded,
  the bootstrap Anthropic model serves users. OpenAI and DeepSeek routes stay impossible until you flip `data_policy`
  (questions 1 and 2).
- **M3c owner questions** ([ADR 0012](adr/0012-eval-suite-and-model-providers.md), "Owner questions"):
  do you extend your Spotify §III 14 risk acceptance (Claude only, ADR 0010) to OpenAI (default no:
  OpenAI stays `owner_pending`, evaluated on synthetic data only); keep DeepSeek
  disabled (default yes); stale routes keep working, flagged (default yes); the suite thresholds; a
  full-suite run before Opus 5.5 is used by a tier (default yes); you place the OpenAI and DeepSeek keys
  yourself (default yes). Each live suite run needs your go-ahead for that run.
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

Every live run against a real MOWL, Spotify or Swedebeat account is listed here
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
- 2026-10-29: owner-authorized live smoke run, once ("do not run live test every time, just
  now"). `python -m classbeat.evals smoke --live --max-usd 10` in the owner's local worker,
  the key from the worker's `anthropic_api_key` secret (added by the owner, never seen by
  agents), `claude-opus-5-5` at effort medium, synthetic briefs and music only, no user account.
  Prices checked against Anthropic's list first ($4 in, $20 out, $0.20 cache read per million
  tokens). 15 cases, 14 passed, $2.59 in total; `long_60` failed its TSS check (#131). Nothing
  was created anywhere. It ran before #121 and #120 merged; the worker's environment was
  checked first and had no `SSLKEYLOGFILE`, `ANTHROPIC_*` or `CI` variable.

## Exceptions

- The first commit on `main` was pushed directly, before the ruleset existed (bootstrap).
  It holds only the protocol, CI gate and docs. Everything after it goes through PRs.
