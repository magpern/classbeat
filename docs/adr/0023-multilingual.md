# ADR 0023: Multilingual support (milestone ML, #657)

Status: Accepted (the owner answered the plan's questions on 2026-11-06; accepted when its PR
(issue #662, ML0) merged after a `PASS`; the milestone is done, ML0 to ML8 merged). The plan, revision 3, is
[docs/plans/multilingual.md](../plans/multilingual.md); where the two differ, fix the plan or amend
this ADR in the PR that changes it. Roadmap Amendment 16.

Related: [ADR 0001](0001-stack-and-layout.md) (bundled frontend, own origin),
[ADR 0003](0003-accounts-and-sessions.md) (accounts, sign-up and OAuth flows),
[ADR 0012](0012-eval-suite-and-model-providers.md) (prompt versions and route staleness; unchanged
here), [ADR 0013](0013-ui-design-night-studio.md) (UI, CSP),
[ADR 0014](0014-account-export-and-deletion.md) (export, guard 12),
[ADR 0022](0022-account-emails.md) (mail catalogue and `mail_locale`).

## Context

classbeat is English only. The frontend has roughly 1,300 to 1,800 hard-coded strings in 170 files,
seven code-to-sentence maps (`frontend/src/api/*Messages.ts`), 24 hand-rolled English plurals and
about 15 date, number and money sites that follow the browser or a fixed `en-US`. Backend errors are
already reason codes the frontend words; two modules still send English sentences to the page
(`jobs/sentences.py`, `spotify/errors.py`). Mail goes through ADR 0022's catalogue
(`emails/locales/en.py`, `emails/catalogue.py` with `mail_locale(user) -> "en"`). No language
preference is stored anywhere and nothing reads `Accept-Language`.

Owner decisions (2026-11-06): English, Swedish and Danish in this milestone; translations drafted by a
Sonnet translator and reviewed by an independent Opus-class agent (Opus 5.5 today; AGENTS.md names
model classes, so "Opus-class" means the current Opus generation) in separate contexts, with no human proofreading; generated classes stay
English; the admin area stays English; legal and consent sentences are translated the same way, with
no owner sign-off.

## Decision

**1. Languages.** `en` is the source language and the fallback for every string; `sv` and `da` are
added. Codes are bare language codes. `CLASSBEAT_LOCALES` (default `en,sv,da`, `en` always on) is an
operator enablement switch, not a review gate. A language is offered when it is enabled and
supported. The supported locales are one checked-in manifest, `frontend/src/i18n/locales.json`
(`["en"]` until ML7 adds `sv` and `da`): the frontend pins its import map, catalogue files and
`locale-init.js` list to it; the backend loads it as `SUPPORTED_LOCALES` (shipped in the image; its test
reads the repository copy) and pins a mail catalogue for each entry.

**2. Storage and choice.** The resolved language in the browser is the first of: `users.locale`, set
on the account page with `PUT /api/account/locale` and returned with the session; the browser's
`localStorage` key `cb-locale` (set by a picker on the logged-out pages); the first offered entry of
`navigator.languages`; `en`. No new cookie. (The language in the URL was added by [ADR 0027](0027-language-prefixed-urls.md): the prefix wins for the view.) A pre-paint
`public/locale-init.js` sets `<html lang>`, only to a manifest locale (CSP `script-src 'self'`
unchanged).

The server's **request locale** is the optional `locale` body field if offered, else a bounded
`Accept-Language` parse (256 bytes, 8 entries), else `en`.

**A new account starts with the language in effect at sign-up**, stored in `users.locale` when the
account is created. `NULL` means "follow this browser": it holds for accounts that existed before ML2
and for anyone who picks "Follow this browser" on the account page; such accounts get English mail.
Accounts made by the CLI or an admin start at `NULL`.

Pre-account mail and the copies, following the code's paths (plan §2 has the table):
- "account exists" (`POST /api/auth/signup`, `POST /api/auth/oauth/pending/email`): the request
  locale; nothing is stored, as today;
- email sign-up: the confirmation in the request locale, stored on `pending_signups.locale`;
  `POST /api/auth/signup/confirm` copies it into `users.locale`;
- provider sign-in: `POST /api/auth/oauth/{provider}/start` stores the request locale on
  `oauth_flows.locale`, the only point where the app's choice can enter (the callback is a provider
  redirect with no body). The callback copies it into `users.locale` when it creates the account for a
  verified provider email (and sends that notice in it), or into the hand-off `pending_signups.locale`;
  `POST /api/auth/oauth/pending/email` sends the provider confirmation in the request locale (the row's
  when the request has none) and writes that locale to the row; `confirm_provider_signup` copies it
  into `users.locale`.

**3. Frontend runtime.** An in-house typed catalogue in `frontend/src/i18n/`: `en.ts` is the source;
`sv.ts` and `da.ts` `satisfies` its shape, so the type check fails on a missing key; `t()` with named
placeholders, `plural()` on `Intl.PluralRules`, a `Trans` helper with named slots for links (no markup
in strings, no `dangerouslySetInnerHTML`). Non-English catalogues load through a static import map as
separate chunks. Style chips and presets show translated labels but write English values into the
brief.

**4. Formatting.** One `format.ts` on `Intl` with the resolved locale and the browser's time zone, for
dates, times, months, numbers, money (always USD) and durations. Units need no conversion. Mail keeps
UTC, formatted per catalogue.

**5. Mail.** `emails/locales/sv.py` and `da.py` with the same `S` ids; their `format_when` takes month
names from the catalogue, never `strftime("%B")` with the process-wide `locale.setlocale`.
`mail_locale(user)` returns the user's stored offered locale, else `en`. Because essential kinds never
look up a user (ADR 0022 §1), `emails.send` and `build` take `locale` as an argument, filled as
Decision 2 says: the request locale for "account exists" and the email sign-up confirmation, the
request or row locale for the provider confirmation, `oauth_flows.locale` for the notice at a verified
provider sign-up, and `mail_locale(user)` of the user an account route already holds.
`Content-Language` comes from the allowlisted code. Golden files per kind and locale.

**6. Server sentences.** `jobs/sentences.py` and `spotify/errors.py` add a sentence code and its
numbers; the frontend words the code and falls back to the English text for an unknown code.
Model-facing English (`design/rules/rationales.py`) is unchanged.

**7. Generated classes stay English.** Brief, prompts, prompt versions, model routes and eval suites are
unchanged. The design stays open to a later "class language" milestone: the brief can gain an
`output_language` field defaulting to `en`, chips and presets already send English values, and no
compared or hashed value contains translated text.

**8. Quality gate and translation review.** CI: a guard against hard-coded strings in `.tsx` with an
allowlist that only shrinks (admin pages and brand names exempt); every catalogue complete except the
keys on its untranslated list; placeholder names equal to English; placeholders bare names with no
format spec and no conversion (`string.Formatter().parse` must give an identifier and empty
`format_spec` and `conversion`, so `{x.attr}`, `{x[0]}`, `{n:>999999999}` and `{n!r}` fail); no markup;
plural categories present.

**Swedish and Danish content is authored only in dedicated translation PRs**: ML6 (mail), ML7 (app)
and later catch-up translation PRs. Each has a **Sonnet translator** as Developer and an
**independent Opus-class Reviewer**, the PR's one Reviewer under the unchanged review gate, who checks
meaning, register, the glossary (`docs/i18n/glossary.md`), placeholders and that security, consent and
legal sentences keep their English meaning. They run in Claude Code: Codex and Cursor have no
Opus-class model, so they never author or review `sv`/`da` content. No human proofreads.

*Staffing change (owner decision, 2026-11-06, given in session after this ADR's first draft):* the
translator was first planned as Opus-class; the owner decided the Developer of translation PRs is
Sonnet. The Reviewer stays Opus-class and independent of the Developer, so the Opus review is the
quality gate for Swedish and Danish text. The ML8 test PR (#673) stays Sonnet and Sonnet.

**Feature PRs add English keys only**, whatever their Developer, and list each new or changed key on
the untranslated list (`frontend/src/i18n/untranslated.json`, `emails/locales/untranslated.py`), where
it shows English to Swedish and Danish users. CI fails on a missing key that is not listed and on a
listed key that is already translated or no longer exists. A catch-up translation PR empties the list;
the Orchestrator opens one when it is not empty, at the latest before a release. ML6 and ML7 merge
with empty lists.

**9. Security.** The locale is an allowlisted code everywhere (`Any` plus the offered-set allowlist in API models, so an unknown value is ignored and never echoed in a 422; format
`CHECK ^[a-z]{2}$` on columns, unknown values dropped, never echoed) and never builds a path, import
specifier, SQL, header name or prompt. `Accept-Language` parsing is bounded (256 bytes, 8 entries).
`users.locale` is in the GDPR export; `pending_signups` and `oauth_flows` stay unexported short-lived
rows (`auth/export.py` lists them with reasons) and guard 12 is unaffected by their new columns. No new
cookie or log field.

**10. Scope.** The admin area, CLI, logs, audit log, API reason codes and developer docs stay English.
No right-to-left languages, no URL prefix, no runtime machine translation, no per-user time zone.

## Alternatives

- **i18next + react-i18next**: mature, but ≈ 15 to 20 kB gzip and a second interpolation style next to
  the mail catalogue; the fallback if the in-house runtime outgrows itself.
- **Lingui or FormatJS (ICU)**: a compile-time macro with Vite 8 and plugin-react 6 (Lingui) or the
  heaviest runtime (FormatJS); ICU features are not needed for `one`/`other` plurals.
- **A translation platform**: for human translators; not needed.
- **Language in the URL or a server-read cookie**: the server renders no HTML and the app is not
  indexed.
- **`Accept-Language` only**: mail needs a stored value.
- **Human proofreading before a language is offered**: rejected by the owner.
- **New accounts start at `NULL` ("follow this browser")**: a user who signed up in Danish would get
  English mail until they found the account page; rejected for the sign-up language.
- **`sv`/`da` strings in every feature PR, with a second Opus review of those strings**: a Sonnet
  Developer would draft translations and one PR would need two Reviewers; rejected for dedicated
  translation PRs and the untranslated list.
- **Generated classes in the UI language**: deferred by the owner (Decision 7).

## Consequences

- Three nullable columns (`users.locale`, `pending_signups.locale`, `oauth_flows.locale`; the last
  two carry the sign-up language to the new account), one setting (`CLASSBEAT_LOCALES`), one manifest
  (`locales.json`), one account route, two catalogues and one untranslated list per side.
- New accounts carry their sign-up language, so their pages in any browser and all their mail
  (reset, security notices, "class ready") are in it. Accounts that existed before ML2, and accounts set
  to "Follow this browser", get English mail and pages that follow the browser; the account page says
  so next to the option.
- Between a feature PR and the next catch-up translation PR, new strings show English in Swedish and
  Danish; the untranslated list keeps the gap visible and CI-checked.
- Translation PRs need a Claude Code session (Sonnet translator, Opus-class Reviewer); Codex and Cursor sessions do none.
- **Swedish and Danish text, legal and consent sentences included, is reviewed by no human** (owner
  decision, 2026-11-06); the English text remains the reference. An operator can switch a language off.
- A Swedish or Danish UI shows English class text and English third-party names.
- "The UI redesign" is an owner idea, not a milestone. If it is planned, it writes new components
  against the catalogue once ML1 and ML3 are merged, and ML5b to ML5d, ML7 and ML8 follow it; if not,
  they follow ML5a and nothing waits.
