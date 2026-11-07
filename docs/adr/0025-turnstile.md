# ADR 0025: Cloudflare Turnstile on sign-in, sign-up, the provider-email step and the reset request (#721)

Status: Proposed. Accepted when this PR merges after an Opus `PASS` (first review of a new
security design). Turnstile ships **off** in code defaults and is switched on in production only by
the last PR of the split (§13), after the owner has created the widget.

Related: [ADR 0003](0003-accounts-and-sessions.md) §4 (Origin and CSRF), §5 (the flows), §9 (rate
limits, client IP), §10 (logging), §11 (fakes); [ADR 0006](0006-agent-runs.md) §9 and `egress.py`
(outbound allowlists); [ADR 0013](0013-ui-design-night-studio.md) §9 (CSP, no third-party hosts);
[ADR 0019](0019-passkeys.md) (passkeys, not on `main`); [ADR 0020](0020-image-only-install.md)
(release assets and install checks); [ADR 0023](0023-multilingual.md) (locales, sentence codes).
Amends ADR 0003 §5 (order of checks in four routes) and §9 (one new bucket); the pointers go into
ADR 0003 with the code (PR 1).

## Context

Owner decision (2026-11-07, #721): Cloudflare Turnstile as the captcha on sign-up and sign-in; the
contact form follows in its own issue. Today every anonymous auth route is protected by Origin and
CSRF checks, argon2 with a bounded pool, per-account and per-IP fixed-window limits (ADR 0003 §9) and
the invite gate. Two residual risks are open: anyone who knows an address can **exhaust its
per-account sign-in cap and its reset-mail bucket** from many IPs (T3 residual), and the anonymous
mail-sending routes can be scripted against arbitrary addresses within their limits (T24).

Facts checked against Cloudflare's documentation (`cloudflare/cloudflare-docs`, `src/content/docs/
turnstile`, commit of 2026-11-07; the docs site itself was not reachable from the agent's network):

- Siteverify: `POST https://challenges.cloudflare.com/turnstile/v0/siteverify`, JSON or form body,
  fields `secret`, `response` (required), `remoteip`, `idempotency_key` (UUID, for safe retries;
  both optional). A token is at most 2048 characters, **valid 300 s, and single use**; a second
  validation answers `timeout-or-duplicate`. The answer has `success`, `challenge_ts`, `hostname`,
  `error-codes`, `action`, `cdata` (and `metadata.ephemeral_id`, Enterprise only). Error codes:
  `missing-input-secret`, `invalid-input-secret`, `missing-input-response`,
  `invalid-input-response`, `bad-request`, `timeout-or-duplicate`, `internal-error`.
- CSP: `script-src https://challenges.cloudflare.com` and `frame-src https://challenges.cloudflare.com`
  (or a nonce with `strict-dynamic`); `connect-src 'self'` only for pre-clearance, which we do not use.
- Widget: explicit rendering (`api.js?render=explicit`), options `action` (≤ 32 of `[A-Za-z0-9_-]`),
  `cData`, `theme` (`auto`/`light`/`dark`), `language` (`sv` and `da` are supported), `size`
  (`normal` 300 px, `flexible` ≥ 300 px, `compact`), `appearance`, `execution`, `refresh-expired`,
  `feedback-enabled` (default true), `tabindex`; `turnstile.reset()` and `remove()`. Turnstile is
  stated to be WCAG 2.2 AA compliant.
- Test keys: site keys `1x00000000000000000000AA` (pass), `2x00000000000000000000AB` (fail),
  `3x00000000000000000000FF` (interactive); secrets `1x…AA` (pass), `2x…AA` (fail), `3x…AA`
  (already spent), plus invisible site keys `1x00000000000000000000BB` and `2x00000000000000000000BB`.
  All follow one pattern: a digit, `x`, then zeros and a two-letter suffix. They produce
  `XXXX.DUMMY.TOKEN.XXXX` but **still load `api.js` from Cloudflare and still call siteverify**:
  they need the network on both sides, so they cannot run in CI or e2e. With a test secret,
  siteverify answers `hostname: "example.com"` and no `action`, so they cannot pass §4's checks.
- Storage: Cloudflare's mobile guide says the widget "relies on" cookies and local storage to keep
  state; the privacy addendum lists IP, TLS fingerprint, User-Agent, site key and origin as signals,
  calls Cloudflare a **processor** for providing Turnstile and a **controller** for improving its bot
  detection, and defers cookie details to Cloudflare's cookie policy. We therefore claim nothing
  about Cloudflare's storage beyond "it may use cookies or local storage in its own frame".
- Free plan: unlimited challenges and verifications, 10 hostnames per widget.

## Decision

### 1. Which routes require a token

| Route | Turnstile | Why |
|---|---|---|
| `POST /api/auth/login` | **Yes**, action `signin` | Password guessing, credential stuffing, per-account cap exhaustion |
| `POST /api/auth/signup` | **Yes**, action `signup` | Sends mail to any address; invite-code probing; hashing load |
| `POST /api/auth/oauth/pending/email` (provider-email step) | **Yes**, action `signup_email` | Sends mail to any address the client names |
| `POST /api/auth/password/reset/request` | **Yes**, action `reset` | The main mail-flooding route (T24) and the reset-bucket exhaustion of T3 |
| `POST /api/auth/oauth/{p}/start` (`sign_in`, `sign_up`) | No | Touches no account or mailbox: it stores a 10-minute flow row, per-IP limited; the provider's own sign-in is the human check; an invite code is 16 random bytes, so the start is no useful oracle. The mail-sending step after it is covered (`pending/email`) |
| `POST /api/auth/signup/confirm`, `password/reset/info`, `password/reset/complete`, `email/confirm` | No | The 32-byte mailed token is the proof; a second check adds friction and nothing else |
| Signed-in routes (`reauth`, MFA verify and step-up, `email/change`, `password/change`, link and reauth starts) | No | The session already passed Turnstile at sign-in; out of scope (#721) |
| `GET /oauth/{provider}/callback` | No | Single-use flow taken atomically with state, PKCE and nonce (ADR 0003 §5); the start that made the flow is per-IP limited and the provider checked the person |
| `POST /api/mail/unsubscribe`, `POST /api/mail/unsubscribe/confirm` | No | Signed token, changes only that mail preference |
| Passkey sign-in (ADR 0019, not on `main`) | No | An assertion cannot be guessed; revisit only on abuse |

A **guard test** walks the app's routes and lists **every route reachable without a session that
changes state**, anywhere in the app: every unsafe method, plus `GET /oauth/{provider}/callback`
(it creates accounts and sessions with a safe method). Each must be either in the Turnstile set or
in an exempt list with its reason. It is an allowlist: a new anonymous route, the coming contact
form included, fails the test until it is classified.

### 2. Order of checks

In each of the four routes, unchanged steps keep their order; Turnstile is inserted as follows:

1. Body cap, Origin and CSRF (middleware, unchanged).
2. Body shape (Pydantic) and checks that depend only on the body (email syntax, the password rule
   of sign-up). A new optional field `captcha_token: str | None` with **no** Pydantic length limit
   is part of the shape (the body cap still bounds the request). A missing or overlong token is
   **not a 422** but the generic failure of step 4, so every failure looks the same.
3. **Per-IP captcha bucket** (§6), reserved before any network call.
4. **Token check**: empty, over 2048 characters or not printable ASCII → `captcha_failed` without a
   network call; otherwise siteverify (§4).
5. Everything else, in today's order: the account lookup, known-device check and the existing
   buckets (sign-in), sign-up mode and invite (sign-up), the pending cookie (provider email), the
   mail buckets and account lookup (reset), argon2, session creation, mail.

So no database read about an account, no account-scoped bucket, no argon2 slot, no mail and no
session happens before a valid token. The Turnstile answer is therefore **independent of whether
the account exists** (T33 holds by construction), and a script without tokens can no longer spend a
victim's per-account sign-in cap or reset-mail bucket (the T3 residual shrinks to an attacker who
pays for a solved challenge per attempt). Turnstile is an extra check: no existing limit, the invite
gate or the "no permanent lockout" rule changes.

The check is an explicit call at the top of each handler (not a decorator), and a test per route
uses a recording fake verifier and a spy session factory. It shows that the verifier runs before the
route's first database session (the only earlier database write is the limiter's own reservation of
the captcha bucket in step 3), and that a failing token leaves no `auth_events`,
`rate_limits` row outside the
captcha bucket, `pending_signups` row, mail or cookie.

