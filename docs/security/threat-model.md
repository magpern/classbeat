# Threat model

Reviewed at every milestone; the M6 security review re-checks it end to end. Milestones refer
to [the roadmap](../roadmap.md); §7 there lists the security work per milestone.

## What we protect

| Asset | Why it matters |
|---|---|
| User accounts, sessions, linked identities | Takeover gives access to everything below |
| MOWL tokens | Write access to the user's MOWL account (workouts, playlists) |
| Spotify Client IDs and access/refresh tokens | Write access to the user's Spotify playlists |
| Swedebeat tokens and device ids | Access to the user's Swedebeat account |
| Our model API keys and budget | Direct cost; abuse runs up the bill |
| Classes, revisions, conversations, usage | The user's work and private data |
| Admin powers | Grants, codes, flags, user management |
| The repository, CI and images on GHCR | A compromised build reaches every user |

## Who might attack, or fail

- **Anyone on the internet**: no account; sees the landing page and sign-in.
- **A signed-in user** who is curious or malicious, trying other users' IDs or abusing quotas.
- **A taken-over account** (phished, reused password).
- **Hostile content in upstream data**: song titles, playlist names, artist fields, a user's own
  free text. It reaches the model and can try to steer it (prompt injection).
- **The model itself**: its output is untrusted; it can be wrong, inconsistent or steered.
- **A compromised dependency, base image or GitHub Action.**
- **Our own autonomous agents**: a mistake can commit a secret or weaken a check.
- **Provider failures**: MOWL, Spotify, Swedebeat or a model API timing out mid-write.

## Trust boundaries

```
browser ──TLS── caddy ── web ──────────── Postgres
                          │ (app role, RLS)  │
                          ├── sign-in providers (Google, GitHub, Microsoft; Spotify OAuth)
                          ├── mail relay
                          ├── MOWL / Swedebeat sign-in (user's password, once)
                       worker ───────────────┘
                          ├── model APIs (our keys; receives class data)
                          └── MOWL / Spotify / Swedebeat (the user's tokens)
GitHub repo ── Actions ── GHCR image (private) ── VPS (owner-deployed)
development agents ── the repository, issues, PRs (their input is untrusted text too)
```

Everything crossing into web or worker is validated there: browser input, upstream API
responses and model output alike.

## Threats and mitigations

