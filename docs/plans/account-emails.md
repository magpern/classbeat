# Plan: milestone ME "Account emails" — revision 3

Revision note (2026-11-05): the ADR is numbered 0022 and the roadmap amendment 15, because ADR 0018
and Amendment 12 were already taken by M8 (running classes).

Revision 3 follows the owner's re-review (2026-11-05): fire-and-forget delivery everywhere, a
minimal worker→web hand-off instead of an outbox, admin toggles per email type, user
preferences with unsubscribe, and one central registry of email kinds.

## Context

Every email classbeat sends is bare `text/plain`: 11 builders in
`backend/src/classbeat/auth/mails.py`, sent by `Mailer` in `backend/src/classbeat/mail.py`
(`set_content(body)`, no Date/Message-ID, bare From). The 2FA notice looks unprofessional and says
"contact the operator". Some account events send nothing.

Owner decisions (2026-11-05):
- New milestone **ME**; **MH Help and contact** added as a placeholder, not planned. Multilingual
  support is a later milestone the owner adds; ME is English, every string via a catalogue.
- Templates in code; admin edits branding and switches email types on/off; hosted PNG logo;
  support address (mailto + Reply-To) replaces "the operator".
- New emails: password changed, welcome, account deleted, sign-in method removed, two-factor
  turned off, and **"class ready"** when a design job or the B2 verify job (ME5 owns both
  producers) that ran a fresh mix
  analysis finishes (cached mix: none).
- **Delivery is fire and forget**: one SMTP attempt, failures logged (error class only), no
  retries, tracking or delivery history. A mail not arriving is not an application failure.

## The email registry (central, ME1)

`backend/src/classbeat/emails/registry.py`: one entry per kind with `id`, `category`, `essential`,
`user_can_opt_out`, `admin_can_disable`, `enabled_by_default`, builder and sample params (for
preview). Admin UI, account preferences, preview list and the send gate all read it; a test fails
if a kind lacks any field, a golden file, sample params or catalogue strings.

| Kind | Category | Essential |
|---|---|---|
| signup confirm, provider signup confirm, password reset, email-change confirm, account exists, email-change address taken, connections wiped (incident, with reset link), email changed (to the **old** address) | essential | yes: never switched off by admin or user |
| welcome | welcome | no |
| password changed, 2FA set up, failed 2FA attempts, sign-in method added / removed, 2FA turned off, account deleted | account activity | no |
| class ready | class ready | no |

"Email changed (old address)" stays essential: that address is no longer the account's, so no
preference applies, and it is the only signal of a takeover. Account deleted is sent before the
row is gone, using the preference read before deletion.

**One send path** for every mail: `emails.send(kind, recipient, params, *, prefs=None)` → admin
switch on? → user preference on? (essential skips both) → render with live branding →
`send_logged` once. **The user context is optional**: essential kinds never look up a user (sign-up
confirmations and "account exists" have no account or must not depend on one); an optional kind
uses the `prefs` passed in, or looks them up by recipient when an account exists, and with no
account found it is not sent. A test pins that essential kinds make no preference lookup.

## Issues (one PR each, own worktree, fresh Reviewer, merge gate)

