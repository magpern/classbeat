# Sign-up and cost controls (PB-5, #976)

Design: [ADR 0032](../adr/0032-paddle-on-fakes.md) §7. This is the report the ADR asks for (what
already protects sign-up and model cost, checked against `main` at `a8d0577`) and what PB-5 changed.
Public sign-up stays **off**: opening it is the owner's step (M7b-real).

## What already protects sign-up

| Control | Where | What it does |
|---|---|---|
| Sign-up mode | `CLASSBEAT_SIGNUP_MODE`, `app_settings.signup_mode`, `flags.core.stricter_signup` | `invite` by default; the database can tighten (`closed`), never open. `closed` is the emergency brake and also stops confirmation (ADR 0011 §7, FL1). |
| Invite gate | `signup_routes.valid_invite`, `oauth_routes`, `oauth_outcomes` | In `invite` mode a valid, unused, unexpired invite is needed; it is consumed only when the account is created. |
| Email proof | `pending_signups`, `signup/confirm` | No account exists until the address is proven (24 h token). A password sign-up confirms with the chosen password; a provider sign-up needs a verified provider address or the hand-off mail, in the browser that started it. Same answer for a new and an existing address. |
| Turnstile | `auth/captcha.require_captcha` (ADR 0025) | On sign-up, the provider-email step, sign-in and reset, before any bucket, lookup, argon2 or mail. Fails closed (503). Fake and Cloudflare test keys refused in prod. Off in prod only by the owner's human degrade, logged at start and on every request. |
| Rate limits | `auth/ratelimit.py` (ADR 0003 §9) | Sign-up 3/h per normalized address and 5/h per IP; captcha 60/15 min per IP; confirmation counts as a sign-in attempt (per IP and pair). |
| Abuse view | `admin/abuse.py` | Top users by request rate, 429s, sessions seen from several networks, with a one-click disable that revokes every session. |

## What already bounds cost per account

| Control | Where | Default |
|---|---|---|
| Plan limits, new accounts on `free` | `plans`, `quota.ledger` | Free: 3 classes per month, 3 revisions per class, 1 concurrent job, monthly model cost cap seeded at $10. |
| Environment bounds over every plan | `quota.bounds` | `CLASSBEAT_CLASSES_PER_MONTH_MAX` 200, `CLASSBEAT_MONTHLY_COST_MICROS_MAX` $300, `CLASSBEAT_JOBS_PER_USER` 3, revisions 25, class minutes 180. |
| Per-job ceilings | `config_agent` | `CLASSBEAT_AGENT_JOB_COST_MICROS` $1.50, `CLASSBEAT_SONG_JOB_COST_MICROS` $3, per-turn token reservations. |
| Save caps | `saving.caps` | 30 per hour, 100 per day per user (smaller per plan). |
| External calls | `egress.RequestBudget` | Spotify request budget per job; song picking `granted` by default (only users given the feature). |
| Trial | `flags.trial`, `plans.entitle` | Off by default; at most `instructor`, one per account ever. |

## Gaps for opening sign-up to the public

1. **No single, explicit switch.** `CLASSBEAT_SIGNUP_MODE=open` alone opened sign-up, with no other
   precondition. **Closed by PB-5.**
2. **Turnstile off in prod left open sign-up without its captcha.** The owner's emergency degrade
   (ADR 0025 §5) is meant to keep sign-in working; with `open` it also let anonymous scripts
   create accounts behind the rate limits only. **Closed by PB-5.**
3. **No site-wide ceiling.** Every cap above is per account. With public sign-up the worst case
   is roughly *accounts created × the free plan's cap* (about $10 each per month), bounded only by
   Turnstile, email proof and the per-IP limits. There is no daily cap on new accounts and no
   global monthly model-cost cap. **Not closed: the Architect decides** (below).
4. **No disposable-address filter.** Not built and not recommended on its own: a list needs
   upkeep, is easy to route around and refuses real people; the cap in 3 bounds the same risk.

## What PB-5 changed

- `CLASSBEAT_PUBLIC_SIGNUP` (default `false`) is the one switch
  (`classbeat.flags.signup_policy`, read only through `flags.effective.environment_signup_mode`):
  - off: the environment's mode is `invite` (or `closed`); `CLASSBEAT_SIGNUP_MODE=open` without
    the flag is **refused at start-up**;
  - on: the environment's mode is `open`, unless `CLASSBEAT_SIGNUP_MODE=closed` (the brake wins);
  - on, in prod with `CLASSBEAT_TURNSTILE_ENABLED=false`: `invite`, so the degrade keeps sign-in
    and invited sign-up working and anonymous account creation stops;
  - the database can still tighten it (`invite`, `closed`), never open it, as before.
- Every reader (password and provider sign-up and confirmation, `/api/settings/public`, the admin
  Settings view) asks the same function; a test pins that nothing else reads the two fields.
- Nothing was loosened: the invite gate, the brake on confirmation, Turnstile, the rate limits
  and all caps are unchanged.

`compose.prod.yaml` does not pass `CLASSBEAT_PUBLIC_SIGNUP` yet, so prod cannot open sign-up
without a reviewed compose change; that belongs to the owner's M7b-real step.

## For the Architect

Gap 3 needs a design decision before sign-up is opened: a cap on new uninvited accounts per day
(simplest; at confirmation, so pending rows cannot pile up past it, which also amends the ADR 0011
FL1 rule that confirmation ignores `invite` and `open`) and/or a global monthly model-cost cap
(needs a cross-user sum the app role cannot read under RLS today, so a reviewed `SECURITY DEFINER`
function or a maint read). Both need a user-facing refusal in en, sv and da and numbers from the
owner.