### 3. Answers and errors

- Any failed verification (missing, malformed, invalid, expired, reused, wrong `hostname`, wrong
  `action`, `success` not literally `true`): **400 `{"reason": "captcha_failed"}`**.
- Siteverify unreachable, timed out, a non-2xx, unparsable JSON, `internal-error`, the global
  concurrency limit of §4 reached, or a misconfiguration on our side (`missing-input-secret`,
  `invalid-input-secret`, `bad-request`): **503
  `{"reason": "captcha_unavailable"}` with `Retry-After: 30`**. The distinction tells the user to
  wait rather than retry the challenge; it carries nothing about accounts.
- Both are new sentence codes (ADR 0023 §6) with en, sv and da text: "The security check did not
  pass. Please try again." / "The security check is not reachable right now. Try again in a minute."
- Logging: one `WARNING` line `captcha refused: action=… why=…`, where `why` is one fixed word
  (`missing`, `malformed`, a Cloudflare error code from the list above, `hostname`, `action`,
  `stale`, `timeout`, `http_<status>`, `bad_json`). Never the token, secret, idempotency key, IP or
  body. No `auth_events` row: no account was tried. A misconfiguration answer
  (`invalid-input-secret`, `missing-input-secret`, `bad-request`) is logged at `ERROR`, since it
  means every protected request is refused until the owner fixes the secret.