Order: ME0 → ME1 → ME2, ME3, ME4 (any order) → ME5 (also needs A4 #380).

### ME0 `docs: plan account emails (ADR 0022, Amendment 15)` — Architect, Opus reviewer
- `docs/roadmap.md` Amendment 15 and ME/MH rows; `docs/status.md` rows.
- `docs/adr/0022-account-emails.md`, short: registry and categories, rendering and catalogue,
  branding, fire-and-forget delivery (web: `BackgroundTasks` after the commit; worker: hand-off
  table), preferences and unsubscribe token, admin switches. ADR 0016's B2 row gets a note that
  ME5 adds the "class ready" enqueue to its verify job.

### ME1 `feat(mail): branded multipart mail layout and email registry` — Sonnet dev, Opus reviewer
- Keep `classbeat/mail.py` in place (callers unchanged). `MailMessage` gains `html` and `locale`;
  `Mailer._build` makes `multipart/alternative` (text then HTML, UTF-8, no attachments) with
  `From` = display name + `CLASSBEAT_SMTP_SENDER` (address never admin-editable), `Reply-To`
  support address, `Date`, `Message-ID`, `Auto-Submitted: auto-generated`, `Content-Language`, and
  `List-Unsubscribe` / `List-Unsubscribe-Post` on optional kinds (from ME4; absent until then).
- Package `backend/src/classbeat/emails/`:
  - registry above and the single send path;
  - **block content model, no template engine**: `Heading`, `Paragraph`, `Button`, `Note`,
    `Bullets`, `EventTime`, rendered by one layout to HTML and text (same text, same links), all
    text escaped in one place;
  - **structural URL check**: links parsed with `urllib.parse`, must match the normalized
    `public_base_url` on scheme, host, port, no userinfo; `mailto:` must equal the support address;
  - **catalogue** `emails/locales/en.py`: ids as an `Enum`, subjects included with today's exact
    text; `mail_locale(user) -> "en"` is the one place i18n changes; `format_when` for times (UTC);
  - **layout**: table-based, inline styles, 600 px card, system fonts, bulletproof button in brand
    `#5b4bdb` (`emails/theme.py`), preheader, footer (site link, support mailto, why-you-got-this,
    optional admin footer line as escaped plain text, unsubscribe link on optional kinds). No
    remote fonts, tracking pixel or script; readable with images off.
- Rewrite the 11 builders on it; **triggers and subjects unchanged**; bodies get a heading, one
  button, "wasn't you?" with the support address; security notices show the event time.
- Logo: `frontend/public/email/logo.png` (2x, tile + wordmark, `alt="classbeat"`) rendered once
  from an SVG source by a Playwright script in `scripts/`; the web process's static route
  (`web.py` `_add_single_page_app`; this repository has no Caddyfile and the SWAG config stays
  untouched) answers `/email/*` only with `Cross-Origin-Resource-Policy: cross-origin` and a long
  cache.
- `CLASSBEAT_MAIL_SUPPORT_ADDRESS` setting (default support mailbox, bare address).
- Tests: `smtpfake` `.text`/`.html`; MIME structure; golden files
  `backend/tests/mail_golden/<kind>.{txt,html}` with a regenerate script; same links in both parts;
  escaping; URL check (`https://classbeat.eu.attacker.example`, userinfo, port, `javascript:`);
  registry/catalogue completeness. e2e: one assertion that the HTML part references the logo.

### ME2 `feat(admin): mail branding, notification switches, preview and test send` — Sonnet dev, Opus reviewer
- Settings keys (migration on `app_settings_key` like `0016_swedebeat_flag.py`;
  `classbeat/flags/core.py` parsers + `validate_for_write`; branches in `admin/settings.py`;
  audit: key + length or kind ids, never text):
  `mail_sender_name` (display name ≤ 60, no control chars/CR/LF), `mail_support_email` (bare
  address, overrides the env default), `mail_footer` (plain text ≤ 280, banner rules),
  `mail_kinds_disabled` (list of kind ids; only `admin_can_disable` kinds accepted).
- `GET /api/admin/mail` (kinds from the registry with enabled state),
  `GET /api/admin/mail/{kind}/preview?part=html|text` (production renderer, sample data, token
  `SAMPLE`; headers: CSP `default-src 'none'; style-src 'unsafe-inline'; img-src <base>/email/;
  frame-ancestors 'self'; sandbox`, `nosniff`, `Cache-Control: no-store`),
  `POST /api/admin/mail/{kind}/test` (to the admin's own address, 10/hour, audited). Preview and
  test send **bypass the admin switch and user preferences** (a disabled kind can still be
  designed and tested) but use the normal renderer and SMTP send.
- **Middleware exemption** (ADR 0022 §6, amends ADR 0013 §9): `SecurityHeaders` must not replace
  the CSP of a response from the exact route `GET /api/admin/mail/{kind}/preview` that already carries
  its own; a 401, 403, 404 or 500 there still gets the default CSP. App pages get the path-scoped
  `frame-src <public_base_url>/api/admin/mail/`. Tests pin all four error cases, the preview's
  own CSP, and that `frame-ancestors 'none'` holds for every other response.
- Admin page "Mail": Branding cards (sender name, support address, footer), Notification types
  (a switch per optional kind; essential ones shown locked "always sent"), preview in
  `<iframe sandbox src=…>` with HTML/text toggle, "Send test to me".
- Tests: the usual admin authorization checks on every route; a disabled kind is not sent;
  essential kinds cannot be disabled.

### ME3 `feat(auth): new account notices` — Sonnet dev, Opus reviewer
- Password changed (signed-in change and completed reset), welcome (after sign-up confirmation;
  on Google/GitHub/Microsoft account creation it names the provider and replaces the "sign-in
  method added" notice for that case — the one intended trigger change, in ADR 0022), account
  deleted (address and preferences read before the cascade and **passed into the background task
  as `prefs`**, so the send path never reloads the deleted user), sign-in method removed,
  two-factor turned off.
- Rules: send after the commit through the one send path; a failure is logged and never undoes the
  change; one mail per committed change (a request that changes nothing sends none); no token,
  password or account detail in any notice.
- Tests per trigger with `SmtpFake`; golden files.

### ME4 `feat(account): email preferences and unsubscribe` — Sonnet dev, Opus reviewer (signed token)
- Preferences on `users`: `mail_unsubscribed_all` (bool) and `mail_opted_out` (categories);
  GDPR export entry (guard 12). Account page section "Email": "Unsubscribe from all optional
  emails" plus a switch per category (welcome, account activity, class ready), with a note that
  essential emails (sign-in, reset, confirmation links) are always sent.
- Unsubscribe without sign-in: optional mails carry a link `<base>/unsubscribe#token=…` and the
  `List-Unsubscribe` one-click header (`List-Unsubscribe-Post: List-Unsubscribe=One-Click`,
  `POST /api/mail/unsubscribe?token=…`, which mail providers call without any session). The
  **token carries its own data**: URL-safe base64 of `user id | category or "all" | expiry
  (90 days)` followed by an HMAC-SHA256 over those fields under a key derived from
  `app_secret_key` (ADR 0003 §8); the server decodes the fields, checks the HMAC in constant time
  and the expiry. It can only turn that category or "all" **off**, never sign in or turn anything on. The page asks
  "Unsubscribe from <category>" / "from all" and POSTs; GET never changes anything (link scanners).
  The route is added to ADR 0003 §10's never-logged list and to the log redactor, and noted in
  the SWAG runbook (the proxy logs request lines). The one-click route is exempt from the Origin check, rate-limited per IP, and answers the same
  for an unknown user; an expired or bad token says "sign in to manage your emails".
- Tests: token tamper/expiry/other-user, opt-out respected per category and for all, essential
  still sent, GET does nothing.

### ME5 `feat(jobs): "class ready" mail via a worker hand-off` — Opus dev (fenced job write), Opus reviewer
Depends on ME1, ME4 (the preference it checks) and A4 (#380).
ME5 owns the whole "class ready" feature for both producers: the table, the worker insert, the web
consumer and the mail. It exposes one enqueue helper, used by design jobs and by the already merged
`swedebeat_verify_mix` verify job (ADR 0016 B2, #384), which ME5 edits to enqueue only when it ran a
fresh mix analysis.
- Table `mail_handoff(id, owner_id FK users ON DELETE CASCADE, kind, job_id, params jsonb
  (class id only), created_at)`, unique `(kind, job_id)`, RLS by owner, guard 12 entries (not
  exported; cascade).
- The worker inserts the row (`ON CONFLICT DO NOTHING`) in the fenced transaction that finishes a
  job that ran a fresh analysis (recorded on the job during the attempt), so a stale or retried
  attempt adds nothing extra. It skips the insert when the admin switch or the user's preference
  already has "class ready" off (cheap reads in the same transaction); the web side still checks
  both again just before sending.
- Retention: the purge of rows older than a day is the `SECURITY DEFINER` take function itself
  (RLS is forced, so no plain app-role `DELETE`); with SMTP not configured an hourly web task calls
  it purge-only (limit 0). `mail_handoff` also has a guard 10 retention entry.
- The web process takes rows with one `SECURITY DEFINER` function
  (`SET search_path = pg_catalog, pg_temp`, `EXECUTE` for the app role only, added to the queue
  digests): `DELETE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED LIMIT n) RETURNING …`, which
  also drops rows older than a day. Taking a row discards it; then the one send path runs (admin
  switch, user preference, class still the user's) and makes one attempt. Woken by
  `LISTEN mail_handoff` and a 30 s poll, only when SMTP is configured. Account deletion's cascade
  removes untaken rows.
- Tests: fresh analysis → one mail; cached mix → none; retried attempt → one row; two web
  consumers never send the same row; disabled by admin or user → none; class deleted → none;
  the verify job: fresh mix analysis → one row, cached mix or retried attempt → none; rows older
  than a day are purged with SMTP unset.

## Done when (milestone ME)
- Every mail is multipart with the branded layout, golden-tested; existing triggers and subjects
  unchanged except the documented OAuth-creation welcome; readable with images off.
- Admin can set branding, switch optional kinds off, preview every kind and send a test to
  themselves; essential kinds can't be switched off.
- Users can opt out per category or of all optional mail, signed in or from the mail's link.
- "Class ready" arrives once per qualifying job on the fakes.
- No test sends real mail (SmtpFake, Mailpit, netguard). ADR 0022 accepted.

## Verification
- Per PR: `cd backend && uv run ruff check && uv run pyright && uv run pytest` (throwaway Postgres
  per AGENTS.md); `cd frontend && npm run lint && npm run typecheck && npm test`.
- Visual: worktree stack, trigger sign-up, reset, 2FA enrolment; Mailpit UI
  (`http://127.0.0.1:40000+<issue>`): HTML, text, the "HTML Check" tab, images off.
- e2e: `accounts.spec.ts`, ME2 admin spec, an unsubscribe spec, `mu-pages.spec.ts`.
- Real inboxes: the owner uses "Send test to me" after deploy; agents never mail real people.
