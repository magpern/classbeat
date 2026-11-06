# ADR 0023: Multilingual support (milestone ML, #657)

Status: Accepted, subject to review (the owner answered the plan's questions on 2026-11-06; this ADR
is accepted when its PR (issue #662, ML0) merges after a `PASS`). The plan, revision 2, is
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

Owner decisions (2026-11-06): English, Swedish and Danish in this milestone; translations drafted and
reviewed by Opus 5.5 agents in separate contexts, with no human proofreading; generated classes stay
English; the admin area stays English; legal and consent sentences are translated the same way, with
no owner sign-off.

## Decision

**1. Languages.** `en` is the source language and the fallback for every string; `sv` and `da` are
added. Codes are bare language codes. `CLASSBEAT_LOCALES` (default `en,sv,da`, `en` always on) is an
operator enablement switch, not a review gate. A language is offered when it is enabled and in the
backend's `SUPPORTED_LOCALES`, which lists a locale only when it has a complete app catalogue and a
complete mail catalogue (a test pins both).

**2. Storage and choice.** The resolved language is the first of: `users.locale` (nullable; `NULL` is
"Automatic"), set on the account page with `PUT /api/account/locale` and returned with the session;
the browser's `localStorage` key `cb-locale` (set by a picker on the logged-out pages); the first
offered entry of `navigator.languages`; `en`. No language in the URL and no new cookie. A pre-paint
`public/locale-init.js` sets `<html lang>` (CSP `script-src 'self'` unchanged). The sign-up and OAuth
start POSTs carry an optional `locale`, stored on `pending_signups.locale` and `oauth_flows.locale` for
that flow's mails; without it the server uses a bounded `Accept-Language` parse, then `en`. A new
account starts at `NULL`.

**3. Frontend runtime.** An in-house typed catalogue in `frontend/src/i18n/`: `en.ts` is the source;
`sv.ts` and `da.ts` `satisfies` its shape, so the type check fails on a missing key; `t()` with named
placeholders, `plural()` on `Intl.PluralRules`, a `Trans` helper with named slots for links (no markup
in strings, no `dangerouslySetInnerHTML`). Non-English catalogues load through a static import map as
separate chunks. Style chips and presets show translated labels but write English values into the
brief.

**4. Formatting.** One `format.ts` on `Intl` with the resolved locale and the browser's time zone, for
dates, times, months, numbers, money (always USD) and durations. Units need no conversion. Mail keeps
UTC, formatted per catalogue.

**5. Mail.** `emails/locales/sv.py` and `da.py` with the same `S` ids. `mail_locale(user)` returns the
user's stored offered locale, else `en`. Because essential kinds never look up a user (ADR 0022 §1),
`emails.send` and `build` take `locale` as an argument: flows pass the stored flow locale, account
routes the user they hold. `Content-Language` comes from the allowlisted code. Golden files per kind
and locale.

**6. Server sentences.** `jobs/sentences.py` and `spotify/errors.py` add a sentence code and its
numbers; the frontend words the code and falls back to the English text for an unknown code.
Model-facing English (`design/rules/rationales.py`) is unchanged.

**7. Generated classes stay English.** Brief, prompts, prompt versions, model routes and eval suites are
unchanged. The design stays open to a later "class language" milestone: the brief can gain an
`output_language` field defaulting to `en`, chips and presets already send English values, and no
compared or hashed value contains translated text.

**8. Quality gate and translation review.** CI: a guard against hard-coded strings in `.tsx` with an
allowlist that only shrinks (admin pages and brand names exempt); every catalogue complete; placeholder
names equal to English; mail placeholders bare names (no attribute or index access through
`str.format`); no markup; plural categories present. Translation PRs have an **Opus 5.5 translator** as
Developer and an **independent Opus 5.5 Reviewer** (the AGENTS.md Reviewer role and review gate) who
checks meaning, register, the glossary (`docs/i18n/glossary.md`), placeholders and that security,
consent and legal sentences keep their English meaning. No human proofreads. Once `sv` and `da`
exist, any PR changing English text adds both translations; if its Developer is Sonnet, those strings
get the Opus 5.5 translation review.

**9. Security.** The locale is an allowlisted code everywhere (`Literal` in API models, format
`CHECK ^[a-z]{2}$` on columns, unknown values dropped, never echoed) and never builds a path, import
specifier, SQL, header name or prompt. `Accept-Language` parsing is bounded (256 bytes, 8 entries).
The new columns are in the GDPR export and guard 12. No new cookie or log field.

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
- **Generated classes in the UI language**: deferred by the owner (Decision 7).

## Consequences

- Three nullable columns (`users.locale`, `pending_signups.locale`, `oauth_flows.locale`), one setting
  (`CLASSBEAT_LOCALES`), one account route and two catalogues per side.
- Every UI change after ML7 and every mail change after ML6 carries Swedish and Danish text.
- **Swedish and Danish text, legal and consent sentences included, is reviewed by no human** (owner
  decision, 2026-11-06); the English text remains the reference. An operator can switch a language off.
- A Swedish or Danish UI shows English class text and English third-party names.
- The redesign writes new components against the catalogue once ML1 and ML3 are merged; extraction of
  redesigned screens waits for the redesign.