### 4. Verification

- **Our own checks on a `success: true` answer:** `hostname` equals the host of
  `CLASSBEAT_PUBLIC_BASE_URL`, which in production is `https://${CLASSBEAT_DOMAIN}`
  (`compose.prod.yaml`; today the beta host `classbeat.pernemark.se`; a later `.eu` domain is not
  decided). A domain change therefore needs the new host on the widget first (owner step 1).
  `action` equals the route's action (a token solved on the reset page cannot sign in);
  `challenge_ts` parses and is not older than 300 s nor more than 60 s in the future
  by our clock (defence in depth; Cloudflare already enforces expiry). `cdata` is not used.
- **Single use and replay:** Cloudflare enforces it (`timeout-or-duplicate`); we keep no token store.
  Two parallel requests with one token: Cloudflare accepts one. The fake verifier (§8) enforces
  single use too, so tests cover replay.
- **Timeouts and retries:** one total deadline of **5 s** per verification (connect 3 s, body read
  capped at 8 KiB). At most **two attempts** inside that deadline, the second only after a connect
  error, a **read timeout** (the request may have arrived and its answer been lost), a 5xx or
  `internal-error`, and both with the same `idempotency_key` (a fresh UUID4 per verification, never
  logged), so a retry after a lost answer cannot turn a success into `timeout-or-duplicate`. A 4xx
  or any other error code is not retried. Then 503.
- **Global concurrency:** at most **16** siteverify calls in flight per web process (a semaphore);
  a request that cannot get a slot within 1 s gets 503 `captcha_unavailable`. The per-IP bucket of
  §6 does not bound many source addresses holding 5 s calls open at once.
- **`remoteip`:** sent, set to the client address from the trusted-proxy reader of ADR 0003 §9
  (full address, not the /64 bucket). It lets Cloudflare tie the token to the solver's address
  (token relaying from solver farms). Privacy: Cloudflare already sees that address twice, as our
  proxy and from the widget; no new category of data leaves us. It is omitted when the reader gives
  no usable address.
