# ADR 0022: Account emails (milestone ME, #649)

Status: Accepted (the owner approved the plan on 2026-11-05; #658, issue #651, merged after a
`PASS`; ME1 to ME5 are merged and their e2e specs land with the PR that closes #649, 2026-11-07). The plan, revision 3, is
[docs/plans/account-emails.md](../plans/account-emails.md); where the two differ, fix the plan
or amend this ADR in the PR that changes it.

Numbering: the plan and issues #649 and #651 call this "ADR 0018". That number was taken by
[ADR 0018](0018-mowl-running-classes.md) (and Amendment 12 by the M8 amendment), so this is the
next free ADR number and roadmap Amendment 15.

Related: [ADR 0003](0003-accounts-and-sessions.md) (accounts, §8 key derivation),
[ADR 0005](0005-jobs-and-leases.md) (jobs, leases, fencing),
[ADR 0014](0014-account-export-and-deletion.md) (export, deletion, guard 12),
[ADR 0016](0016-swedebeat-music.md) (B2 verify job).

## Context

Every mail classbeat sends is bare `text/plain`: 11 builders in `auth/mails.py`, sent by `Mailer`
in `mail.py` with no Date, Message-ID or display name. The two-factor notice says "contact the
operator". Some account events send nothing. The owner wants branded mail, admin control of
branding and of which optional mails are sent, user preferences with unsubscribe, new account
notices and a "class ready" mail.

## Decision

**1. Registry and categories.** `classbeat/emails/registry.py` has one entry per kind: `id`,
`category`, `essential`, `user_can_opt_out`, `admin_can_disable`, `enabled_by_default`, builder
and sample params. The admin page, account preferences, preview list and send gate all read it;
a test fails if an entry lacks a field, golden file, sample params or catalogue strings.

| Category | Kinds | Switchable |
|---|---|---|
| essential | signup confirm, provider signup confirm, password reset, email-change confirm, account exists, email-change address taken, connections wiped, email changed (to the **old** address) | never, by admin or user |
| welcome | welcome | admin and user |
| account activity | password changed, 2FA set up, failed 2FA attempts, sign-in method added / removed, 2FA turned off, account deleted | admin and user |
| class ready | class ready | admin and user |

"Email changed (old address)" stays essential: that address is no longer the account's, so no
preference applies, and it is the only signal of a takeover.

One send path: `emails.send(kind, recipient, params, *, prefs=None)`: admin switch on? then user
preference on? (essential kinds skip both and never look up a user) then render with live
branding then `send_logged` once. An optional kind uses the `prefs` passed in, or looks them up by
recipient; with no account found it is not sent. Account deleted is sent with the `prefs` read
before the cascade, so the path never reloads the deleted user.

**2. Rendering and catalogue.** Templates are code, not admin-editable and without a template
engine: a block content model (`Heading`, `Paragraph`, `Button`, `Note`, `Bullets`, `EventTime`)
that one layout renders to HTML and text with the same text and links, escaped in one place. A
structural URL check (parsed with `urllib.parse`) accepts links only on the normalized
`public_base_url` (scheme, host, port, no userinfo) and a `mailto:` equal to the support address.
All strings, subjects included, are in a catalogue (`emails/locales/en.py`, ids as an `Enum`);
`mail_locale(user)` returns `"en"` and is the one place multilingual support (milestone ML,
#657) changes. Every mail is `multipart/alternative` (text, then HTML), table-based with inline
styles, system fonts, no remote fonts, tracking pixel or script, readable with images off.

**3. Branding.** The admin sets sender display name, support address and an optional plain-text
footer (settings keys, audited by key and length only). The From **address** is
`CLASSBEAT_SMTP_SENDER` and is never admin-editable. The logo is a hosted PNG
(`frontend/public/email/logo.png`) served with `Cross-Origin-Resource-Policy: cross-origin` on
`/email/*` only. The support address replaces "the operator" as `mailto:` and `Reply-To`.
**Default support address:** `CLASSBEAT_MAIL_SUPPORT_ADDRESS` defaults to `support@classbeat.eu`
(a bare address; the admin setting overrides it). This is an owner-confirmable default: the
mailbox must exist before deploy, and changing it is a setting, not a code change.

**4. Fire-and-forget delivery.** One SMTP attempt per mail; failures are logged by error class
only; no retries, outbox, tracking or delivery history. A mail not arriving is not an
application failure and never undoes the change that caused it.

| Where the event happens | How the mail is sent |
|---|---|
| Web request | FastAPI `BackgroundTasks`, after the commit; one mail per committed change |
| Worker ("class ready") | a hand-off row in `mail_handoff`, inserted in the fenced transaction that finishes a job which ran a **fresh** mix analysis (unique `(kind, job_id)`, so a stale or retried attempt adds nothing); the web process takes rows with one `SECURITY DEFINER` function (`FOR UPDATE SKIP LOCKED`, deletes on take, drops rows older than a day), woken by `LISTEN mail_handoff` and a 30 s poll, only when SMTP is configured; then the normal send path (admin switch, user preference, class still the user's) |

The worker never sends mail itself and never holds SMTP settings. The hand-off row carries the
class id only; RLS by owner; cascade on account deletion; guard 12 entry, not exported. A cached
mix sends nothing. The worker also skips the insert when the admin switch or the user's preference
already has "class ready" off (the web side checks both again before sending).

The take function is `SECURITY DEFINER` with `SET search_path = pg_catalog, pg_temp` and `EXECUTE`
for the app role only, and is added to the queue digests. Retention: a purge of rows older than a
day is done by that same function, not by a plain app-role `DELETE` (RLS is forced on the table).
With SMTP not configured, an hourly task in the web process calls it in a purge-only mode (limit 0).
`mail_handoff` also has a guard 10 retention entry, so the table cannot grow without SMTP.

ME5 (#656) owns "class ready" end to end for both producers: design jobs and the already merged
`swedebeat_verify_mix` verify job of ADR 0016 B2 (#384). ME5 adds one enqueue helper and edits that
verify job to call it only when it ran a fresh mix analysis, with a test (fresh analysis: one row;
cached or retried: none). ME5 depends on ME1 (#652), A4 (#380) and **ME4 (#655)** for the user
preference it checks.

As built by ME5 (#656; these refine the text above, they change no guarantee): the unique key is
`(owner_id, kind, job_id)` (owner-inclusive, as every owned table's must be; a job has one owner,
so it is the same key) and `(owner_id, job_id)` references `jobs` with `ON DELETE CASCADE`, so a
row goes with its job, its class and the account. "Ran a fresh analysis" is `jobs.fresh_analysis`,
set in the fenced transaction that stores the analysis, so the attempt that finishes the job sees it
even when a retry found the map cached; an attempt that ends any other way commits no row, and a
"Check again" on an analysed mix (a new job, cached) adds none. `classbeat_queue` gets `SELECT`,
`UPDATE` and `DELETE` on `mail_handoff`: `UPDATE` only because `SELECT ... FOR UPDATE SKIP LOCKED`
requires it; the function's pinned body never updates. The web side reads the recipient's address
by the owner's id when it sends (never stored in the row), and skips a disabled account.

**5. Preferences and unsubscribe token.** `users` gets `mail_unsubscribed_all` and
`mail_opted_out` (categories), in the GDPR export. Optional mails carry a link
`<base>/unsubscribe#token=…` and `List-Unsubscribe` / `List-Unsubscribe-Post: List-Unsubscribe=One-Click`
(`POST /api/mail/unsubscribe?token=…`, no session). The token carries its own data: URL-safe
base64 of `user id | category or "all" | expiry (90 days)` followed by an HMAC-SHA256 over those
fields under an HKDF key derived from `app_secret_key` with a new info string (ADR 0003 §8, a
`mail-unsubscribe` entry added there by ME4). It can only turn that category or "all" **off**,
never sign in or turn anything on. GET never changes anything (link scanners); the page POSTs.
The route is exempt from the Origin check, rate-limited per IP, checks the HMAC in constant time
and the expiry, and answers the same for an unknown user. The token is in a query string (RFC 8058), so ME4 adds
the route to ADR 0003 §10's never-logged list and to the log redactor, and notes in the SWAG
runbook that the reverse proxy logs request lines. Rotating `app_secret_key` invalidates
outstanding links; the account page still works.

**6. Admin switches.** `mail_kinds_disabled` lists kind ids; only `admin_can_disable` kinds are
accepted. Preview and "Send test to me" bypass the switch and preferences but use the normal
renderer and SMTP send. The preview is shown in a sandboxed iframe; see the CSP amendment below.

**CSP amendment (amends ADR 0013 §9).** Context: the app's CSP has `frame-src 'none'` and
`frame-ancestors 'none'`, and `SecurityHeaders` sets the CSP on every response. A same-origin
preview iframe on the admin page needs two changes. Decision:

- App pages change `frame-src 'none'` to the path-scoped `frame-src <public_base_url>/api/admin/mail/`
  (a CSP host source with a path ending in `/` matches that prefix, the way ADR 0013 §9 scopes
  `img-src`; `'self'` cannot carry a path). Only that prefix can be framed.
  `frame-ancestors 'none'` stays for every response except the preview.
- `SecurityHeaders` does not overwrite the CSP of a response from the exact route
  `GET /api/admin/mail/{kind}/preview` that already carries its own CSP. A 401, 403, 404 or 500
  from that route still gets the default CSP. The preview response keeps its own: `default-src 'none'; style-src 'unsafe-inline'; img-src <base>/email/;
  frame-ancestors 'self'; sandbox`, plus `X-Content-Type-Options: nosniff` and
  `Cache-Control: no-store`.
- ME2 (#653) implements both and tests them, including the four error cases above; every other
  response keeps `frame-ancestors 'none'`.

Alternatives considered: (1) plain `frame-src 'self'`, simpler but lets any page frame any
same-origin URL; rejected for the path-scoped source above; (2) a `srcdoc` iframe, which still
needs `frame-src`; (3) no iframe, a text-only or download preview, which cannot show the layout the
admin is designing; (4) a separate origin for previews, too heavy for one admin page. Residual
risk: the CSP ignores paths after a redirect (the host is still checked), and the app has no HTML
injection path (ADR 0013); the preview route is admin-only, its sandbox has no `allow-scripts`, and
all content in it is escaped by the one renderer.

**7. The one intended trigger change.** On Google, GitHub or Microsoft **account creation**, the
welcome mail (naming the provider) replaces the "sign-in method added" notice, which that flow
sends today. Every other existing trigger and subject is unchanged.

ME3 (#654) implements it. Both creation paths (the provider callback and the e-mail hand-off
confirmation) queue `welcome` with the provider and no longer send `identity_linked`; an explicit
link from the account page still sends `identity_linked`, now through the gated path. The six
notices (`welcome`, `password_changed`, `account_deleted`, `identity_unlinked`, `totp_removed`,
and the rewritten `identity_linked`) are queued as background tasks after the commit
(`emails/notices.py`), one mail per committed change and none for a no-op or refused request; a
send failure is logged by error class only. `account_deleted` takes the address and preferences
before the cascade and passes them as `prefs`. A password reset that also removes linked sign-in
methods sends only `password_changed` (the page that lists them shows what was removed). Preferences
are read in one function, `notices.read_prefs`, from ME4's columns (`mail_unsubscribed_all`,
`mail_opted_out`; #655); it leaves out the user id, so the deletion notice carries no unsubscribe
link (the account is gone, and the link would change nothing).

## Alternatives

- **An outbox with retries and delivery history**: rejected by the owner; more tables, a worker
  loop and failure modes for a notice that is allowed to be lost.
- **The worker sends the mail**: needs SMTP settings and branding in the worker and breaks the
  one send path.
- **A Postgres `NOTIFY` payload instead of a table**: lost if the web process is down.
- **Admin-editable templates or a template engine**: injection and review surface; blocks plus
  branding fields give control with little risk.
- **A stored unsubscribe token**: a table to expire and purge; the signed token needs none.
- **Per-recipient opt-out of essential mail**: would block sign-in and takeover signals.

## Consequences

- Every mail changes appearance; golden files pin both parts.
- Two new user columns, one table (`mail_handoff`), four settings keys, one HKDF info string.
- "Class ready" may be lost if the web process is down for over a day or SMTP fails; accepted.
- Multilingual mail is a later change at `mail_locale` and the catalogue.
- MH (help and contact) is a placeholder and is not planned here.
