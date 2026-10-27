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
| T1 | Account takeover by OAuth email matching (an attacker's provider account with the victim's email) | Identity key is `(provider, subject)`, never email; linking needs a signed-in confirmation; unverified emails never link | M1 |
| T2 | Login CSRF, OAuth state or code injection | State, nonce (OIDC) and PKCE; single-use, short-lived callbacks bound to the browser session | M1 |
| T3 | Credential stuffing and brute force | argon2id; rate limits per IP and account; email verification; invite-only beta | M1 |
| T4 | Session theft or fixation | Server-side sessions; HttpOnly, Secure, SameSite=Lax cookie; rotation on login; idle and absolute expiry; CSRF tokens on writes | M1 |
| T5 | Cross-tenant read or write (IDOR), including through a background job | `UserContext` everywhere, repository filters, Postgres RLS (ADR 0002), guard tests, 404 tests for foreign IDs | M0 contract, M2 |
| T6 | Token theft from a database dump or backup | Envelope encryption, AES-256-GCM with AAD `user_id\|provider`, keys outside the database, `key_id` rotation; encrypted backups | M2, M6 |
| T7 | Secrets in logs, errors or traces | `SecretStr`; secrets only from files; no exception text in API responses; log redaction and tests that grep logs for planted tokens | M0, M2 |
| T8 | Prompt injection through upstream content steering the agent into writes or leaks | The model has no write tools; per-stage tool allowlists; writes only by the backend after the user's approval and server-side revalidation; tool output marked as data; tokens never sent to a model | M3a, M4 |
| T9 | A model-supplied ID that belongs to another user | Tool handlers resolve IDs within `ctx`'s rows only; tests | M3a |
| T10 | Writes to a user's accounts they didn't approve | Write categories (§2); approval carries the revision ID and content hash; only the latest list for the class; managed playlists by ID with an ownership check | M3b, M4 |
| T11 | Duplicate or half-done external writes after a crash | `external_operations` with idempotency keys; `uncertain` writes are reconciled and never retried automatically | M3b |
| T12 | A stale worker committing after losing its lease | Leases with heartbeat; fencing token (`lease_version`) checked on every write | M3a |
| T13 | Cost exhaustion: many or huge jobs, or a runaway agent loop | Invite-only beta; quotas reserved before work; per-job limits on turns, tool calls, time and cost; per-user concurrency limit; rate limits | M3a, M5 |
| T14 | A Spotify OAuth callback connecting the wrong account or Client ID | State and PKCE verifier stored server-side, bound to user, Client ID and redirect URI, single-use; mismatch rejected | M4 |
| T15 | Admin account compromise | TOTP MFA mandatory; step-up for sensitive actions; admin routes 403 for others; audit log | M1, M5 |
| T16 | Admin misuse | Admins never see tokens; every admin action audited; admin reads through the separate audited role | M5 |
| T17 | Compromised dependency, base image or Action | Lockfiles; exact versions; checksum-verified tool downloads; pip-audit, npm audit, Trivy; Dependabot; least-privilege workflow `permissions`; no `pull_request_target`; `persist-credentials: false` | M0 |
| T18 | An agent committing a secret or weakening a check | gitleaks on the whole history in CI; fresh-Opus review gate; ruleset with required checks; agents can't bypass | M0 |
| T19 | SSRF through user-supplied links (playlists, mixes) | Parse provider IDs only, never fetch user URLs; outbound allowlists for worker (MOWL, Spotify, Swedebeat, model APIs) and web (sign-in providers, Spotify OAuth, MOWL and Swedebeat sign-in, mail relay) | M3a, M4 |
| T20 | XSS through user, model or upstream text in the SPA | React escaping only, no raw HTML rendering; strict CSP | M1, M6 |
| T21 | Exposed services or container breakout | Non-root image; local ports bound to 127.0.0.1; only Caddy public on the VPS; Postgres never published there | M0, M6 |
| T22 | Keeping data longer than allowed, or no way to delete it | GDPR export and delete; retention of Spotify-derived data per track F answers | F, M6 |
| T23 | A MOWL or Swedebeat password captured or kept | Typed on our page over TLS, used once for a token, never stored, logged, put in a URL or sent to a model | M2 |
| T24 | Email abuse: sign-up spam, reset flooding | Invite codes; rate limits on sign-up, verification and reset mail | M1 |
| T25 | Playlist links shown to other users | Never displayed outside the owner's pages; privacy wording explains what Spotify's non-public setting means | M4 |
| T26 | SQL injection: the app role can set `app.user_id` itself, so injected SQL defeats both the code checks and RLS, and could hold locks on owned tables | Only bound parameters and the ORM; no SQL built from strings (ruff's SQL-injection rule on); the app role has no `TRUNCATE`, `MAINTAIN` or ownership (ADR 0002). Locks can't be prevented for a role with `UPDATE`/`DELETE`; `lock_timeout`, `statement_timeout` and `idle_in_transaction_session_timeout` are role defaults of the app role and limit accidents, but injected SQL could lower them (residual denial-of-service risk) | M0, every PR |
| T27 | A stolen but still valid session changing the account's identities, email, password or connections | Recent authentication (10 min) required for those actions (§3) | M1 |
| T28 | Taking over the first admin through `ADMIN_EMAILS` with an unverified email | The bootstrap grants admin only to an account whose email is verified, then requires TOTP setup | M1 |
| T29 | Shared music analysis leaking one user's sources to another | Reuse only after an access check with the requester's own credentials; the analysis holds no playlist links or owner data (§6) | M2, M4 |
| T30 | `NOTIFY` payloads read by any listener on the database | Payloads carry only IDs; data is read afterwards in a user session (ADR 0002 §5) | M3a |
| T31 | Prompt injection of the development agents through issue, PR or upstream text | Agents treat repository and web content as data; the review gate and ruleset apply to every merge; secrets are never in the repository or agent prompts | M0 |
| T32 | User data sent to model providers beyond need | Only the class brief, the music data track F allows, and the draft are sent; no tokens, emails or account data; provider data-retention terms checked before enabling a provider (§5) | M3a, M3c |

## Accepted risks (beta)

- MOWL's API is undocumented and may change or be refused; the beta uses each tester's own
  account and wider use needs MOWL's authorization (track F).
- Spotify BYOK is not confirmed compliant for a hosted service; live use waits for the
  permission assessment (ADR 0010).
- Swedebeat sign-in needs Swedebeat's app key, which the `mowl-workouts` wheel carries and
  which can be extracted from it. AGENTS.md forbids using credentials embedded in third-party
  apps, so classbeat doesn't use that key: Swedebeat features stay behind a disabled flag with
  fakes until Swedebeat authorizes server-side use, and the classbeat image never contains it:
  it installs a `mowl-workouts` wheel built without the bundled key file (M2).
- Images on GHCR are private; making a package public is an owner decision.