- **Sent to Cloudflare from our server:** secret, token, `remoteip`, `idempotency_key`. Never an
  email, password, invite code, account id or cookie. Nothing from the answer is stored.

### 5. Cloudflare unavailable: fail closed

Fail closed (503, §3), no automatic degrade. Reasons: (a) any automatic bypass is the path an
attacker aims at (a slow or failing siteverify, a blocked widget); (b) **if** the owner runs the site
proxied by Cloudflare (an owner choice, `vps-deploy.md` §8), an outage of Cloudflare's edge mostly
takes the site down anyway and a degrade buys little; if the site is DNS-only, an outage stops new
sign-ins while the site stays up, which (c) and (d) make acceptable for the beta; (c) signed-in
users keep their sessions (up to 30 days), so an outage stops only new sign-ins, sign-ups and reset
requests; (d) the rate limits stay as a second line. The **bounded
degrade is human**: the owner sets `TURNSTILE_ENABLED=false` and restarts web (runbook, §12); the
app then logs an `ERROR` at start and a `WARNING` on every protected request, and every other check
still runs.

### 6. Rate limit

New row in ADR 0003 §9: **Captcha verification, per IP, 60 / 15 min, every request counts** (a
success too; nothing is released), `Limit("captcha", "ip", 60, 900)`, reserved at step 3. It caps
our outbound siteverify calls per client and sits above the existing per-IP sign-in limit (30
failures / 15 min) so a shared gym NAT is not hit first by it. 429 with `Retry-After` as usual.

### 7. Settings and secrets

- `CLASSBEAT_TURNSTILE_ENABLED` (bool, **default false** in `Settings`, so dev, CI and tests run
  without it); `compose.prod.yaml` passes `${TURNSTILE_ENABLED:-false}` from PR 1 and
  `${TURNSTILE_ENABLED:-true}` from PR 3 (details under "Which file sets what" below).
- `CLASSBEAT_TURNSTILE_SITE_KEY` (public, environment). Given to the SPA by `GET /api/auth/session`
  as `captcha: {"site_key": "…"}` when enabled, `null` when off; nothing is baked into the bundle.
- Secret file **`turnstile_secret_key`** (`SECRET_FILE_FIELDS`, never the environment, `SecretStr`,
  never logged), mounted on **web only** (never the worker), mode 0640 group 10010 like the others.
  Compose refuses to start when a `file:` secret is missing, so **PR 1** also adds the empty
  placeholder: in `scripts/worktree.sh`, in the placeholder loop of `vps-deploy.md` §5, and in
  `check-prod-compose.sh`, `test-install-check.sh` and the release assets (ADR 0020). A release cut
  between PR 1 and PR 3 then still installs.
- `CLASSBEAT_TURNSTILE_FAKE` (bool, default false): uses the fake verifier and the fake widget (§8)
  instead of Cloudflare; no site key or secret file is needed then.
- **Start-up rules** (in `Settings` validation, so web refuses to start):
  - enabled, not fake, and no site key or an empty secret file → refuse;
  - **`environment == "prod"` and `turnstile_fake` true → refuse**, the same convention as the fake
    model provider (`check_worker_model_provider`, `config.py`: "`fake` never in prod") and
    whatever `TURNSTILE_ENABLED` says, so a fake can never be armed in prod;
  - `environment == "prod"` and a site key or secret matching the dummy pattern
    `^[0-9]x0{16,}[A-Z]{2}$` (today 20 zeros in a site key, 31 in a secret) → refuse. It is a
    pattern, not a list, so dummy keys Cloudflare adds later are refused too;
  - `environment == "prod"` with Turnstile off → start, logged at `ERROR` (the human degrade of §5).
