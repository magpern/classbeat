# ADR 0033: Invite requests and invite codes that carry a tier (PB-7, #985)

Status: Accepted (owner request 2026-11-12: "while sign-up is invite-only, a visitor can request
an invite code ... an admin reviews the requests and, in the admin UI, issues an invite code with
a tier of the admin's choosing"; issue #985). Amends ADR 0011 section 1 ("an invite never carries
authority beyond an account") and ADR 0025 section 1 (a fifth Turnstile route). Real mail to real
people stays gated by the owner's SMTP setup.

## Context

Sign-up is invite-only (`CLASSBEAT_SIGNUP_MODE=invite`, ADR 0003 section 5). Today an invite comes
only from the CLI or the admin's Codes screen, so a stranger who hears of classbeat has no way in.
The owner wants a "request an invite" form and, in the admin area, a list of requests where the
admin issues an invite **with a tier** (a plan for some days) or declines.

What exists and must not be weakened:

- Invites (`invites`, ADR 0003): 16 random bytes, only the SHA-256 stored, shown once, consumed
  inside the account-creating transaction by a conditional `UPDATE` (`use_invite`) on all three
  paths that create an account (password confirmation; provider callback; provider hand-off).
- Redeem codes (ADR 0011 section 2): plan, days, uses; `POST /api/account/redeem` runs the
  conditional count (`redeem_codes_guard` lets the app role only add one use to a live code), the
  one entitlement writer of the app role (`plans.entitle.add_entitlement`, `source = 'code'`) and
  the `code_redemptions` row, in one `user_session`.
- The admin gate (ADR 0011 section 6): admin role, TOTP within 12 h, step-up within 5 min for
  writes, rate limit, one `admin_audit` row per call whose `params` never hold a secret or code.
- Turnstile on anonymous forms that cost us something (ADR 0025), the rate-limit mechanism
  (ADR 0003 section 9), the 64 KiB body cap of every route (SR-01).

## Decision

### 1. Data (migration `0047_invite_requests`)

`invite_requests`, a **system table** (there is no account yet, so no `owner_id`, no row
security; `db.tables.SYSTEM_TABLES`): `email` (as typed, at most 254), `email_normalized`,
`heard_from` (at most 300 characters), `instructor` (boolean), `locale` (the page's language),
`state` (`pending`, `issued`, `declined`), `plan_id` and `grant_days` (the tier, both or neither,
only when issued), `invite_id` (→ `invites`, set null), `decided_by` (→ `users`, set null),
`decided_at`, `created_at`. A partial unique index on `email_normalized WHERE state = 'pending'`
makes **one open request per address**.

`redeem_codes.invite_id` (→ `invites`, set null, unique; a check keeps such a code at one use):
the link from an invite to the tier it carries. Nothing is added to `invites` or to the M1 table
definitions.

**Who can read it.** The app role (`classbeat_app`) may `INSERT` and `DELETE` requests and read
only `id`, `state`, `created_at` and `decided_at` (what the pending count and the purge need):
it can never read an address or the text, whatever the query (column grants, guard 2).
`classbeat_maint` reads every column and may update only the decision columns (guard 11). So the
table is protected by the role grants rather than by row security: the only reader of what a
visitor wrote is the audited admin path.

### 2. The public request: `POST /api/auth/invite-request`

Body: `email`, `heard_from` (optional), `instructor` (strict boolean), `locale` (any value; an
unoffered one is ignored), `captcha_token`. Order, as sign-up's (ADR 0025 section 2):

1. body cap (64 KiB, every route) and Origin/CSRF (middleware); body shape: a plausible single
   address, `heard_from` at most 1200 characters raw, then its whitespace folded to single spaces
   and at most 300 left, with no control, format (bidi, zero-width), surrogate, private-use or
   unassigned character (`auth.invite_request_policy`). A 422 never echoes the input (the route
   is under `/api/auth/`).
2. **Turnstile**, action `invite_request` (the guard test's `TURNSTILE` table).
3. Buckets, every request counts: **3 per normalized address and hour, 5 per IP (IPv6 /64) and
   hour** (`invite-request` limits; the mechanism keeps windows of at most an hour).
4. The effective sign-up mode (`flags.effective.signup_mode`: the environment's mode from
   `CLASSBEAT_PUBLIC_SIGNUP` and `CLASSBEAT_SIGNUP_MODE`, ADR 0032 section 7, and the database,
   stricter wins): anything but `invite` (public sign-up open, or closed) is 403
   `requests_closed` (the mode is public anyway, `GET /api/settings/public`).
5. Store, unless the address has a pending request (the unique index) or `PENDING_CAP` (1000)
   requests are pending (a warning line with the count, never the address).

The answer is always **202 `{"status": "received"}`**: new, duplicate, over the cap, or an
address that already has an account (the route never reads `users`). No mail is sent: a mail
would let anyone make us write to any address. The text is shown to the admin as text, never
rendered as HTML, and never reaches a model, a log line or an audit row.

### 3. The admin API (`/api/admin/invite-requests`, on the admin gate)

- `GET ?state=pending|issued|declined&offset=&limit=` (`invite_request.list`, params: the filter
  and page): newest first; each request with `has_account` (whether the address is already an
  account's; only the admin sees this) and the issued invite's state (`active`, `used`,
  `expired`).
- `POST /{id}/approve` `{plan_id?, grant_days?, expires_days = 14 (1 to 30), send_email = false}`
  (`invite_request.approve`, params: those four, never the code or the address). One maint
  transaction, the request's row locked first (409 `already_decided`; 404 `not_found`; 422
  `unknown_plan`; a half tier is a 422): the invite (`auth.create_admin_invite`, the CLI's code
  and hashing), and with a plan its tier: one `redeem_codes` row, `max_redemptions = 1`, the same
  `expires_at` as the invite, `label = 'Invite request'`, `invite_id` set, and a `code_hash` of
  16 random bytes **nobody is ever shown** (so it cannot be typed at `/api/account/redeem`); the
  request becomes `issued`. Answer 201 `{code, request, email}`: the code is in this response
  only, as with `POST /invites`.
- `POST /{id}/decline` (`invite_request.decline`, no params): 409 `already_decided` once decided.
- *Amendment of 2026-11-12 (#989).* The list answer also carries `pending`, the size of the whole
  pending queue, so the screen shows it against the cap from one page. `POST /decline`
  `{ids}` (1 to 100 ids, nothing else) declines each request as `POST /{id}/decline` does, in its
  own audited transaction: one `invite_request.decline` row per request, its id as the target, no
  params. A request that is gone or decided meanwhile is skipped (its `refused` row says why) and
  does not undo the others; the answer is `{declined, skipped}`. This answers the pending cap
  filled by someone who can solve Turnstile (finding 5 of the #987 review).

### 4. One code, the tier granted through the redeem path

The requester types **one code**, the invite's, at sign-up (password or provider). It passes the
invite gate unchanged. Where the invite is consumed and the account created, in the same
transaction (all three paths: `signup_routes._create_account` and
`provider_account.create_provider_account`, used by the callback and the hand-off),
`auth.invite_tier.grant_invite_tier` looks up the code tied to the invite (`FOR UPDATE`), sets
`app.user_id` to the new account for those statements (transaction-local, through a bare context
`auth.deps.invite_tier_context`, named only there, `test_user_context_guard.py`), and runs the
redeem path's own steps 2 to 4 (`plans.redeem_steps.take`): the guarded count update, the
entitlement through `add_entitlement` (`source = 'code'`, `ends_at = now() + grant_days`), the
`code_redemptions` row. Then `app.user_id` is cleared again. **There is no second entitlement
writer**, and the database's own checks (the restrictive insert policy, `redeem_codes_guard`)
apply as to any redeem.

**Where the shared steps live.** Steps 2 to 4 moved out of `plans.redeem` into
`plans.redeem_steps`, which opens no session and is the one module that calls `add_entitlement`
for a code (the T89 import guard lists it in place of `plans.redeem`). `plans.redeem` keeps the
hash lookup and the `user_session` of `POST /api/account/redeem` and is still imported only by
the plan routes (the session guard). `plans.redeem_steps` may be imported only by `plans.redeem`
and `auth.invite_tier` (`test_session_guard.py`). The sign-up path never imports a module that
opens a user session.

If the code can no longer be redeemed (an admin disabled it, or it expired with its invite), the
sign-up is refused as `invite_invalid` and rolls back whole: no account, the invite unused, the
pending row kept. An account never exists without the tier its invite promised, and the tier is
granted once (the invite is consumed once and the code has one use). A plain approve (no plan)
is an ordinary invite. In `open` mode the invite field is not read, so no tier applies; requests
are not taken in that mode.

*Amendment of 2026-11-12 (#989).* A refusal the redeem steps raise (`AlreadyRedeemed`,
`TermsRefused`) is a refusal too: the steps run in a savepoint, so the grant answers `False` and
the sign-up rolls back as `invite_invalid` instead of failing with a 500, and `app.user_id` is
cleared in a `finally`. Because a mode change would leave issued tier invites unhonoured without
a word, `GET /api/admin/settings` reports `read_only.unused_tier_invites` (issued with a plan,
neither used nor expired) and the Settings view warns while the effective sign-up mode is not
`invite` and that count is above zero. It also reports `read_only.mail_configured` (a boolean,
never an SMTP detail), so the approve form offers to email the code only when mail can go out.

### 5. Delivery

By default the admin copies the code to the requester by hand. With `send_email` and mail
configured (`CLASSBEAT_SMTP_HOST` set), the `invite_code` mail (essential kind: there is no
account, so no preference or admin switch; en, sv and da strings and golden files) goes to the
requested address in the request's language after the commit, through the one send path
(`emails.live`); the answer says `queued`. Mail off: nothing is sent and the answer says
`mail_off`. A failed send is logged by error class only. `MailMessage` hides its body, so the
code never reaches a log.

### 6. Retention

The hourly purge (`auth.purge`) deletes a decided request **30 days after the decision** (the
invites' own retention; an invite lives at most 30 days, so it ends first) and a pending one
**90 days after it was made**. The pending cap bounds the table meanwhile. Purging an invite sets
`redeem_codes.invite_id` and `invite_requests.invite_id` to null; the code row stays, as every
redeem code does.

## Alternatives

- **A tier column on `invites`, applied by a new writer at sign-up.** A second way to write
  entitlements, outside the redeem code's guard and count; rejected by the issue.
- **Two codes for the requester (an invite and a redeem code).** Works without touching the
  sign-up transaction, but the owner asked for one code, and a user who never redeems the second
  one silently gets no tier.
- **Grant the tier after the sign-up commits, in its own `user_session`.** Simpler isolation, but a
  failure between the two transactions leaves an account without its tier and a consumed invite.
- **Row security on `invite_requests`.** There is no owner to match; the column grants say more
  precisely who may read what.
- **Answer differently for a duplicate or a known address.** An enumeration oracle; rejected.

## Consequences

- Amends ADR 0011 section 1: an invite may now carry a tier, through a code of the redeem path,
  and the sign-up transaction (M1, security-critical) calls that path. Reviewed as auth,
  entitlements and abuse (Opus).
- A fifth Turnstile route (ADR 0025 section 1) with its own action; the frontend (PB-8) renders
  the widget with action `invite_request`.
- New reason codes for the frontend's sentences (en, sv, da): `requests_closed` (public form),
  `already_decided`, `unknown_plan` (admin).
- An address someone else typed is stored for up to 90 days (pending) or 30 days after the
  decision. The privacy notice should name this (legal drafts, owner).
- The Codes list shows these single-use codes with the label "Invite request"; disabling one there
  makes its invite refuse sign-up.
- Mail to real people is sent only when the owner has configured SMTP and an admin asks for it.