| # | Threat | Mitigation | Milestone |
|---|---|---|---|
| T1 | Account takeover by OAuth email matching (an attacker's provider account with the victim's email) | Identity key is `(provider, subject)`, never email; linking needs a signed-in confirmation; unverified emails never link; Microsoft emails always count as unverified (ADR 0003 §5) | M1 |
| T2 | Login CSRF, OAuth state or code injection | State, nonce (OIDC) and PKCE; single-use, short-lived callbacks bound to the browser session | M1 |
| T3 | Credential stuffing and brute force | argon2id; failure-only rate limits per (account, IP), per IP, and a per-account cap with a known-device exemption; no permanent lockout; invite-only beta. Residual: an attacker who knows the email can exhaust the per-account cap from many IPs, so the victim signs in from a known device or by OAuth for up to an hour (accepted for the beta, revisited at M6; ADR 0003 §9) | M1 |
| T4 | Session theft or fixation | Server-side sessions stored as token hashes; `__Host-` cookie, HttpOnly, Secure, SameSite=Lax; rotation on sign-in, MFA and re-auth; all sessions revoked on a reset or role change; idle 7 days and absolute 30 days; Origin check and CSRF tokens on writes (ADR 0003 §3, §4) | M1 |
| T5 | Cross-tenant read or write (IDOR), including through a background job | `UserContext` everywhere, repository filters, Postgres RLS (ADR 0002), guard tests, 404 tests for foreign IDs; a job's context is issued only from its claimed row, always with role `user` (ADR 0005 §3) | M0 contract, M2, M3a |
| T6 | Token theft from a database dump or backup | Envelope encryption, AES-256-GCM with AAD `user_id\|provider`, keys outside the database, `key_id` rotation; encrypted backups | M2, M6 |
| T7 | Secrets in logs, errors or traces | `SecretStr`; secrets only from files; no exception text in API responses; log redaction and tests that grep logs for planted tokens | M0, M2 |
| T8 | Prompt injection through upstream content steering the agent into writes or leaks | The model has no write tools; per-stage tool allowlists; writes only by the backend after the user's approval and server-side revalidation; tool output marked as data; tokens never sent to a model | M3a, M4 |
| T9 | A model-supplied ID that belongs to another user | Tool handlers resolve IDs within `ctx`'s rows only; tests | M3a |
| T10 | Writes to a user's accounts they didn't approve | Write categories (§2); approval carries the revision ID and content hash; only the latest list for the class; managed playlists by ID with an ownership check | M3b, M4 |
| T11 | Duplicate or half-done external writes after a crash | `external_operations` with deterministic keys and request hashes; a `pending` row from an older lease counts as `uncertain`; `uncertain` writes are reconciled once and never retried automatically (ADR 0005 §7) | M3a, M3b |
| T12 | A stale worker committing after losing its lease | Leases with heartbeat; fencing token (`lease_version`) checked as the first statement of every worker transaction, which then holds the job row lock until commit; stale-worker tests (ADR 0005 §3) | M3a |
| T13 | Cost exhaustion: many or huge jobs, or a runaway agent loop | Invite-only beta; quotas reserved before work; per-job limits on turns, tool calls, time and cost, cost counted across retries (ADR 0006 §5); per-user job limit (ADR 0005 §2); rate limits | M3a, M5 |
| T14 | A Spotify OAuth callback connecting the wrong account or Client ID | State and PKCE verifier stored server-side, bound to user, Client ID and redirect URI, single-use; mismatch rejected | M4 |
| T15 | Admin account compromise | TOTP MFA mandatory; step-up for sensitive actions; admin routes 403 for others; audit log | M1, M5 |
| T16 | Admin misuse | Admins never see tokens; every admin action audited; admin reads through the separate audited role | M5 |
| T17 | Compromised dependency, base image or Action | Lockfiles; exact versions; checksum-verified tool downloads; pip-audit, npm audit, Trivy; Dependabot; least-privilege workflow `permissions`; no `pull_request_target`; `persist-credentials: false` | M0 |
| T18 | An agent committing a secret or weakening a check | gitleaks in CI (a PR's own commits; the full history of every branch on the weekly and manual runs); fresh-Opus review gate; ruleset with required checks; agents can't bypass | M0 |
| T19 | SSRF through user-supplied links (playlists, mixes) | Parse provider IDs only, never fetch user URLs; outbound allowlists for worker (MOWL, Spotify, Swedebeat, model APIs; in-process from M3a, ADR 0006 §9) and web (sign-in providers, Spotify OAuth, MOWL and Swedebeat sign-in, mail relay, and since D1 one read-only MOWL call with the user's token, `GET /api/music/mowl-playlists`, to the same host `api.mowl.com`) | M3a, M4 |
| T20 | XSS through user, model or upstream text in the SPA | React escaping only, no raw HTML rendering; strict CSP | M1, M6 |
| T21 | Exposed services or container breakout | Non-root image; local ports bound to 127.0.0.1; only Caddy public on the VPS; Postgres never published there | M0, M6 |
| T22 | Keeping data longer than allowed, or no way to delete it | GDPR export and delete; a user's Spotify-derived track lists and timing deleted with their class or account; a shared music analysis deleted once no class of any user refers to it (ADR 0010; roadmap Amendment 1) | M3b, M6 |
| T23 | A MOWL or Swedebeat password captured or kept | Typed on our page over TLS, used once for a token, never stored, logged, put in a URL or sent to a model | M2 |
| T24 | Email abuse: sign-up spam, reset flooding | Invite codes; rate limits on sign-up, verification and reset mail | M1 |
| T25 | Playlist links shown to other users | Never displayed outside the owner's pages; privacy wording explains what Spotify's non-public setting means | M4 |
| T26 | SQL injection: the app role can set `app.user_id` itself, so injected SQL defeats both the code checks and RLS, and could hold locks on owned tables | Only bound parameters and the ORM; no SQL built from strings (ruff's SQL-injection rule on); the app role has no `TRUNCATE`, `MAINTAIN` or ownership (ADR 0002). Locks can't be prevented for a role with `UPDATE`/`DELETE`; `lock_timeout`, `statement_timeout` and `idle_in_transaction_session_timeout` are role defaults of the app role and limit accidents, but injected SQL could lower them, and `ALTER ROLE classbeat_app SET statement_timeout = 0` by the role itself would persist (Postgres cannot forbid it). Web and worker check the role settings at startup and on every new connection and refuse to start on any difference, and guard 2 checks them too (residual denial-of-service risk until detected) | M0, every PR |
| T27 | A stolen but still valid session changing the account's identities, email, password or connections | Recent authentication (10 min) required for those actions (§3) | M1 |
| T28 | Taking over the first admin through `ADMIN_EMAILS` with an unverified email | The bootstrap grants admin only to an account whose email is verified, then requires TOTP setup | M1 |
| T29 | Shared music analysis leaking one user's sources to another | Reuse only after an access check with the requester's own credentials; the analysis holds no playlist links or owner data (§6) | M2, M4 |
| T30 | `NOTIFY` payloads read by any listener on the database | Payloads carry only IDs; data is read afterwards in a user session (ADR 0002 §5, ADR 0005 §6) | M3a |
| T31 | Prompt injection of the development agents through issue, PR or upstream text | Agents treat repository and web content as data; the review gate and ruleset apply to every merge; secrets are never in the repository or agent prompts | M0 |
| T32 | User data sent to model providers beyond need | Only the class brief, the music data (for the beta circle Spotify-derived track metadata and MOWL analysis, roadmap Amendment 1), and the draft are sent; no tokens, emails or account data; provider data-retention terms checked before enabling a provider (§5) | M3a, M3c |
| T33 | Account enumeration through sign-in, sign-up, reset or timing | One generic message per flow; sign-up hashes the password and answers "check your inbox" whether or not the email exists, with the difference only in the mail; a dummy argon2 verification for unknown emails and password-less users; mail sent after the response (ADR 0003 §5) | M1 |
| T34 | A reset or verification token leaking through logs, referrers or reuse | 32 random bytes, stored as SHA-256 only, single-use, short-lived (reset 30 min); carried in the URL fragment so it never reaches server or proxy logs; a reset signs out every session (ADR 0003 §5) | M1 |
| T35 | TOTP secrets read from the database | AES-256-GCM with a key derived from `app_secret_key` outside the database, AAD bound to the user; moved onto the envelope scheme in M2 (ADR 0003 §8, ADR 0004 §2) | M1, M2 |
| T36 | Open redirect after sign-in | `return_to` only as a relative path starting with a single `/`, checked again before use (ADR 0003 §4) | M1 |
| T37 | Host-header poisoning of links in mail | Links built only from `CLASSBEAT_PUBLIC_BASE_URL`, never from request headers (ADR 0003 §5) | M1 |
| T38 | OAuth mix-up between providers, or a forged ID token | One callback path per provider; the flow row records the provider and is taken atomically; ID tokens RS256 only, checked for signature (provider JWKS), `iss`, `aud`, `exp`, `nonce`; Microsoft's per-tenant issuer checked against the token's `tid` (ADR 0003 §5) | M1 |
| T39 | Account pre-hijacking: registering a victim's address first (by password or an unverified provider email) and keeping access after the victim takes it over | No unverified accounts: an account exists only after a mail link proves the address (and, for a password sign-up, the chosen password is typed again at confirmation), or a provider states it verified it; Microsoft emails count as unverified; a verified provider email matching an existing user creates and links nothing; provider sign-ups with an unverified email confirm only in the browser that started them; email-change confirmation needs the requesting user's session; every link is mailed; a reset clears known devices and offers to remove linked methods (ADR 0003 §1, §3, §5) | M1 |
| T40 | Provider tokens read from the database or a backup | Envelope encryption: a random data key per row and write, AES-256-GCM, wrapped by a KEK kept in a secret file outside the database; ciphertext bound by AAD to owner, provider and row; backups hold ciphertext only; a startup check value catches a swapped KEK; rewrap re-wraps keys but doesn't re-encrypt payloads, so any suspected KEK compromise wipes every connection and resets every TOTP with the services stopped, and older backups are treated as compromised (ADR 0004 §2) | M2 |
| T41 | A token leaking through logs, errors, job rows, events or model input | Only `classbeat.connections` decrypts; secrets travel only as a redacting `Credentials` object built per request or job; jobs store the connection id; the log formatter masks secret keys; caplog, `repr` and `pg_dump` tests (ADR 0004 §3, §7) | M2 |
| T42 | One user's credentials used for another user's work | Owned table under RLS read through `UserContext`; the worker rebuilds credentials from the job's owner; AAD makes ciphertext moved between rows fail; concurrent two-user test with fake upstreams recording each call's token (ADR 0004 §2, §3; #67) | M2 |
| T43 | Password guessing against MOWL through our connect page | Recent auth and CSRF on connect; 5 attempts per user, 20 per IP and 5 per MOWL email per 15 minutes (ADR 0004 §4) | M2 |
| T44 | A MOWL password kept after connect | Used once for the library's sign-in, never stored, logged, put in a URL, kept in an exception (classbeat raises its own error after the `except` block, so the library's exception is neither cause nor context) or sent to a model; the route's bodies are excluded from request logging; tests check logs (ADR 0004 §4) | M2 |
| T45 | The queue's `SECURITY DEFINER` functions used to read or change other users' jobs | Five functions owned by `NOLOGIN` `classbeat_queue` with only the privileges they need; fixed `search_path`, schema-qualified names, no caller-supplied ids, only `(job_id, owner_id, lease_version)` or counts returned; created and handed over by a superuser script, never by migrations; web and worker refuse to start on any drift; guard 9 (ADR 0005 §3) | M3a |
| T46 | A repeated or replayed browser request creating duplicate jobs, reservations or approvals | `Idempotency-Key` with the request hash, `UNIQUE (owner_id, idempotency_key)`, 409 on a different body; approve and cancel idempotent by target state (ADR 0005 §2) | M3a |
| T47 | Approving a song list other than the one shown (a stale tab, a superseded or expired list) | Approval carries revision id and content hash, checked under a row lock against the job's waiting list and the newest list of the class; expiry after 7 days; a refused approval starts nothing (ADR 0005 §2, §4) | M3a, M4 |
| T48 | Job events or SSE leaking another user's progress, secrets or provider data | Owned `job_events` read per request in a user session; 404 for foreign ids; per-kind payload models allow only enums, own ids, counts and times, never error text, model text or provider data; `NOTIFY` carries a job id only (ADR 0005 §6) | M3a |
| T49 | The worker used to reach arbitrary hosts (SSRF, exfiltration through the model or the library) | Process-wide outbound allowlist on `urllib` and `httpx` (https, listed hosts, port 443, every redirect hop); guard test on HTTP client construction; no fetch of user-supplied URLs; TLS contexts that never write a key log (`SSLKEYLOGFILE` is ignored, and the model provider refuses to start with it set) (ADR 0006 §9) | M3a |
| T50 | A poison job retried forever, or retries multiplying model cost | `max_attempts` per phase (default 3), capped exponential backoff, non-retryable categories fail at once, the cost ceiling counts every attempt of the whole job; the claim skips a row with spent attempts and the reaper fails it, so one bad row can't stall other users' jobs (ADR 0005 §2, §3, §4, ADR 0006 §5) | M3a |
| T51 | A worker that lost its lease mid-call recording or repeating an external write | Outcomes recorded only under the token that wrote `pending`; a `pending` row from an older token is `uncertain` and never re-sent; the user decides (ADR 0005 §7) | M3a |
| T52 | The model API key leaking or used by the web process | Secret file mounted on the worker only, `SecretStr`, never logged; `docker inspect` check; the fake provider refused in `prod` (ADR 0006 §9) | M3a |
| T53 | A job continuing for a disabled or deleted user | Owner checked after each claim through `classbeat.auth`; jobs cascade with the user and class, so the fence fails on the next write (ADR 0005 §3) | M3a |
| T54 | Model-supplied identifiers reaching another user's or an unintended resource | Extends T9: the model sees and names only indexes into data loaded for this job; no provider or row ids in model input; the dispatcher validates indexes (ADR 0006 §3, §7) | M3a, M4 |
| T55 | Malformed, oversized or out-of-range model output stored or saved | Strict Pydantic schemas with bounds, 32 KB caps on tool input and results, `workout.validate()` before storing and before any save, TSS and timing computed by the backend (ADR 0006 §3, §4) | M3a |
| T56 | Prompt injection through song titles, playlist names or the brief | Extends T8: no write or network tools, index-only references, fixed-shape tool results, validation and limits; adversarial fake scripts and smoke cases (ADR 0006 §7, §8) | M3a |
| T57 | Data beyond the allowed fields reaching the model provider | Extends T32: one allowlisted model-input builder; planted-value test over every forbidden field (tokens, emails, ids, URLs, provider errors) (ADR 0006 §7) | M3a |
| T58 | A Save or Update writing something other than the revision the user approved (stale tab, replay, tampered body) | The browser names rows by id and hash only; revalidated at creation, in the worker and in the operation's decide transaction against the authenticated user and the stored revision; the request hash covers content hash and target (ADR 0007 §5) | M3b |
| T59 | An Update overwriting a MOWL program that is not the user's saved one, or one they edited in MOWL | The program id is read from the user's own live `saved_workouts` row, never from input; the program is read first and a changed, public or missing one stops the job unless the user confirms an overwrite (ADR 0007 §7) | M3b |
| T60 | Duplicate or half-written workouts after a failed multi-request write | Every failure of the write is `uncertain`, never retried by a job; reconciled by a listing baseline (Save and Update) and the full content; an update whose target is unchanged but that left a new program is inconclusive, so strays cannot pile up through retries; absence is no proof until the listing lag is measured; an orphaned `pending` row becomes `uncertain` and Save/Update/class delete refuse until it is resolved; the user's Check again / Do it anyway (ADR 0007 §6 to §8) | M3b |
| T61 | A repair interleaving with a stale call that is still sending requests | `call_may_be_running`: no user action, no automatic reconciliation and no class delete on such a row until the call's bound (a total per-request deadline) has passed; every return to `pending` resets `call_ended_at` and the context (ADR 0007 §1, §6, §8) | M3b |
| T62 | A write landing in a different MOWL account after a reconnect, or an old saved row pointing into the new account | The link, `hold` and credentials come from one connection generation; reconnect deletes links and write operations and detaches saved rows; Save refuses a revision whose link is gone (ADR 0007 §9) | M3b |
| T63 | Model text or an injected title persisting in the stored conversation and steering later revisions | Only the user's requests go back to the model; the model's note is shown as plain text and never read back; the change summary is computed by the backend (ADR 0007 §3) | M3b |
| T64 | Usage and cost misreported, lost with a class, or edited | Usage lives in `agent_runs`, immutable once finished and not deletable by the app role, kept past class deletion; estimates carry stated gaps (ADR 0007 §10) | M3b |
| T65 | Spotify-derived data surviving the deletion of its class or account, or a shared analysis deleted while in use | Owned rows cascade; guard 10 (retention registry) and a plant-and-delete test; a shared store needs its own `SECURITY DEFINER` purge with open-job protection before it exists (ADR 0007 §11) | M3b |
| T66 | A Spotify callback connecting the wrong account or app, or a forged callback (mix-up, code injection, a victim's session finishing an attacker's flow) | Extends T14. State (SHA-256 in the flow row) single-use, 10 minutes, bound to user, session, Client ID and the fixed redirect URI; the redirect URI sent to the token endpoint is the stored one, never from the request; a mismatch changes nothing and deletes only the caller's own flow; another user's or session's state is answered like an unknown one and deletes nothing; a different Spotify account than the connected one needs explicit confirmation (ADR 0009 §3 to §5) | M4 |
| T67 | Spotify token theft, refresh-token loss or replay, and concurrent refreshes | Tokens only as AES-256-GCM ciphertext under the envelope scheme (T40); refresh under a row lock with a total deadline below the idle-in-transaction limit, re-read after the lock, the rotated refresh token persisted before the lock is released, conditional on `generation`; the library never refreshes and never gets a refresh token; no Client Secret exists; bodies of the token endpoint never logged (ADR 0009 §6, §10) | M4 |
| T68 | A write to the wrong Spotify playlist: another user's, a name match, or one the user made public | Playlists addressed by stored id only; an owner check (`owner.id`, `public: false`, `collaborative: false`) before every write; final playlists always newly created; the only name-adjacent use is the reconciler's search for an own, just-created playlist by a unique `ref` plus the owner and privacy checks; the pool create key is per job, so a deleted or revoked pool's id is never reused, and `forget_pool` deletes the pool's operation rows with the consent revoke and `pool_missing`; the model and browser name no playlist id (ADR 0008 §2) | M4 |
| T69 | Prompt injection through track titles, artists, albums or MOWL analysis steering song choice | Extends T8 and T56. Registry indexes instead of ids, no write or network tool, fixed-shape results, text cleaned and capped, backend-computed totals and metadata, strict schemas, plain-text approval by the user, adversarial fake scripts (ADR 0008 §3) | M4 |
| T70 | Approving a song list other than the one shown, replaying an approval, or creating a playlist from an expired or superseded list | Extends T47. Approve carries revision id and hash, checked under the job row lock against the newest list; expiry with no further calls; `account_changed` and connection checks; phase 2 recomputes the hash and builds the playlist only from the stored revision; a resume needs a prior approved job as receipt, spent once its list has a completed job or a resume in flight (ADR 0008 §4 to §6) | M4 |
| T71 | BYOK misconfiguration: wrong Client ID or redirect URI, a user off the allow-list, an app owner without Premium, a Client Secret pasted | Distinct plain-word categories (`app_misconfigured`, `not_allowlisted`, `invalid_grant`); the callback URL is shown, never accepted; no secret field and a 422 test; a failed or abandoned flow leaves the old connection untouched (ADR 0009 §2, §8) | M4 |
| T72 | Quota or rate-limit abuse of the user's Spotify app, and worker threads parked on 429 | The library never sleeps on a 429; `Retry-After` drives the job's backoff; over an hour is `quota_exhausted` and not retried; a request budget per job; connect-start rate limits (ADR 0009 §4, §8) | M4 |
| T73 | SSRF or exfiltration through URLs in Spotify responses (`next`, `href`, images, previews) | Offset pagination only; no URL from a response is requested, stored or shown; ids validated as 22 characters and URIs rebuilt; the Spotify hosts are on the allowlists and nothing else is (ADR 0009 §10). Exception (ADR 0013 §9): album image URLs that fully match `https://i\.scdn\.co/image/[0-9a-f]{40}` (lowercase hex) are stored only in `song_list`, never requested by the server, never sent to or taken from the model, and loaded only by the browser; UI7 (#251) adds the cover-art host's own entry | M4, MU |
| T74 | A half-written playlist: created but not filled, or filled wrongly | Create and fill are separate operations; the fill is absolute and followed by a read-back of the exact ids and order; a create that may have happened is reconciled by a unique `ref`; nothing is retried blindly; an uncertain pool write is never offered to the user and is superseded by the next pick under a new key and `ref` (ADR 0008 §2) | M4 |
| T75 | Two jobs of one user mixing their candidates in the shared pool playlist | One pick at a time per user (`pool_busy`); the analysed track list must equal the pool just written; a stale writer blocks the next replace until its bound has passed (ADR 0008 §2) | M4 |
| T76 | Spotify-derived data surviving the deletion of its class, the consent or the connection | Registry entries and a plant-and-delete test; Disconnect, Client ID change, consent revoke, class and account deletion each have a tested path; classbeat stores no shared analysis; playlists in the user's own Spotify are theirs and the dialogs say so (ADR 0008 §8) | M4 |
| T77 | A job or refresh using a connection after the user reconnected or changed account | `connections.generation` replaces `wrapped_dek` as the guard: `hold`, status flips and refresh writes are conditional on it; a rewrap or refresh does not change it, a user's connect does; a job ends `not_connected`; only the token endpoint's `invalid_grant`/`invalid_client` or a 401 on the row's current token (also compared by `wrapped_dek`) flips a Spotify connection, so a stale 401 cannot flip a freshly refreshed one (ADR 0009 §5, §6) | M4 |
| T78 | A pool or final playlist made public or shared by the user, or its link shown to others | Created private and non-collaborative; the owner check refuses a public or collaborative playlist and classbeat never flips privacy; links shown only on the owner's own pages; the wording says that a non-public playlist can be opened by anyone with its link (T25, ADR 0008 §2) | M4 |
| T79 | An OpenAI or DeepSeek API key leaking, used by the web process, or read from the environment | As T52 for each key: secret files mounted on the worker only, `SecretStr`, never logged; the web app refuses to start when it can read one; the worker refuses `OPENAI_*`, `DEEPSEEK_*`, `ANTHROPIC_*` and `SSLKEYLOGFILE`; every SDK option is passed explicitly; canaries in the `docker inspect` stack check (ADR 0012 §2) | M3c |
| T80 | A model client reaching a host other than its own provider's (one SDK, two providers; a base URL from the environment) | Each provider's client gets a one-host allowlist from the catalog; a model host is on the worker allowlist only with its key (DeepSeek also only with `CLASSBEAT_DEEPSEEK_ENABLED`); environment variables refused; the egress guard covers the new clients (ADR 0012 §2) | M3c |
| T81 | User data, and Spotify-derived metadata that Spotify's Developer Policy §III 14 forbids sending to an AI model, reaching a provider the owner has not accepted (OpenAI or DeepSeek; the owner's risk acceptance covers Claude only, ADR 0010) | Non-Anthropic providers are reachable only through a route; OpenAI and DeepSeek ship with `data_policy: owner_pending`, so a route can be neither set nor used until the owner's decision flips it in a reviewed change with a dated ADR 0010 line; `resolve_route` re-checks the policy, key and DeepSeek setting before every stage; eval runs use synthetic data only; the same allowlisted `model_input` and planted-value test for every adapter; `store=False` at OpenAI (ADR 0012 §5, §6) | M3c |
| T82 | A model enabled for users without passing the suite (a code bug or agent adding a result writer, a route at a failing or mismatched result, a fake-provider result, a stale or failing newer result) | One writer of results, `classbeat.models.store.record_result`, called only by the live command, held by a static guard test over `backend/src/classbeat` (CI fails on any other module naming the table, `SYSTEM_TABLES`' bare key excepted, or reaching `record_result` through any import; `classbeat.connections.record_result` is a different function); no import of result files; the verdict is recomputed when recorded and stored `NOT NULL`; every `CHECK` is NULL-proof (`coalesce(..., false)`): the verdict must be a JSON object whose `passed` is a JSON boolean equal to the row's `passed`, and the provider never `fake`; append-only by privilege (the app role has no `UPDATE`, `DELETE` or `TRUNCATE` on results, kept by the migration's `REVOKE` and a matching line in `10-roles.sql` so a re-run does not grant them back); a composite foreign key (`MATCH FULL`, every column `NOT NULL` on both sides) makes the database refuse a route whose result is not a pass for exactly that provider, model, effort and group, and `RESTRICT` keeps a used result from changing; the exact DDL is in ADR 0012 §5 and was checked against its must-fail cases; `set_route` needs the newest result (by `recorded_at`, then insertion order) of the current suite version and manifest digest to pass with the running prompt versions of its group's stages (it reads no key file: the worker's `resolve_route` refuses a keyless route `model_config`), and a newer failure, other prompt versions, an older suite version or another manifest digest mark a route stale; a job's provider is built only from `resolve_route`'s choice (`RouteModels`, guard test `test_models_jobs_guard.py`). **Not enforced:** a direct `INSERT` by the operator or a doctored run from the worker's shell (operator trusted, accepted risk; a recording role is for M6) (ADR 0012 §5) | M3c |
| T83 | Adversarial or malformed tool output, or provider responses, steering a non-Anthropic model or breaking the loop | Extends T56 and T69: the same stages, allowlists, schemas and limits for every adapter; strict schemas transformed and validated again by the loop; provider stops and errors mapped by type, never by text; the suite's `adversarial` category must pass on every run before a model is enabled (ADR 0012 §3, §4) | M3c |
| T84 | A provider outage or a costly model passing or failing the gate wrongly, or eval runs overspending | Outage runs count as not run and too many give no verdict; the median cost per class is a gate rule; the live command prints its ceiling and needs `--max-usd` and the owner's go-ahead per run (ADR 0012 §4) | M3c |
| T85 | Racing requests (two tabs, a script) reserving more classes than the plan allows | Each reservation locks the user's `quota_periods` row for the month and counts the ledger under that lock, in the job's create transaction; tested with 20 parallel connections (ADR 0011 §3) | M5 |
| T86 | Refund abuse: cancelling or failing jobs late to get the credit back while the model spend stays | The per-job ceiling per kind and a monthly cost cap per plan counted over all runs, released ones included; checked at reserve, approve and revise (ADR 0011 §4) | M5 |
| T87 | Deleting classes to regain quota | Ledger rows survive the class (`job_id` set null); no `DELETE` policy; a committed credit keeps counting (ADR 0011 §1, §3) | M5 |
| T88 | Redeem-code guessing, sharing or double use | 128-bit codes stored as SHA-256 and shown once; rate limits per user and IP; `max_redemptions` by conditional `UPDATE`; one redemption per user and code; one answer for every invalid code; codes can be disabled (ADR 0011 §2) | M5 |
| T89 | A user granting themselves a plan (forged request, injected SQL in the app role) | The plan is resolved server-side from rows; a restrictive policy lets the app role insert only `code` and `trial` entitlements; grants only through `classbeat_maint`; one trial per account by a unique index (ADR 0011 §1, §2) | M5 |
| T90 | A stolen admin session or password used to change users, plans, codes or flags | Admin role only with TOTP within 12 h; step-up (5 min) for every admin write; admin role changes stay CLI-only; every call audited; a router test fails any admin route without the dependencies (ADR 0011 §6) | M5 |
| T91 | The admin path exposing tokens or class content, or the maint credential misused | `classbeat_maint` has column-level `SELECT` on an allowlist (guard 11 with a secret-column registry), read-only transactions unless a write passed step-up, its secret on web and `db-functions` only, one module may use it (AST guard); a planted-value test over every admin response (ADR 0011 §5) | M5 |
| T92 | The audit log altered or deleted to hide an admin action | `admin_audit` has no grant for the app role, `INSERT, SELECT` only for maint, and a trigger refusing updates and deletes except the foreign keys' set-null; the audit row commits with the action, refusals in their own transaction (ADR 0011 §1, §5) | M5 |
| T93 | Cross-site requests or caching of admin pages | CSRF on every admin write, `Cache-Control: no-store`, the SPA's CSP and `frame-ancestors 'none'`, an admin rate limit (ADR 0011 §6) | M5 |
| T94 | A flag loosening a protection: open sign-up, song picking past its kill switch, billing or Swedebeat turned on | Stricter-wins: the database can only tighten what the environment allows; `open` sign-up, billing and Swedebeat can't be switched from the admin (ADR 0011 §7, §8) | M5 |
| T95 | Admin-entered or user text (banner, labels, notes, emails) running as script | Rendered as text only (T20); length limits; no HTML or Markdown (ADR 0011 §7) | M5 |
| T96 | A ledger row changed to undo a commit or a refund | A trigger allows only `reserved → committed/released` and `committed → refunded`, for every role including maint; restrictive policies for the app role; settle and ledger change in one transaction (ADR 0011 §1, §3) | M5 |

## Accepted risks (beta)

- MOWL's API is unofficial and undocumented: MOWL may block an account or change the API
  without notice. The beta (owner and invited friends) uses each tester's own account on the
  owner's risk acceptance; wider use needs MOWL's authorization (ADR 0010).
- Spotify: song picking sends Spotify-derived metadata to Claude, which the Developer Policy
  forbids, and BYOK's fit with the one-Client-ID rule is unconfirmed. Accepted by the owner for
  the beta circle; a developer app or account may be disabled, borne by each tester for their
  own. The beta is free and non-commercial, testers don't play Spotify music publicly in a gym,
  and nothing Spotify-derived is kept once no class refers to it (§III 10, §IV 2, §IV 3.1(a)).
  Public or paid use needs Spotify first (ADR 0010).
- Swedebeat sign-in needs Swedebeat's app key, which the `mowl-workouts` wheel carries and
  which can be extracted from it. AGENTS.md forbids using credentials embedded in third-party
  apps, so classbeat doesn't use that key: Swedebeat features stay behind a disabled flag with
  fakes until Swedebeat authorizes server-side use, and the classbeat image never contains it:
  it installs a `mowl-workouts` wheel built without the bundled key file (M2).
- MOWL connect has a per-email limit (5 per normalized MOWL email per 15 minutes, T43, ADR 0004
  §4) that counts across users: a signed-in user who submits another person's MOWL email five
  times blocks that person's connect for 15 minutes (their own per-user and per-IP slots are
  spent too). It is the trade-off for stopping password guessing through our page, it denies
  nothing but a new connect (an existing connection keeps working), and it needs an invited
  account; revisited at M6 with the other per-account limits.
- Images on GHCR are private; making a package public is an owner decision.
- Per-account denial of service: an attacker who knows a user's email can exhaust the
  per-account sign-in cap from many IPs; the user then signs in from a known device or by OAuth
  for up to an hour (ADR 0003 §9). Revisited at M6.
- No breached-password check during the beta (it needs a new outbound host); revisited at M6
  (ADR 0003 §5).
- GitHub can't force a re-login, so re-authentication through GitHub only proves a live GitHub
  session (ADR 0003 §5).
- A stolen known-device cookie lifts that user's per-account sign-in cap; guessing is then
  limited only by the per-(account, IP) and per-IP limits (ADR 0003 §3, §9).
- Someone who has an admin's password can exhaust the per-account TOTP limit (20/hour) and keep
  the admin out of MFA for that hour (ADR 0003 §9).
- After `admin mfa-reset`, the password stays valid until the owner resets it, so its holder
  could enroll TOTP first; the enrollment mail warns the owner, who runs `mfa-reset` again
  (ADR 0003 §7).
- Users behind one NAT (a gym network, for example) share the per-IP buckets (ADR 0003 §9).
- Disconnecting MOWL is local only (no known revoke endpoint): the token stays valid at MOWL
  until it expires, and older backups keep it wrapped until they age out (ADR 0004 §4).
- During the beta the KEK file lives on the same host as the database; encryption protects
  database dumps and backups, not a compromise of the host itself. Revisited at M6 (ADR 0004).
- After a suspected KEK compromise the wipe removes every stored MOWL and Spotify token and
  mails each affected user, but MOWL tokens can't be revoked and may stay valid at MOWL until
  they expire; users are told to end sessions or change their MOWL password (ADR 0004 §2).
- The worker's outbound allowlist is enforced inside the process; code running in the worker could
  open a socket around it. A network-level egress rule follows at M6 (ADR 0006 §9).
- The worker holds the KEK and the model API key on the same host as the database; revisited at
  M6 with the KEK (ADR 0004).
- An uncertain MOWL attach that the user resolves with **Do it anyway** may leave a second MOWL
  playlist wrapper in their own account; no read can rule it out (ADR 0005 §7, ADR 0006 §3).
- A model turn already running when the user cancels finishes and is billed (ADR 0005 §4).
- Lost `NOTIFY` wake-ups delay progress by up to 5 s and claims by up to 2 s (ADR 0005 §3, §6).
- A model turn that finishes after its worker lost the lease isn't recorded, so a job's cost can
  undercount by one turn per lost lease; a request that fails after billing started returns no
  usage and isn't recorded either, and neither is an attempt the SDK retried silently inside a
  turn, even when the turn then succeeds: at most two uncounted attempts per turn, three for the
  turn that ends a failed attempt (ADR 0006 §6).
- A **Do it anyway** on an uncertain final playlist create, or the next pick superseding an uncertain
  pool create, may leave an empty private playlist in the user's own Spotify; no read can rule it out
  until the listing delay is measured, and classbeat never writes to it (ADR 0008 §2).
- The pool playlist keeps the last candidate tracks in the user's own Spotify until the next pick
  overwrites it; classbeat never deletes a user's playlist (ADR 0008 §8).
- The PKCE verifier of a Spotify flow is stored in plaintext for at most 10 minutes, as ADR 0003's
  sign-in flows do; it is useless without the one-time code (ADR 0009 §3).
- Spotify's Developer Terms and Policy risks of ADR 0010 apply to song picking in full (BYOK, track
  metadata to a model, business use); accepted by the owner for the beta circle only.
- The model eval gate trusts the operator: someone with database or worker access can write a result
  row or run the recording command with a doctored suite. It protects against agents and mistakes, not
  against the owner's own infrastructure (ADR 0012 §5).
- An OpenAI route would send users' briefs and Spotify-derived track metadata to OpenAI as they go to
  Anthropic. Spotify's Developer Policy §III 14 forbids sending Spotify Content to an AI model; the
  owner's acceptance of that breach (ADR 0010) covers Claude only. It is **not accepted** for OpenAI or
  DeepSeek until the owner answers ADR 0012 questions 1 and 2; until then their catalog entries are
  `owner_pending` and no route can use them. If accepted, `store=False` keeps data out of stored
  responses, but the provider's own abuse-monitoring retention still applies.