- **Which file sets what:**
  - `Settings` defaults: enabled false, fake false (dev stack, CI, backend tests);
  - `compose.yaml` (dev stack): nothing, so off;
  - `compose.e2e.yaml`, service `web` (profile `e2e`, run as `compose.yaml` plus
    `compose.e2e.yaml`; `CLASSBEAT_ENVIRONMENT` stays `dev` there):
    `CLASSBEAT_TURNSTILE_ENABLED: "true"` and `CLASSBEAT_TURNSTILE_FAKE: "true"`, next to the other
    e2e-only switches;
  - `compose.prod.yaml`, service `web`: `CLASSBEAT_TURNSTILE_ENABLED: ${TURNSTILE_ENABLED:-false}`
    (PR 1), `:-true` (PR 3), and `CLASSBEAT_TURNSTILE_SITE_KEY: ${TURNSTILE_SITE_KEY:-}`. It never sets
    `CLASSBEAT_TURNSTILE_FAKE`, and `check-prod-compose.sh` refuses the name in it and, from PR 3,
    requires the `:-true` default.

### 8. Tests, fakes and netguard

- **Unit and integration:** the verifier sits behind a small interface on `app.state`; tests inject
  an in-process fake (pass, fail with each error code, wrong hostname or action, stale
  `challenge_ts`, timeout, 5xx then success with the same idempotency key, reused token). The real
  client is also tested through an `httpx.MockTransport`. `netguard` stays on; nothing is marked
  `allow_network`.
- **E2E:** `compose.e2e.yaml` (profile `e2e`) sets both switches for `web` (§7). The session then
  returns `captcha: {"fake": true}` and the SPA renders
  a local stand-in (a labelled checkbox) instead of loading `api.js`; it yields
  `fake-pass-<random>` (accepted once) and a test can force `fake-fail`. The e2e covers each of the
  four forms, the generic error, the reset after a failure and the unavailable message. Cloudflare's
  test keys are **not** used in CI or e2e (they need the network, see Context), and they cannot
  pass the `hostname` and `action` checks of §4 anyway; local manual testing uses the fake.

### 9. Egress

A new client in `egress.py`, `turnstile_async_client()`, built like the Swedebeat catalogue client:
`httpx.AsyncClient`, allowlist `{"challenges.cloudflare.com"}` (https, port 443), on **no urllib
list**, `trust_env=False`, no redirect followed, no default headers, a total deadline, **no
argument** (a transport only through a `turnstile_test_client` the guard refuses inside
`classbeat`), and it refuses to run in the worker. In production the call leaves through the
existing `web-out` network; the network-level egress rule stays an M6 item (accepted risk list).

### 10. CSP and other headers

- Only when Turnstile is enabled with a real site key (not fake), `headers.py` adds
  `https://challenges.cloudflare.com` to `script-src` and to `frame-src` (next to the mail-preview
  prefix). Nothing else changes: no nonce, no `unsafe-inline`, no `connect-src` change (no
  pre-clearance). The CSP is per document and the SPA is one document, so the source is allowed on
  every route once enabled; the script itself is loaded only on the four pages (§11).
- swag's headers (COOP and CORP `same-origin`, `Permissions-Policy`) do not concern a cross-origin
  iframe or script; none changes. `Referrer-Policy: no-referrer` stays page-wide; the owner's live
  check (§12) confirms the widget does not need a referrer (open question 2 if it does).

### 11. Frontend

- **Where:** a `Turnstile` component used by `AuthScreen` (sign-in, sign-up), `ProviderEmailPage`
  and the request step of `ResetPage`. It inserts `https://challenges.cloudflare.com/turnstile/v0/
  api.js?render=explicit` once, on first mount of one of those pages (never in `index.html`, never on
  other routes), renders with `turnstile.render` and calls `turnstile.remove` on unmount. A loaded
  script stays in the document until the next full load; it renders nothing elsewhere.
  `no-third-party-hosts.test.ts` gets one exemption: that exact URL string in that one file.
