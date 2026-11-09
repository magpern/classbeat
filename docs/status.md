# Status

Updated on every merge. The roadmap is [roadmap.md](roadmap.md); the milestones and issues on
GitHub hold the detail.

See also: ideas that are not planned, in [ideas.md](ideas.md).

## Milestones

| # | Milestone | State |
|---|---|---|
| M0 | Repo, protocol, CI, skeleton, compose, worktrees | done (milestone closed 2026-10-28) |
| F | Provider-permission questions (ADR 0010) | closed, not pursued (owner's risk acceptance, 2026-10-28) |
| M1 | Accounts | done (milestone closed 2026-10-28) |
| M2 | `mowl-workouts` v0.4.0, isolation, encryption, connect MOWL | done, except the token-lifetime check (waits for the owner) |
| M3a | Jobs, Anthropic loop, design on existing music | done (2026-10-29); live smoke run once: 14 of 15 passed |
| M3b | Class page, revisions, save to MOWL, usage and cost | done technically (2026-10-30, on fakes): [ADR 0007](adr/0007-classes-revisions-and-saving.md), issues #141 to #151 |
| M4 | Song picking (beta), Spotify BYOK | done technically (2026-10-31, on fakes; design accepted 2026-10-31: [ADR 0008](adr/0008-song-picking.md), [ADR 0009](adr/0009-spotify-connection.md), #198); issues in milestone "M4 Song picking and Spotify" (#173 to #186). Live activation is the owner's, one authorised step at a time: [docs/runbooks/m4-live-activation.md](runbooks/m4-live-activation.md); none has been run |
| M3c | Eval suite, OpenAI and DeepSeek adapters | done technically (on replayed providers, #219): [ADR 0012](adr/0012-eval-suite-and-model-providers.md); the owner's live steps are in [docs/runbooks/m3c-model-enable.md](runbooks/m3c-model-enable.md) |
| M5 | Tiers, quotas, codes, admin | done technically (2026-10-31, on fakes; #228 merged) ([ADR 0011](adr/0011-tiers-quotas-and-admin.md)). Live activation is the owner's |
| MU | Night Studio UI (ADR 0013) | done technically (2026-11-01, on fakes): [ADR 0013](adr/0013-ui-design-night-studio.md), UI10 (#255). The song-strip comparison with analysed music is still open and is not part of that done-when |
| M6 | Security review, backups, GDPR, VPS | done technically (2026-11-01, on fakes; #340 closed). Matrices #339, GDPR #348 and #349, backups and the restore drill #338, #355 and #364, body cap #351, egress #353, playlist rate limit #354. Caddy and `compose.prod.yaml` are ready and not deployed (#341). SR-02 (#352) is an owner step. The VPS schedule, off-host copy and real backup keys are not done |
| M7 | Commercial (deferred) | deferred |
| M8 | Running classes (ADR 0018 Accepted, owner approval 2026-11-02; roadmap Amendment 12) | Discovery (read-only 2026-11-02, #468) and the live save test (2026-11-03, ADR 0018 Amendment 2) answered what a running save sends: program `ActivityTypeID: 1`, segments 0, running interval bodies, no listing change. L1a (library read side, mowl-workouts#16) and L1b (save, #469) are released as mowl-workouts v0.8.0 (the owner removed `show-me.yml`, mowl-workouts#18); running update and an existing playlist (#482) and the sport-mismatch refusal on update (#485) as v0.9.0, which classbeat vendors without the Swedebeat key (#462). R1, U1, E1, F1 (#463 to #466) follow. Running with a new mix is unverified and stays refused. P9 running profiles are merged (#486: two `moderate` interval cells, ADR 0017 §7); wiring them needs R1's sport on the brief |
| S1 | Swedebeat connection (owner decision 2026-10-31) | connection done on fakes (#362, [ADR 0015](adr/0015-swedebeat-connection.md)). Mixes are in through A3 (#377, #378, #379). A4 (#380, #430) is merged: a class can be designed and saved on the user's own Swedebeat mix, on fakes. Catalogue matching (#383, #441) is merged: a song list is matched on the fake, and only the title and artist leave. Replacing unmatched songs and approving the selection (#385, #444) is merged. The Swedebeat app step (#384, #447) is merged: the hand-off starts from the approved match of the class's current song list. The mix screen (#381, #455) is merged: New class can choose a Swedebeat mix, and a failed mix list stays visible when Spotify lists no playlists. **Technically done on fakes (2026-11-03):** the match read (#458, #467), the match screen and the Swedebeat app step (#386, #471), and the done-when suites (#382 in #459, #387 in #477, which replaced #473) are merged; tracker #363 is closed. An axe audit of the match view runs in the e2e suite. The #442 and #445 follow-ups are merged (#449, #450). A failed catalogue match no longer says it will try again (#452, #453). The job page shows the server's sentence for a failed match (#476). **Live use is an owner step**: nothing has been sent to Swedebeat, the app key is not in this repository, and `MOWL_MEDIA_HOSTS` holds only the confirmed MOWL media host, `users-playlists.s3-accelerate.amazonaws.com` (#524); any other host needs the owner's live check ([runbook](runbooks/swedebeat-connect.md)) |
| ME | Account emails (owner approval 2026-11-05; [ADR 0022](adr/0022-account-emails.md); roadmap Amendment 15; plan [account-emails.md](plans/account-emails.md); umbrella #649) | done technically (2026-11-07, on fakes): ME0 plan and ADR (#658), ME1 branded layout and registry (#660), ME2 admin branding and switches (#677), ME3 new account notices (#680), ME4 email preferences and unsubscribe (#681), ME5 "class ready" via a worker hand-off (#684), e2e specs for the admin mail settings and for unsubscribe (`mail-admin.spec.ts`, `mail-unsubscribe.spec.ts`, #649); ADR 0022 Accepted. Sending real mail waits for the owner's SMTP relay ("Waiting for the owner", Mail for the app) |
| MH | Help and contact (#650) | done technically (2026-11-07, on fakes): public `/help` page in en, sv, da with FAQ and the support address from `GET /api/settings/public` (#722). No form, tickets or chat |
| ML | Multilingual support (owner answers 2026-11-06; [ADR 0023](adr/0023-multilingual.md); roadmap Amendment 16; plan [multilingual.md](plans/multilingual.md); umbrella #657) | done technically (2026-11-07, on fakes; ML1 to ML8 merged). English, Swedish and Danish; translations by a Sonnet translator reviewed by an independent Opus Reviewer in dedicated translation PRs (Claude Code only; staffing per owner decision 2026-11-06), with no human review; generated classes stay English; admin English. Runtime, language preference, string guard and job and Spotify sentences as codes (ML1 to ML4, #692 for ML4); string extraction (ML5a to ML5d, #691, #694, #696, #697, #700, #701); sv/da account mail (ML6, #704, #707); sv/da app catalogues (ML7, #706, #710); the done-when e2e suite (ML8, #673, #709): `e2e/tests/locale-done-when.spec.ts` checks the roadmap ML done-when on the compose stack: the sign-up language kept on `users.locale` and used for the confirmation and reset mail (da), the account language following to a fresh browser, "Follow this browser", a Swedish page around English class text from the recorded model, and `CLASSBEAT_LOCALES=en` offering English only; `locale-sv`/`locale-da` cover the main flow, axe and 390 px (ML7); mail goldens, the catalogue guards and completeness are backend/frontend tests. A further UI redesign is an owner idea, not a planned milestone. Nothing waits on the owner for ML, except that real Swedish and Danish mail delivery depends on the SMTP relay of "Waiting for the owner", Mail for the app |
| MX | UI refresh (owner decisions 2026-11-09; [ADR 0028](adr/0028-ui-refresh.md); roadmap Amendment 17) | In progress: one PR per screen, order in ADR 0028 §5 |
| MP | Passkeys (owner answers 2026-11-05; [ADR 0019](adr/0019-passkeys.md); issue #311) | done technically (2026-11-08, on fakes): W1 to W8 merged (settings and RP ID, schema, register, sign-in, re-auth and manage, reset and `mfa-reset`, cross-device cap, docs and runbook #779). Off by default; compose passes the two settings since #790; switching on in production or test is the owner's ([runbook](runbooks/passkeys.md)) |
| MT | Public pages and sign-in hardening (2026-11-07; [ADR 0025](adr/0025-turnstile.md), issue #721) | Merged 2026-11-07: security activity list and other-session sign-out event (#716, `GET /api/auth/events`, [ADR 0003](adr/0003-accounts-and-sessions.md) amendment); cookie notice (#717); privacy and terms pages in en, sv, da (#720; the controller, address, registration number and hosting are marked placeholders for the owner); help page (#722, MH); ADR 0025 (#723, Proposed, amended 2026-11-07); Turnstile verification on the backend (#725, ced5c81: verifier, fake, per-IP captcha bucket, settings and secret file, CSP, threat T126). Turnstile widget hardening from the #731 review (#732, merged as #736). **In flight:** hardening batch for the review notes of #725, #731, #736, #738 and #740 (#741): `queue_stats()` range-checks `wait_ms`, the drill worker applies the netguard, the swap note for the memory ceilings, a session-fetch error sentence on the Turnstile forms, the Turnstile guard names its read-only GETs, ADR 0025 owner steps. Not done there: a token the user already had is still dropped when the window crosses 300 px (the widget renders again), and the zlib block in `docker/postgres/Dockerfile` stays until a base-image bump. The widget and the e2e switches (#726, PR 2, #731) and the docs for Turnstile (#727, PR 3; it does **not** flip the production default, owner decision 2026-11-07) are separate PRs. **Default on in production (2026-11-08, owner request "Do 721", ADR 0025 Amendment 2):** `compose.prod.yaml` now defaults `TURNSTILE_ENABLED` to true and `check-prod-compose.sh` requires it; a server upgrading to that release without the widget, site key and secret file must set `TURNSTILE_ENABLED=false` first (runbook section 11). The owner still does the Cloudflare steps and every production deploy. Docs amendment: #728 |

## Now

- **Admin edit for the plan save caps (#809, part of #632, 2026-11-08).** The Settings page's plan forms set and clear `plans.saves_per_hour` (1 to 1000) and `plans.saves_per_day` (1 to 10 000) through `PATCH /api/admin/plans/{id}` (the table's checks, 422 `out_of_range`; `null` clears to the setting; audited as `plan.update`); `classbeat_maint` gets `SELECT` and `UPDATE` on the two columns (`30-maint.sql`, guard 11). The plan route moved from `admin/settings.py` to `admin/plans.py` and the refusal-row limiter from `admin/audit.py` to `admin/refusal_limiter.py` (code-structure rule 2). Also the #808 review's N1 to N3: `saves_per_day = 0` is checked, the plan cap on the Update path, for a user on another plan and on the next request after a change, and the `quota/bounds.py` docstring.
- **Watch: database pool size (from the #626 review, non-blocking 2).** Every signed-in `/api` request now runs the general limit's upsert and the security-log write in transactions of their own on the web's pool (SQLAlchemy's default 5 plus 10 overflow, `classbeat.db.engine`), besides the route's own. Fine at the beta's scale; under load, watch pool waits (`QueuePool limit ... reached` or a slow `/api` with an idle database) and size the pool, or batch the limiter's work, before more users. `rate_limits` growth is covered by the hourly purge (ADR 0003).
- **Passkeys docs, runbook and Permissions-Policy check (#779, W8 of ADR 0019, 2026-11-08).** Closing it finishes #311. Developer commands describe the settings, routes, flag-off behaviour, the reset `info` `passkeys` and `complete` `keep_passkeys` fields and the settings check running in every role; the threat model has T113 to T120 (and passkey notes in T4); ADR 0003 carries the pointers of ADR 0019 section 12; ADR 0014, 0019 section 2, section 5 and section 10 wording fixed; `check-prod-compose.sh` refuses a swag sample that gives `publickey-credentials-get` or `-create` the value `()` or `*` (self-tested). Runbook: [docs/runbooks/passkeys.md](runbooks/passkeys.md). Docs and one script check only; no application code.
- **Hardening batch from the #749, #759, #760 and #769 reviews (2026-11-07; tests, helpers, comments and docs only).** The e2e `stepUp` wait only ends on an `/api/admin/` write and `waitForMail` has no `!`; `test_security_log_at_index.py` also checks that the 7-day purge plans on an `at` index (`security_log_at` or `security_log_owner_at`, depending on how many owners hold the rows) and cleans up through `migrated`; a test pins that `classbeat.auth.__getattr__` caches resolved names, and its stale comment is fixed; the admin-path scan in `test_admin_abuse.py` now sees `AsyncSession | None` parameters, `import classbeat.x` and `from classbeat import x`, and fails on an annotation of a `classbeat` function that cannot be resolved; ADR 0003 and `developer-commands.md` note that `ip_hash` is a keyed pseudonym of the full IPv4 address, the key-rotation caveat, and that the admin-abuse Disable shortcut is covered by vitest only. No production behaviour change.
- **Split of the admin audit, admin routes and auth package modules (#753, 2026-11-07).** Refactor only, no behaviour change (code-structure rule 2): the admin area's free-text validators and types move from `admin/audit.py` to `admin/audit_text.py`, the admin body limit (`AdminBodyLimit`, `MAX_BODY_BYTES`, `body_refusal`) from `admin/routes.py` to `admin/body_limit.py`, and the lazy export table and resolver of `classbeat.auth` to `auth/lazy.py` (the `TYPE_CHECKING` imports and `__all__` stay in `__init__`). The security-log scan of the admin path (`test_admin_abuse.py`) now derives the modules outside the admin package that take its maint session from the package's imports (`auth/accounts.py`, `connections/status.py` and the rest), instead of a glob that missed them; the two export-table guards of `test_user_context_guard.py` name `auth/lazy.py` next to `auth/__init__.py`.
- **Admin abuse view (#632 item 3, 2026-11-07).** `/admin/abuse` and `GET /api/admin/abuse` over the security log: top users by request rate, 429 hits, sessions seen from several networks (keyed hashes of the client address), with the existing Disable (sign out everywhere) shortcut. Maint gets a column grant on `security_log`; guard 11 allows only `ip_hash`, read as `count(DISTINCT ...)`. ADR 0003 amendment "the security log" has the limits (429s stand for quota hits; `rate_limits` is not per user).
- **Index on `security_log (at)` (#750, 2026-11-07).** Migration 0040 adds it so the abuse view's window is a range scan instead of a read of the whole table (the `(owner_id, at)` index cannot serve a query with no `owner_id`). No grant change; `test_security_log_at_index.py` checks the index and that the view's four window queries plan on it.
- **Language-prefixed URLs (#719, ADR 0027): P3 (e2e, docs), 2026-11-07.** P1 (#743, frontend) and P2 (#744, backend) are merged; P3 adds the compose-stack specs `e2e/tests/lang-*.spec.ts` in en, sv and da (deep link through sign-in, a language switch that keeps the page, old unprefixed mail links reaching the page with the token, `<html lang>`, canonical and hreflang, sitemap and robots, axe at 390 px), the owner steps in the runbook and "Waiting for the owner", and ADR 0027 Accepted. Checked in the repo: `swag-classbeat.subdomain.conf` and `scripts/check-prod-compose.sh` have no path allowlist, so prefixed paths pass as they are. ADR 0027 item 7 is corrected: the footer's site link stays unprefixed (the client redirects it). `SpaPages.select` now strips exactly one trailing slash, as its comment says (a test pins it). Closes #719.
- **ME follow-up: `/email/` CORP promise reconciled (#679, 2026-11-07; docs only).** ADR 0022 gets a dated CORP amendment (status unchanged): behind the SWAG proxy the server-wide `same-origin` CORP also covers `/email/*`, so webmail without an image proxy shows no hosted logo (mail stays readable without images) and the admin preview iframe logo may be blocked; the runbook no longer says real mail is unaffected. The guard (`scripts/check-prod-compose.sh`) is not extended; a dedicated `/email/` location with pinned headers is recorded as not planned. Also documented, no code change: the `mail.test` audit row records `ok` and a failed send is a separate `admin.request` `send_failed` row.
- **Scaling music analysis (ADR 0026, #733, 2026-11-07, Proposed; docs only).** Owner request "write it up for the future". Records the staged plan (cap, measure and cache now; more workers on one box; a separate analysis host; queue-length autoscaling) with a trigger for each, and states what exists (the analysis cap #574/#630, the 10 analyses per user per day from ADR 0016, the per-user `swedebeat_mix_maps` cache) and what is missing (queue-wait and analysis-time metrics, any cross-user cache, CPU and memory ceilings, an analysis-only worker, a two-worker drill). Only Swedebeat mix analysis is heavy; Spotify analysis is MOWL's. Nothing is built or deployed. Follow-up issue for stage 1 is the Architect's.
- **Technical completion (2026-11-01).** M3c, M4, M5, MU and M6 are done technically, on fakes and in CI. M6's checklist was checked when #340 closed: the restore drill passed into a fresh stack, GDPR export and delete and guard 12 are on main, the log, ciphertext-dump and 403/404 route tests are on main, and `scripts/check-prod-compose.sh` accepts `compose.prod.yaml` and the Caddyfile. Nothing has been deployed. The owner's remaining steps are listed under "Waiting for the owner".
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
  #116), #136 (model comparison, #134); live smoke findings #131 closed. Also merged: #98 and #107
  (auth tests), #114 (A1), #122 (D1), #127 (U1), #135 (egress), #137 (UI and tests), #139
  (connections and egress), #140 (from #138), and #117 (X1, #434: a MOWL refusal on attach is
  `failed`, not uncertain, and a reaped `pending` row stays off the user's list until a later job
  adopts it). Still open: #153 (compare-tool follow-ups from #136).
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
- **M4 design accepted** (song picking, Spotify BYOK): ADR 0008 (song picking) and ADR 0009 (the Spotify
  connection) were accepted on 2026-10-31 (#198 item 6, with their dated amendments), and roadmap
  Amendment 5 is in effect. Of their 14 issues, #173 to #185 are closed and #186 closes with its part 2 (the entry below). Nothing live is run; the
  owner-authorized live steps are in ADR 0008 §11. The superseded-by notes that ADR 0009 owed are written:
  ADR 0004 §4, ADR 0005 §3 and §4's category table, and ADR 0007 §9 (the guard is `generation`,
  compared together with `id`; for Spotify only the flips of ADR 0009 §6). The M4 review follow-ups
  (#198) are done on `main`; item 19 (A3's paging, unreachable with 40 songs) is covered at the
  playlist-read level in `e2e/tests/fake-spotify.spec.ts`. None of #198's items needs a live step.
- **M4 done technically (#186 part 2, 2026-10-31), on fakes only:** nothing live was run. Part 1
  (`spotify-connect.spec.ts`: wizard, consent, Disconnect, two users on different Client IDs, every
  error category's page text) and part 2 (`song-pick.spec.ts`: pick, approval, refusals, partial failure,
  Change songs) check the roadmap M4 "done when" like this (E2E = Playwright against the compose stack
  with fake Spotify, fake MOWL and the recorded model; backend = pytest named in the right column):

  | Done-when item | E2E spec | Backend test |
  |---|---|---|
  | A1 to A3: brief to a draft with one Approve; private (`public: false`, `collaborative: false`) final playlist with exactly the approved ids in order; MOWL lists them | `song-pick`: "a pick, one Approve, and a private playlist of exactly the approved songs, in order" | `test_playlists_writes.py::test_a_final_playlist_is_created_for_its_class_then_filled_and_verified`; 120 items over three pages: `fake-spotify.spec.ts` and `test_spotify_fake.py` |
  | A drop, duplicate or reorder makes the read-back `playlist_mismatch` | `song-pick`: "a playlist that does not match the approved songs fails the job" | `test_playlists_writes.py::test_a_final_fill_whose_read_back_differs_is_a_mismatch_and_not_retried` |
  | Superseded approval | `song-pick`: "a newer list replaced this one while it waits" (still waiting, refused as superseded) and "a newer pick stops the older list" (the stale tab of a stopped pick) | `test_design_with_songs_approve.py::test_expired_superseded_and_hash_mismatch_start_nothing` |
  | Expired approval | `song-pick`: "a list that was not approved in time expires" | same test, and `test_an_expired_list_makes_no_call_and_releases_its_credit_once` (the TTL floor is backend only) |
  | Hash mismatch, a double click and a second tab, another user's job or revision (404), disconnect while waiting, quota refused | `song-pick`: "songs changed behind the page make the approval refuse on the hash", "two users on different Client IDs pick at once" (404 on read and approve), "a Spotify account switch while songs wait" | `test_any_change_to_the_stored_list_is_a_hash_mismatch`, `test_a_double_click_and_a_second_tab_are_one_approval`, `test_another_users_job_or_revision_is_not_found`, `test_a_disconnect_while_waiting_is_not_connected`, `test_the_per_user_limit_and_the_credit_seam` (all `test_design_with_songs_approve.py`), `test_spotify_quota.py` |
  | Ownership: a working or final playlist that is not the user's own, public, collaborative, gone or edited to another user's id: nothing written | `song-pick`: "a working playlist that is not the user's own is never written to" | `test_playlists_writes.py::test_no_write_reaches_a_pool_playlist_that_is_public_collaborative_or_gone`, `test_a_pool_id_edited_to_another_users_playlist_sends_no_write`, `test_a_class_playlist_id_edited_to_another_users_playlist_sends_no_write` |
  | Partial-failure reconcile and resume | `song-pick`: "a playlist that does not match", "a lost answer to the playlist create is reconciled", "a playlist write that may not have happened is left to the user", "Change songs: a failed write is resumed" | `test_playlists_writes.py::test_failure_injection_at_every_request_of_a_pool_replace` and the create/fill tests there; `test_playlists_reconcile.py` |
  | Every error category | `spotify-connect`: "every error category has its plain sentence on the Spotify page, and none shows a provider message", "a token endpoint that refuses the app" | `test_spotify_errors.py` |
  | Concurrent users on different Client IDs, tokens, music and playlists never mix | `song-pick` and `spotify-connect`: "two users on different Client IDs ... never mix" (every fake request carries the token of the playlist's owner and of that user's own app) | `test_owner_filter.py`, `test_spotify_connect.py` |
  | A callback cannot connect the wrong account; another Spotify account needs a confirmed switch | `spotify-connect`: "a callback with a changed state connects nothing", "another Spotify account for the same user needs a confirmed switch", "an account that is not on the app allow-list is refused" | `test_spotify_account.py`, `test_spotify_connect.py` |
  | Change songs | `song-pick`: "Change songs: one song replaced, a new playlist after one Approve, the old one untouched" | `test_revise_songs.py` |

  Live activation stays the owner's (ADR 0008 section 11; agents run a step only with the owner's
  go-ahead for that step, logged under "Live activation log"): steps 1 to 7 of the runbook, and the
  checks it measures: MOWL follows edits of an attached playlist, the `/items` shape and page size,
  the listing lag (`CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS`, `CLASSBEAT_MOWL_LISTING_LAG_SECONDS`), the
  `/me` 403 for a user off the allow-list, the `invalid_grant` body, and the cost per song-picked class
  against the ADR 0008 section 3 ceiling.
- **M3c design** (eval suite, providers, the enable gate): ADR 0012 is Accepted, roadmap Amendment 6,
  threat model T79 to T84. Owner questions with defaults are in ADR 0012 (OpenAI data, DeepSeek kept
  disabled, stale routes, thresholds, a full-suite run for Opus, provider keys); agents build to the
  defaults. Nothing live has been run. G2 and Z7 followed M4's #182 to #184, which are merged.
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
- **MU done technically** (2026-11-01, UI10 #255). ADR 0013 was merged
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
- **MU done-when** (roadmap Amendment 8; checked by UI10, [#255](https://github.com/magpern/classbeat/issues/255)):
  - [x] Every page uses the ADR 0013 tokens and components, no colour outside `tokens.css`: UI1 to UI9
    (#258, #261, #301, #283, #288, #291, #293, #308, #270) and the admin pages (M5 FE2 and FE3). Evidence:
    `frontend/src/styles/tokens.test.ts` scans every `src` css, ts and tsx file for hex, `rgb(`, `hsl(` and
    the other colour functions, CSS colour keywords and Tailwind palette utilities, plus `index.html` and
    `public/theme-init.js`.
  - [x] The class profile follows ADR 0013 section 7: UI3 (#301). Closed by #331: the delete confirmation is
    scrolled into view and focused when the menu opens it, the Z1 to Z7 grid labels have a test, and the song
    strip places each song at its start time (the lengths before it less the class's crossfade, the picker's own
    rule, so the last song ends at the class's total). Still open: comparing those start times with the analysed
    music's own; the class API sends only each song's length and the list's crossfade, so that needs a backend
    field (per-song start from the MOWL analysis) first.
  - [x] Covers load only from `i.scdn.co`, through `CoverArt`, behind the CSP header: `e2e/tests/mu-covers.spec.ts`
    (the real header, covers from the host, a cover from another host shown as a tile, the browser refusing
    other hosts and `data:`, no referrer or cookie on the cover request) and `src/no-third-party-hosts.test.ts`.
  - [x] UI10's viewport, axe and bundle checks: `e2e/tests/mu-pages.spec.ts` (24 pages at 390 and 1440 px in
    the dark and light theme: no sideways scroll, primary action visible, no serious or critical axe
    violation, quiet console), `mu-covers.spec.ts` (Songs for approval) and `npm run check:bundle` in the
    `frontend` job (first-load JavaScript 173 kB gzip of 250 kB).
  - **Open after UI10**, not part of its scope:
    - [#330](https://github.com/magpern/classbeat/issues/330): done (#335 gates `/admin` on the role or
      `adminPendingMfa`; the avatar menu's Admin link follows the same rule, in the #331 frontend PR).
    - [#331](https://github.com/magpern/classbeat/issues/331), closed by its two PRs (#334 backend, the
      frontend PR): the Dialog (and DropdownMenu) scroll lock is class-based, so the CSP refuses nothing and the
      e2e console check has no allow-list (ADR 0013 section 8 amended); the Account tab is carried through a
      provider re-authentication (#291); the delete-class confirmation scroll and focus (#301); Z1 to Z7 label
      tests (#301); CSP and Referrer-Policy on an unhandled 500 (#293); a test for the QR attribute allow-list
      (#293). Left open from it: the song strip's start times against the analysed music (needs a backend field).
- **M6, rate limit on the MOWL playlist listing (#354, SR-05, merged):**
  `GET /api/music/mowl-playlists` now uses the fixed-window limiter of the other provider-calling routes: 30 per
  user and 60 per IP per 10 minutes, every request counts, a refusal (429 `rate_limited` with `Retry-After`) comes
  before the connection is opened and before any MOWL call; a parallel test shows the limit cannot be overshot.
  Other provider-calling GET routes checked: `GET /oauth/{provider}/callback` was already limited per IP
  (`OAUTH_IP`); `GET /spotify/callback` only exchanges a single-use `state` that a limited `POST /api/spotify/start`
  created, so it is bounded by that limit; the remaining GET routes read the database only.
- **M6, GDPR export and delete (#348, follow-ups #349, both merged):** [ADR 0014](adr/0014-account-export-and-deletion.md),
  threat model T98 to T100 and T102. Checklist:
  - [x] `POST /api/account/export` (one JSON file of the user's own rows, connections as status only, no
    hash or ciphertext; recent authentication and the step-up; 5 per hour) and `POST /api/account/delete`
    (typed address checked on the server, open jobs cancelled, credits settled, then the cascade);
    the unconfirmed `DELETE /api/auth/account` is removed.
  - [x] Migration 0015: `auth_events` cascade with the user (the IP address went on living 30 days).
  - [x] Guard 12 (`tests/gdpr_guard.py`): every foreign key to `users` and every user-looking `uuid` column,
    and every owned table's export entry, read from the live schema; a catalog-driven scan of every column for
    the deleted user's id, address and IP; two-user tests for the export (also on a superuser session).
  - [x] Account tab "Your data": Download my data and Delete my account (ADR 0013 components).
  - [x] Follow-ups of the #348 review ([#349](https://github.com/magpern/classbeat/issues/349)): the
    delete scan covers text and jsonb (the id written as text) and the docs say what it does; the admin audit
    log stores no search text or email filter; every column of the exported account tables is included or
    excluded against the live schema; the export is a POST; the Users-screen delete ends jobs first.
  - [ ] Not covered, by decision (ADR 0014 §1 and §2): the admin audit log is not in the export (operator-only
    table, the operator answers a request); `admin_audit.actor_email` of an admin outlives their account;
    the bare `target_id` of an acted-on account outlives it (ADR 0014 §5). Playlists in the user's Spotify and workouts
    in their MOWL stay there.
  - [x] The security review checklist, the backup restore drill and the Caddy configuration are done
    (#340, #338, #341). The VPS deploy itself is an owner step. Nothing here ran against a live account.
- **M6 matrices, merged (#339).** `test(security)` adds the automated checks the M6 "done when" names, all on fakes
  and in CI: `backend/tests/test_m6_security_logs.py` (no secret in any log record or error response over the full
  fake flows: sign-in, reset by mail, OAuth sign-up, MOWL and Spotify connect, a pick, its approval, the design run,
  Save and an admin session; no never-returned credential in any response), `test_m6_security_dump.py` (a full
  `COPY`/`pg_dump --data-only` of every table after those flows holds ciphertext only), and
  `test_m6_security_routes.py` (the app's route table against a classification: every `/api/admin` route is 403 for a
  non-admin and for an admin without a current TOTP check, every owned-id route is 404 for another user's ids with the
  body an unknown id gets, and a new route fails until it is classified; the classification is also checked against each route's own dependencies, so a `public` route that needs a session fails). The real `pg_dump` is not run in CI (client version); the data is read with `COPY`. The `e2e` job also greps the web and worker
  logs of the whole Playwright run for the value of every secret file. The gaps this PR left (the rest of the
  review, backups and restore, GDPR, the VPS configuration) landed in the later M6 issues. #340 is closed.
- **M6, request body cap (#351, SR-01, merged).** A global
  ASGI `BodyLimit` (`classbeat.bodylimit`, 64 KiB, outside the auth middleware, inside the security headers) answers a
  fixed 413 `body_too_large` for every route but `/api/admin` (which keeps its own equal limiter and contract); threat
  model T103; `test_m6_security_body_limit.py`. On a local stack a 200 MB anonymous login body took the web container
  from about 95 MiB to at least 264 MiB (sampled) and answered 422 after 13.5 s before; after, it stays at about 97 MiB
  (three parallel 200 MB posts peaked at about 111 MiB) and the sender is refused with 413 and cut off early. Each refusal writes a rate-limited log line (method, path without query, status, client address; #360), one per client address per 10 s (an IPv6 client by its /64), and a failing log call (a raising handler or filter) never turns the 413 into a 500 (#365; the failure is counted and reported once per 10 s on stderr with the exception class only). **The home beta behind SWAG needs `client_max_body_size 1m` at the
  proxy** (runbook section 8a): that is the owner's configuration. SR-02 (#352, Proton Bridge and the dev-profile beta) stays an owner step.
- **M6, web outbound allowlist and redirects (#353, SR-03, merged):** the web
  process now installs an allowlisted urllib opener at start (`python -m classbeat`; `api.mowl.com` and `api.spotify.com`,
  `https` on 443, no environment proxy, a redirect only to the same host), so the MOWL token cannot follow a redirect to another host; the
  OAuth sign-in clients no longer read the environment or follow redirects. Tests use fake upstreams that record what
  each host receives. Threat model T19 now says what is enforced. There is still no host allowlist for the OAuth
  sign-in clients or the mail relay, and no network-level egress rule (the threat model's accepted risk; not part of the #340 checklist).
- Releases: v0.1.0 (cut partway through M0); v0.2.0 is not cut yet (deferred to save Actions quota). Image
  `ghcr.io/magpern/classbeat`, private.

## Waiting for the owner

Agents do not deploy, change DNS, create provider apps, or contact MOWL, Spotify, Swedebeat or a model provider. Each live run needs a go-ahead for that run (2026-10-29: "do not run live test every time").

- **Passkeys: switching on (#311, [runbook](runbooks/passkeys.md)).** Code is done and off by default. To use them: (1) confirm the RP ID per environment (`classbeat.eu` production, `classbeat.pernemark.se` test; a later change invalidates every passkey); (2) **done (#790):** `compose.prod.yaml` passes `PASSKEYS` (default false) and `WEBAUTHN_RP_ID` (default empty) to web as `CLASSBEAT_PASSKEYS` and `CLASSBEAT_WEBAUTHN_RP_ID`, and the `check-prod-compose` allow-list names them (web only, self-tested); set both in `.env` after the next release; (3) your real-phone check (register on a computer, sign in on another through "use a phone").
- **Live activation.** M4: create the Spotify Development Mode app and the allow-list, then authorize steps 2 to 7 of [m4-live-activation.md](runbooks/m4-live-activation.md) (connect, one pick, one Approve, one Change songs, the cost reading, Disconnect). Until then `CLASSBEAT_SONG_PICKING` stays off. M2: the MOWL token lifetime is still unknown (a 36-character GUID, no expiry claim). The read-only check in [ADR 0004](adr/0004-connections-and-encryption.md) §6 waits for a go-ahead: one `list_workouts` a day on the owner's own connection, no write, logged under "Live activation log". Swedebeat connect: create the `SWEDEBEAT_APP` secret, cut a release, set `SWEDEBEAT_ENABLED=true`, and authorize one live check ([swedebeat-connect.md](runbooks/swedebeat-connect.md)). Until the secret exists, release builds fail the key check and connect answers `app_key_unavailable`.
- **Server deploy.** The server is the owner's own machine at home, behind a swag/nginx reverse proxy; DNS is in Cloudflare and points there (today `classbeat.pernemark.se`). Agents cannot reach it: the deploy is done from a local session on that machine, with the owner's go-ahead. The prod files run behind swag with no Caddy (#492: `compose.prod.yaml` on swag's proxy network or the `compose.prod.loopback.yaml` overlay, swag as the one trusted proxy, the sample site config [swag-classbeat.subdomain.conf](runbooks/swag-classbeat.subdomain.conf) and the Cloudflare notes in [vps-deploy.md](runbooks/vps-deploy.md)); not deployed. Still open for the deploy: the backup schedule, an off-host copy, and the real backup recipient and signing keys.
- **Model enablement.** [m3c-model-enable.md](runbooks/m3c-model-enable.md): place the OpenAI key (DeepSeek later), give a `--max-usd` for each first live run, then a recorded run and `models enable`. Until a pass is recorded, the bootstrap Anthropic model serves users. OpenAI and DeepSeek stay `owner_pending` until you answer ADR 0012's questions (extend the Spotify risk acceptance to OpenAI; keep DeepSeek disabled; stale routes; thresholds; a full-suite run before Opus 5.5 is used by a tier).
- **Mail for the app (#352).** Account mail goes through any SMTP relay (the app's `CLASSBEAT_SMTP_*` settings; no code change). The owner picks a transactional email service (EU-hosted if that matters; a free tier is enough for the beta), adds its SPF, DKIM and DMARC records in Cloudflare, and puts the SMTP password in the `smtp_password` secret file. Cloudflare Email Routing only forwards incoming mail (for example `admin@<domain>` to a personal inbox); it cannot send. Proton Mail Bridge is no longer the plan (it needs a desktop app that stays logged in on the server); [proton-bridge-smtp.md](runbooks/proton-bridge-smtp.md) stays for reference. Agents do not touch mail accounts.
- **Swedebeat live media host check.** `MOWL_MEDIA_HOSTS` holds only `users-playlists.s3-accelerate.amazonaws.com` (#524, ADR 0016), so a mix on any other host ends `media_host_not_allowed` and no media request is sent. The owner confirms a new host from one authorized live analysis, then a reviewed change may add that exact host. Agents do not edit the constant and do not create `SWEDEBEAT_APP`.
- **Running live checks (M8).** ADR 0018 is Accepted (owner, 2026-11-02). The read-only capture ran on 2026-11-02 (PR #468) and a one-program live save test on 2026-11-03 (see the live activation log); agents run no further live check without a go-ahead. Running profiles (#401) are merged for two interval cells (#486); more cells need more classes. No live running check has been authorized beyond the 2026-11-02 read-only discovery, the one-program save test and the one-program update test of 2026-11-03, and the read-only studio-library reads of the same day.
- **Soundless classes (#526).** The design is ADR 0007's amendment of 2026-11-03 (issues #527 to #533). `SOUNDLESS_ENABLED` stays false until the owner authorizes, one at a time: one cycling save with `PlaylistID: -1` on a new private program, read back once; one Update of that program, still `-1`, read back once; and a recorded `suite --live` pass on suite version 12 or later covering the soundless stages, then `models enable`. Without that coverage the effective flag stays off anyway.
- **App name and domain.** The repository name is classbeat. The beta can run on `classbeat.pernemark.se`. The owner is considering `classbeat.eu` (the owner quotes about $20 for 3 years, unchecked; the apex is fine for the app, no `app.` subdomain needed). The domain fixes the passkey RP ID, the Google OAuth redirect URI and the secure cookies, so it is chosen before enabling passkeys or Google sign-in. Check the renewal price and a trademark search first; the owner decides.
- **Google OAuth app creation.** Google sign-in stays off until the owner creates the OAuth client and sets `CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID` plus the `oauth_google_client_secret` file. It is not blocked on a new domain: the consent screen can stay in testing mode with the beta testers as test users, and the redirect URI can be on `classbeat.pernemark.se`. A public consent screen needs the final domain. The app asks for `auth_time` itself (ADR 0003 §5). GitHub and Microsoft are the same kind of owner step when those apps are wanted.
- **Testing starts (Turnstile, MOWL QR, support mailbox).** When testing starts the owner switches these on; agents do not. (1) **Turnstile** ([ADR 0025](adr/0025-turnstile.md), Amendment 2): the default in `compose.prod.yaml` is now on, so the owner does the Cloudflare steps in the ADR's owner list (widget, site key in `.env`, secret in `secrets/turnstile_secret_key`) before or with the deploy of the release that carries it; until the keys exist the server must have `TURNSTILE_ENABLED=false` in `.env`, or `web` will not start; `./check-prod-compose.sh` in the install folder now fails before `up` when Turnstile is on and the site key or secret file is empty (#798). Deploying to production is owner-only. (2) **`CLASSBEAT_MOWL_QR_ENABLED`** (compose `MOWL_QR_ENABLED`, default false). (3) **The real support mailbox**, `MAIL_SUPPORT_ADDRESS` or the admin's `mail_support_email` (shown on the help, privacy and terms pages). Dev and e2e use Mailpit and need none of these.
- **Language-prefixed URLs (#719, [ADR 0027](adr/0027-language-prefixed-urls.md), item 9).** Accepted and built (P1 #743, P2 #744, P3). At deploy, the owner enters `https://<domain>/en/privacy` and `https://<domain>/en/terms` in the Google consent screen (the verifier may not run JavaScript, so the unprefixed redirect is not enough) and submits `https://<domain>/sitemap.xml` to Google Search Console and Bing Webmaster Tools; the runbook has both (sections 5a and 9, post-deploy checklist). No DNS, TLS, Cloudflare or proxy change: `swag-classbeat.subdomain.conf` and `scripts/check-prod-compose.sh` have no path allowlist (checked 2026-11-07).
- **Legal line placeholder.** Sign-in and sign-up show a beta sentence (`frontend/src/components/auth/AuthScreen.tsx`): "By continuing you agree to take part in the invite-only beta. classbeat is not made by or affiliated with MOWL, Spotify or Swedebeat." The real legal line is the owner's.
- **Shared mix analysis across users (ADR 0026 stage 1, item 4 of #735; decision only, nothing built).** Today every user's first use of a Swedebeat mix costs one full fetch (up to 250 MB) and analysis, because the cache is per user on purpose (ADR 0016 §3: "never shared between users"). A map shared by `mix_id` would save the fetch and decode (not the attach). Before any code it needs: (a) **the owner's licence decision** that a map derived from audio fetched under one user's entitlement may serve another user; (b) **an authorized live check** that one `mix_id` is the same audio and the same listed length for every user (unverified); (c) then ADR 0007 §11's conditions (a system table keyed by mix, length fingerprint and `MAP_VERSION`, read only through a `SECURITY DEFINER` function that checks the requester's own link first, owned rows holding only the analysis id, the purge function) in a new ADR and an Opus review. If (a) is no, stage 1 stays "measure and limit" (queue-wait and analysis-time metrics and the CPU and memory ceilings, #735). Agents do not decide (a) and do not run (b).
- **M5 product questions** ([ADR 0011](adr/0011-tiers-quotas-and-admin.md) §11, Q1 to Q10): limits and cost caps per tier, which plan the testers get, trial on or off, quota period, refund on cancel, overlapping codes, Free feature gates, the Pro priority queue, audit-log retention, prices. Agents build on the defaults; an answer changes seeds or settings, not the design.

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
- 2026-11-02: owner-authorized read-only MOWL discovery for M8 / ADR 0018 (D1), in a cloud agent
  session (the owner gave a temporary password in chat; used only for sign-in, never written to
  the repo, a log or a file; the token was never printed). Calls: `login`, `POST v1/programs`
  (lists), `GET v1/programs/{id}` (24 official running programs of a professional studio library), `GET v1/ProgramCategories`,
  `GET v1/ActivityTypes`, `GET v1/users/me` (not recorded). No save, update, delete or attach.
  Findings: [docs/providers/mowl-running-discovery.md](providers/mowl-running-discovery.md). They answer
  most of ADR 0018's unknowns 1 to 5 and 7; 6 and the segment/save fields still need a running program
  the owner creates. Nothing was created or changed.

- 2026-11-03: authorized live write, MOWL running save test (D1 save side, ADR 0018). Owner go-ahead in chat
  ("the containers environment has been changed to allow api.mowl.com, so you should be able to do the MOWL
  running save test"), with the owner's temporary password from chat used only to sign in (stdin of a one-off
  script; never written to the repo, a log or a file; the token was never printed). Created in the owner's
  MOWL account: **one private program** named "classbeat running save test (safe to delete)" (program 336224,
  3 segments, 4 intervals, 6 minutes); `mowl-workouts` has no program delete; the owner deleted it in the app (confirmed 2026-11-03). Requests: `login`, `POST v1/programs` (list, before and after), `PUT v1/programs`, three times
  `PUT v1/segments`, `DELETE v1/segments/{id}/intervals`, `PUT v1/segments/{id}/intervals/multiple`,
  `PUT v1/programs/{id}/segments`, `GET v1/programs/{id}`. Findings in
  [docs/providers/mowl-running-discovery.md](providers/mowl-running-discovery.md) and ADR 0018 Amendment 2.
  Not tested then: update of a running program (tested later the same day, next entry), a new playlist/mix on a running program, segment `ActivityTypeID` 1.
 

- 2026-11-03: authorized live write, MOWL running update test (ADR 0018 Amendment 3). Owner go-ahead in chat
  ("if you need it you have my go. Just don't alter any existing. Create a new, then alter it"), with the owner's
  temporary password from chat used only to sign in (stdin of a one-off script; never written to the repo, a log
  or a file; the token was never printed). Created in the owner's MOWL account: **one NEW private program** named
  "classbeat running update test v2 (safe to delete)" (it was created as v1 and re-saved as v2; program 336241, 6
  segments in total over the two saves, 4 intervals each); `mowl-workouts` has no program delete, so the owner
  deletes it in the app. No existing program was touched. Requests: `login`, `POST v1/programs` (list), the
  create sequence of the save test, then the update: `PUT v1/programs/{id}` with the program id, three new
  segments (`PUT v1/segments`, `DELETE v1/segments/{id}/intervals`, `PUT v1/segments/{id}/intervals/multiple`),
  `PUT v1/programs/{id}/segments`, `GET v1/programs/{id}`, then `PUT v1/programs/{id}` with the `PlaylistID` of an
  already-analysed playlist read from the account's own listing (no new attach). Result: the update works, the
  playlist id is accepted and kept. Not tested: a NEW mix or playlist attach for a running program and segment
  `ActivityTypeID` 1.
- 2026-11-03: owner-authorized live reads of the studio library for ADR 0017 / P9 (issue #388, #401). Owner
  go-ahead in chat ("Grab what you need", "you can look in [the studio's] public camps (read only)", then a camp
  link). Read-only calls with the same sign-in: `GET v1/ProgramCategories` (list and single records, about 120
  camp ids scanned), `POST v1/programs` (camp listings, counts of the cycling library) and `GET v1/programs/{id}`
  for 64 official running programs. Raw JSON stayed in the session scratchpad outside any repository (it goes
  when the session's container is reclaimed); only aggregates were published (#486, ADR 0017 §7). Nothing was
  created or changed in the account.

## Exceptions

- The first commit on `main` was pushed directly, before the ruleset existed (bootstrap).
  It holds only the protocol, CI gate and docs. Everything after it goes through PRs.