- **Options:** site key from the session; `action` per §1; `language` = the active app locale
  (`en`, `sv`, `da`), re-rendered when it changes; `theme` = the resolved app theme (`light` or
  `dark`, not `auto`, because the app's own switch can differ from the system), re-rendered on
  change; `size: "flexible"`, checked at 320 px wide, `compact` below the width that fits;
  `appearance: "always"`, `execution: "render"`, `refresh-expired: "auto"`,
  `feedback-enabled: false` (no feedback reports to Cloudflare), no `cData`.
  `response-field: false`; the token goes into the JSON body as `captcha_token`.
- **Behaviour:** the submit button is disabled until a token arrives, with a visible reason linked by
  `aria-describedby` ("Checking that you are a person…"); after **any failed submit** the widget is
  reset, because a sent token is spent; the error, expired and unsupported callbacks show our own
  text in a `role="status"` region; if `api.js` does not load (blocked, offline) the page says the
  security check could not load and to allow `challenges.cloudflare.com` or try another browser.
  There is no way around the check from the page. The provider buttons stay usable without a token
  (§1).
- **Accessibility:** the widget sits in the form, before the submit button in tab order (iframe
  `tabindex` 0), inside a labelled group; all our messages are translated (en, sv, da) and
  announced; Cloudflare states WCAG 2.2 AA for the widget itself.

### 12. Privacy, cookie notice, threat model, runbook

- **Privacy page** (en, sv, da; the `privacy.processors.*` and `privacy.cookies.*` keys of
  `frontend/src/i18n/legal.{en,sv,da}.ts`, as landed in #720 and #717), processors: a new item:
  "On the sign-in, sign-up, password reset
  and email pages, Cloudflare Turnstile checks that a person, not a program, is using the form. Your
  browser loads it from Cloudflare, which sees your IP address, browser and device signals; our server
  sends Cloudflare the check's result token and your IP address, never your email or password.
  Cloudflare may also use the signals to improve its bot detection. It is based in the US."
  Cookies section: "The security check runs in a frame from challenges.cloudflare.com and may use
  cookies or local storage there for the check only; classbeat sets none for it." Legal basis:
  legitimate interest in securing accounts (wording with the owner, open question 1).
- **Cookie notice:** the text names "and a security check from Cloudflare on the sign-in pages";
  it stays informational (no consent prompt), subject to open question 1.
- **Threat model:** new **T126** (automated abuse of the anonymous auth forms: credential stuffing
  at scale, per-account cap and reset-bucket exhaustion, mail flooding, invite probing; mitigation
  this ADR; residual: paid human or AI solvers, token relaying limited by `remoteip`, a Cloudflare
  outage blocks new sign-ins, Cloudflare sees visitor signals). T3, T24 and T33 cite it; the T3
  residual and the matching accepted risk are narrowed; the trust-boundary diagram gains
  `browser ── challenges.cloudflare.com` and `web ── siteverify`. Accepted risks gain: fail closed on
  a Cloudflare outage; Cloudflare as controller for bot-detection improvement.
- **Runbook** (`vps-deploy.md` §5 and §8, `prod.env.example`): the owner steps below. The empty
  secret placeholder is PR 1's (§7); PR 3 adds the widget, site key and live-check steps.

### 13. The split

| PR | Scope | Developer / Reviewer | Depends on |
|---|---|---|---|
| 1 `feat(auth): verify Turnstile tokens on sign-in, sign-up and reset` | §1 to §10 backend: settings and secret file with its empty placeholder (compose, `worktree.sh`, the `vps-deploy.md` §5 placeholder loop, prod and install checks, release assets), the fake gate of §7, egress client, verifier and fake, the four routes and the order tests, the route guard, the captcha bucket, CSP sources, `captcha` in the session, the two sentence codes (en, sv, da), ADR 0003 pointers, threat model T126. Prod default stays **off** | Opus / Opus | this ADR |
| 2 `feat(web): Turnstile widget on the sign-in, sign-up and reset forms` | §11: the component, the four pages, the fake stand-in, the host-guard exemption, strings en, sv, da, e2e for the four forms on the `compose.e2e.yaml` stack (profile `e2e`) | Opus / Opus (a third-party script on the auth pages) | 1 |
| 3 `docs: privacy, cookie notice and runbook for Turnstile; on in prod` | §12: privacy page and cookie notice en, sv, da, runbook and `prod.env.example` (widget, site key, live check), `compose.prod.yaml` default **on** and its `check-prod-compose.sh` assertion. Merged only after the owner's live check of step 4 below passed | Sonnet / Sonnet, then one final Opus review (it flips a security default) | 1, 2 |

### Owner-only deploy steps (agents do not touch the Cloudflare dashboard)

1. Cloudflare dashboard → Turnstile → add a widget for the **host name of the real site domain**,
   the value of `CLASSBEAT_DOMAIN` on the server (today `classbeat.pernemark.se`), and any other host
   the owner serves the site on; no `localhost`. Mode **Managed**, pre-clearance **off**. When the
   domain changes (for example to a `.eu` domain), add the new host to the widget **before** changing
   `CLASSBEAT_DOMAIN`, then remove the old one.
2. Put the site key in the server `.env` as `TURNSTILE_SITE_KEY`; write the secret key to
   `secrets/turnstile_secret_key` (`<deploy user>:10010`, mode 0640).
3. Deploy a release that contains PR 1 and PR 2 **with `TURNSTILE_ENABLED=true` set explicitly** in
   `.env` (the default is still off), before PR 3 flips the default.
4. Live check, reporting only pass or fail and `why` words: sign in, sign up with an invite, request
   a reset, finish a provider-email step; switch to sv and da and to dark and light; the browser
   console shows no CSP violation and no `110200` (domain or referrer, open question 2);
   `docker compose logs web` shows no `captcha refused … why=` line for those attempts. If anything
   fails, remove `TURNSTILE_ENABLED=true` and restart web; agents fix it before PR 3.
5. Once step 4 passes, PR 3 merges; deploy its release (the explicit setting can then go).
6. Emergency: `TURNSTILE_ENABLED=false`, `docker compose up -d web`; switch back once Cloudflare
   recovers.

## Alternatives

- **hCaptcha or reCAPTCHA:** the owner chose Turnstile; reCAPTCHA also sets Google cookies.
- **Turnstile on OAuth starts:** rejected (§1): no account or mail effect, and the provider checks
  the person; it would mean two challenges.
- **Turnstile after the rate-limit reservations:** rejected: tokenless scripts would still spend a
  victim's per-account buckets, which is the main gain here.
- **Fail open, or allow N unverified requests per window during an outage:** rejected (§5).
- **A local token store for replay:** unnecessary; Cloudflare enforces single use and we do not
  retry without an idempotency key.
- **Not sending `remoteip`:** loses token-relay detection for no privacy gain (§4).
- **Cloudflare's test keys in e2e:** rejected; they need outbound network from the browser and the
  backend.
- **A nonce-based CSP:** not needed; the app has no inline script and host sources are enough.
- **`appearance: interaction-only`:** fewer visible widgets but a submit button that waits without
  a visible reason; `always` gives clearer, accessible feedback.

## Consequences

- One new outbound host for web (`challenges.cloudflare.com`), one new secret file, two settings,
  one rate-limit row, two sentence codes, threat T126.
- New sign-ins, sign-ups and reset requests depend on Cloudflare; signed-in use does not.
- Every failed submit costs the user one more (usually invisible) check.
- The contact-form issue reuses the verifier with its own action and the same route guard.

## Open questions for the owner (defaults the PRs build to)

1. **Legal wording:** is "legitimate interest" and "strictly necessary for security" (no consent
   prompt, cookie notice stays informational) acceptable, given Cloudflare names itself controller
   for improving bot detection? Default: yes, with the wording of §12.
2. **Referrer:** if the live check shows the widget fails (`110200`) under `Referrer-Policy:
   no-referrer`, may the policy become `strict-origin` page-wide? That would also send our origin with the
   Spotify cover requests and make the privacy page's "we send no referrer" untrue, so it needs that
   sentence changed too. Default: no change until seen.
3. **Widget mode:** Managed (a checkbox only for suspicious visitors). Non-interactive or invisible
   are alternatives. Default: Managed.
