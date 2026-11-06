# Plan: milestone ML "Multilingual support" — revision 2

Issue #657 (umbrella). Design record: [ADR 0023](../adr/0023-multilingual.md); roadmap Amendment 16.
Where this plan and the ADR differ, fix the plan or amend the ADR in the PR that changes it.

Revision 2 (2026-11-06) applies the owner's answers to revision 1's questions:

1. Languages: **English** (source and fallback), **Swedish (`sv`)** and **Danish (`da`)**, all three
   in this milestone.
2. **No human proofreading.** Translations are drafted and reviewed by Opus-class agents (Opus 5.5)
   in separate contexts: a translator (the PR's Developer), then an independent Reviewer of the
   translations (the Reviewer role of AGENTS.md). CI completeness and placeholder checks are the
   quality gate.
3. Translator and translation reviewer: Opus 5.5.
4. **Generated classes stay English.** Brief, prompts and eval suites are unchanged in ML. A class
   language is a possible later milestone; nothing here blocks it (section 6).
5. The admin area is English only.
6. Legal and consent sentences: drafted by Opus 5.5 and checked by an independent Opus 5.5 Reviewer;
   no owner sign-off.

## Context

classbeat is English only, and nothing outside mail is prepared for another language:

- **Frontend** (`frontend/`, React 19, Vite, no router or i18n library: `src/router/Router.tsx` is
  "a tiny history-API router"). Every string is a literal in a component or in one of seven
  code-to-sentence maps (`src/api/messages.ts`, `jobMessages.ts`, `songMessages.ts`, `saveMessages.ts`,
  `planMessages.ts`, `classMessages.ts`, `spotifyMessages.ts`). `index.html` hard-codes `lang="en"`.
- **Dates and numbers** use whatever the browser gives (`toLocaleString()` with no locale in
  `RevisionsPanel.tsx`, `HistoryPanel.tsx`, `Devices.tsx`, `NextStep.tsx`, `lib/songs.ts`,
  `UncertainWrite.tsx`, `Connections.tsx`, `Swedebeat.tsx`, `pages/admin/format.ts`, `Table.tsx`), or a
  hard-coded `en-US` (`components/account/Usage.tsx`: `Intl.NumberFormat('en-US')`, month names,
  `` `$${…toFixed(2)}` ``). So today the date format follows the browser, not the app's language.
- **Plurals** are hand-rolled English (`=== 1 ? ' was' : 's were'`, 24 sites, e.g.
  `SongsForApproval.tsx`, `swedebeat/MatchCard.tsx`).
- **Backend errors are already codes.** The 32 `HTTPException` sites carry `{"reason": code}` and the
  frontend words them (`messages.ts`: "the raw code is never shown"). The exceptions are a few server
  sentences the job page shows as they are: `jobs/sentences.py` (song picking, Swedebeat) and
  `spotify/errors.py` `SENTENCES`. Rule notes are already worded twice: `design/rules/rationales.py`
  for the model, mirrored by rule id in `frontend/src/lib/patternNotes.ts` for the page.
- **Mail** (ME1, #660, merged): `backend/src/classbeat/emails/` with the catalogue
  `emails/locales/en.py` (ids as the `Enum` `S`, 79 ids, `str.format` placeholders, `format_when` in
  UTC) and `emails/catalogue.py` (`LOCALES = {"en": EN}`, `translator(locale)`,
  `mail_locale(user) -> "en"`); `emails/dispatch.py` builds every mail with `mail_locale()` and no
  user (ADR 0022 §2).
- **Model output** (unchanged by ML): the stages write the class text (`agent/schemas.py`: name,
  description, segment names, cues, revision note) in English from English prompts; the brief and
  "Change it" chat may already be in any language (`clean_text` keeps every letter).
- **Where a preference could live**: `users` (`auth/tables.py`) has no settings column; the theme is a
  per-browser `localStorage` key `cb-theme`, applied before first paint by `public/theme-init.js`
  (CSP `script-src 'self'`, `headers.py`). `GET /api/auth/session` (`auth/routes.py`) returns the
  user; sign-up (`auth/signup_routes.py`, `pending_signups`) and OAuth start
  (`POST /api/auth/oauth/{provider}/start`, `oauth_flows`) begin with a POST body, before any user
  exists. Nothing reads `Accept-Language` today.
- **Docs**: `docs/` and the READMEs are for developers and agents. There is no user help yet (MH #650
  is a placeholder). The landing page and the example class (`components/landing/exampleClass.ts`) are
  the only content pages.

## Inventory of user-visible text (rough counts, 2026-11-06)

| Area | Where | Rough size | In ML |
|---|---|---|---|
| Frontend app UI | 170 non-test `.ts/.tsx` files under `frontend/src` (≈ 21,600 lines) | ≈ 750 sentence-like literals, ≈ 340 JSX text runs, ≈ 160 `aria-label` / `placeholder` / `title`; **≈ 1,300 to 1,800 strings** | Yes |
| Code-to-sentence maps | seven `src/api/*Messages.ts` | ≈ 260 entries (jobs 102, songs 60, save 32, auth 29, class 16, plan 11, Spotify 8) | Yes |
| Admin pages | 13 files in `src/pages/admin`, `src/api/admin*.ts` | ≈ 100 to 150 strings | No (English) |
| Hand-rolled plurals | 24 sites | | Yes |
| Date, time, number, money formatting | ≈ 15 sites, 8 `toFixed` | | Yes |
| Server sentences shown as-is | `jobs/sentences.py`, `spotify/errors.py` | ≈ 26 | Yes, as codes |
| Rule notes | `rationales.py` (31, model-facing), `lib/patternNotes.ts` (page) | 31 | Page copy only |
| API reason codes | 32 `HTTPException` sites, OAuth outcome codes | codes | Never translated |
| Mail | `emails/locales/en.py` | 79 ids now, more after ME3 to ME5 | Yes |
| Model-generated class text | name, description, segment names, cues, note | per class | No (English) |
| Logs, CLI, audit, developer docs, ADRs | | | No (English) |
| e2e assertions | 39 specs, ≈ 580 `getByText` / role-name queries | | Stay on `en`; one spec per new language |

## Goals

1. An instructor can use the whole instructor-facing app (landing, sign-in and sign-up, onboarding, new
   class, designing, songs for approval, class page, my classes, music, account) in English, Swedish
   or Danish, chosen once and remembered on the account, with English as the fallback.
2. Account mail is sent in the account's language, through ADR 0022's catalogue.
3. Dates, times, numbers and money follow the chosen language.
4. A further language is a catalogue file plus a translation review, not a change in components.
5. CI keeps it that way: no new hard-coded user-visible string, no missing or broken translation.

## Non-goals

- **Generated class content** stays English: brief, prompts, model routes and eval suites unchanged.
- The admin area, CLI, logs, audit log, API reason codes and developer docs stay English.
- No right-to-left languages; no language in the URL (`/sv/...`: the app is invite-only and not
  indexed; revisit for a public landing page in M7); no runtime machine translation or translation
  service (ADR 0001: bundled, own origin); no per-user time zone (mail keeps labelled UTC); no
  translation of third-party content (song titles, artists, playlist and mix names, MOWL's texts).
- No human proofreading (owner decision).

## Design

### 1. Languages, default and fallback

- `en` is the source language and the fallback for every string, in the app and in mail.
- `sv` and `da` are added in this milestone. Locale codes are bare language codes, no region.
- **Enablement setting** `CLASSBEAT_LOCALES` (comma list, default `en,sv,da`): the languages users may
  pick. `en` is always on. A language is offered only if it is enabled **and** supported by the
  build: the backend constant `SUPPORTED_LOCALES` is `("en",)` until ML7 adds `sv` and `da`, and a test
  pins that every supported locale has a complete app catalogue and a complete mail catalogue. The
  setting is a plain operator switch (for example to turn a language off after a bad translation is
  found), not a review gate.
- A browser language that is not offered (for example Norwegian `nb`) falls back to English.

### 2. Where the language is stored and how it is chosen

One resolved value, from the first of:

1. **Signed in: `users.locale`** (new nullable `text`; `NULL` = "Automatic", follow this browser).
   Set on the account page ("Language": Automatic / English / Svenska / Dansk) with
   `PUT /api/account/locale` (body `{locale: "sv" | null}`, validated against the offered languages,
   CSRF as usual) and returned by `GET /api/auth/session`, so a signed-in page renders in it at once.
2. **This browser: `localStorage` key `cb-locale`**, the `cb-theme` pattern, set by a language picker
   on the logged-out pages (landing, sign-in, sign-up, reset, unsubscribe) and by the account page.
3. **The browser's languages** (`navigator.languages`), the first one offered.
4. `en`.

No new cookie: the server renders no HTML (`index.html` is static) and needs the language only for
mail. A pre-paint `public/locale-init.js` (the CSP-safe `theme-init.js` pattern) sets `<html lang>`
from storage or `navigator.languages`; the app updates it once the session is known. The frontend
learns the offered languages from a public `GET /api/locales` (or a field of the session answer).

**Mail before an account exists** (sign-up confirmation, provider sign-up confirmation, "account
exists"): the POST that starts the flow carries the app's current `locale` (optional body field,
validated against the offered languages, otherwise ignored), stored on `pending_signups.locale` and
`oauth_flows.locale` for that flow's mails only. A new account starts at `users.locale = NULL`, so a
shared computer does not pin a language. With no field (old tab, API client) the server uses
`Accept-Language` (bounded parser: first 256 bytes, at most 8 entries, q-values, exact match) and then
`en`.

### 3. Translation runtime (frontend)

A small in-house typed catalogue, no library:

- `frontend/src/i18n/`: `locales.ts`; `en.ts`, the source catalogue (a nested `as const` object keyed
  by feature: `auth.*`, `newClass.*`, `jobs.failure.<code>.title` …); `sv.ts` and `da.ts` typed as
  `Catalogue` (`satisfies` the shape of `en`, so `tsc -b` fails on a missing or extra key);
  `t(key, params)` with `{name}` placeholders; `plural(key, n)` on `Intl.PluralRules` (`one`/`other`
  covers all three languages); a `useT()` hook bound to the resolved locale.
- Non-English catalogues load through a **static import map** (`{ sv: () => import('./sv'), da: … }`),
  never a path built from the locale; each is its own chunk, so English users download nothing extra
  and the first-load budget (`npm run check:bundle`, 173 of 250 kB) grows only by the runtime.
- Text is rendered as React text, never `dangerouslySetInnerHTML`. A sentence with a link or bold part
  uses a `Trans` helper with named slots that take React elements; a translator never writes markup.
- The `*Messages.ts` maps become catalogue lookups by code; the code lists stay where they are.
- The style chips (`StylePicker.tsx`) and presets (`lib/presets.ts`) show a translated label but write
  the same English value into the brief, so the language never changes what the model or a Spotify
  search receives (and generated classes stay English, non-goals).

### 4. Workflow, extraction, CI checks and translation review

- **Extraction is a refactor, area by area**, with English text **unchanged**, so the existing Vitest
  and Playwright assertions prove nothing moved. No extraction tool.
- **Guard test** in the style of `src/styles/tokens.test.ts`: scans non-test `.tsx` files for JSX text
  and user-visible attributes (`aria-label`, `placeholder`, `title`, `alt`, our components' text props)
  outside `src/i18n/`. It starts with an allowlist of every file not yet extracted that **only
  shrinks**; admin pages and brand names ("classbeat", "MOWL", "Spotify", "Swedebeat") are permanent
  exemptions.
- **CI quality gate** (frontend and backend): every catalogue has every key; each translated string has
  exactly the placeholder names of the English one; mail placeholders are bare names (checked with
  `string.Formatter().parse`: no `{x.attr}` or `{x[0]}`, which would let a catalogue reach object
  attributes through `str.format`); no markup in any string; plural forms present for each language's
  `Intl.PluralRules` categories.
- **Translation by agents, reviewed by agents** (owner, 2026-11-06): a translation PR's Developer is an
  **Opus 5.5 translator**; its Reviewer is an **independent Opus 5.5** context (AGENTS.md Reviewer
  role, review gate unchanged) that checks every changed string against the English for meaning,
  register (informal "du", as Swedish and Danish apps use), terminology from the glossary
  `docs/i18n/glossary.md` (zone, cooldown, FTP test, cadence, intervals, the MOWL app's own words where
  public), placeholders, and that security, consent and legal sentences keep their exact meaning.
  No human proofreads; there is no sign-off step beyond the agent `PASS`.
- **Keeping catalogues in step**: `sv.ts` and `da.ts` are created complete by ML7 (`sv.py` and
  `da.py` by ML6); until then the extraction PRs only touch `en.ts`. Once a catalogue exists, a PR that
  adds or changes an English string adds the `sv` and `da` strings in the same PR (CI fails otherwise). When that PR's Developer is Sonnet, it marks the new
  `sv`/`da` strings in the PR description and its Reviewer, for those strings only, is the Opus 5.5
  translation reviewer; larger text changes go in a separate translation PR with an Opus 5.5
  translator. (Amendment 16 records this model rule.)

### 5. Dates, times, numbers, units

- One module `src/i18n/format.ts`: `formatDate`, `formatDateTime`, `formatTime`, `formatMonth`,
  `formatNumber`, `formatMoney` (always `USD`, the currency costs are recorded in, formatted per
  locale), `formatDuration` (`mm:ss` and "45 min" from the catalogue), on `Intl` with the resolved
  locale and the browser's time zone, as today. The ≈ 15 sites move to it; the guard bans bare
  `toLocaleString()` / `toFixed` for user-visible values outside it.
- Units need no conversion: minutes and seconds, rpm, BPM, zones, TSS, IF and an incline flag
  (`agent/schemas.py`); no distance, weight or speed. Only the abbreviations are translated.
- Mail keeps UTC (`format_when`), formatted per catalogue. Model-facing text is unchanged.

### 6. Generated classes (English; a later milestone may change it)

Classes are generated in English: the class name, description, segment names, cues and revision note
on the class page and in the MOWL workout stay English in a Swedish or Danish UI. The UI labels
around them are translated. Kept compatible with a later "class language" milestone:

- the brief schema and the stored class are unchanged, so a later milestone can add an
  `output_language` field to the brief with a default of `en` for every existing class;
- style chips and presets keep writing English values (section 3);
- nothing compared or hashed contains translated text (section 9), so a later language field cannot
  collide with UI language.

That later milestone would need: a prompt sentence per stage (prompt versions change, so routes go
stale until an owner-authorized live suite run, ADR 0012), language-aware eval checks (the "climb"
segment-name rule and `MUSIC_WORDS` in `evals/checks.py` are English-only), and an owner-authorized
check that MOWL stores and shows å, ä, ö, æ, ø. None of that is in ML.

### 7. Mail (ADR 0022 hook)

- `emails/locales/sv.py` and `da.py` next to `en.py`, same `S` ids; `LOCALES` in
  `emails/catalogue.py` gains them; `format_when` lives in each catalogue.
- `mail_locale(user)` returns `users.locale` when set and offered, else `en` (no browser at send time:
  "Automatic" means English unless the account or the flow recorded a language).
- **Essential kinds never look up a user** (ADR 0022 §1), so `emails.send` (`emails/dispatch.py`) and
  `build` take `locale` as a keyword argument: sign-up and OAuth routes pass the flow row's locale,
  account routes pass `mail_locale(user)` of the user they already hold, the "class ready" consumer
  (ME5) reads it with the class owner. A test pins that essential kinds still make no user lookup.
- `Content-Language` is set from the allowlisted code. Golden files per kind and locale. The
  unsubscribe page follows the browser's language (section 2), not the token.

### 8. Server sentences

`jobs/sentences.py` and `spotify/errors.py` return a `sentence` code (and its numbers, such as the
retry-after minutes) next to the existing English text; the frontend words the code from the
catalogue and falls back to the English text for a code it does not know. `RATIONALES` stays the
model's English; the page keeps `patternNotes.ts` keyed by rule id.

### 9. Security

- The locale is an **allowlisted code everywhere**: a `Literal` in API models, a format `CHECK`
  (`^[a-z]{2}$`) on the columns, dropped (never echoed) when unknown. It never builds a file path,
  import specifier, SQL text, header name or prompt: lookups go through fixed maps.
- `Accept-Language` is parsed with hard bounds; headers are outside the 64 KiB body cap
  (`bodylimit.py`).
- Translations are text: React text only, mail escaped in ADR 0022's one place, placeholders bare
  names equal to English.
- No new cookie and no new log field. The locale columns are exported with the account (GDPR export;
  guard 12 sees the new columns on `users`, `pending_signups`, `oauth_flows`).
- Approval hashes (`lib/canonical.ts`, the song-list content hash) and anything compared or stored
  never contain translated text; a test pins that switching locale leaves the same list's hash equal.

## Alternatives considered

- **i18next + react-i18next** (≈ 15 to 20 kB gzip): mature, but runtime weight, a second interpolation
  style next to the mail catalogue and features we would turn off. The fallback if the in-house
  runtime grows past a few hundred lines.
- **Lingui / FormatJS (ICU)**: Lingui needs a compile-time macro next to `@vitejs/plugin-react` 6 and
  Vite 8 (compatibility risk); FormatJS is the heaviest runtime; ICU is not needed for `one`/`other`.
- **A translation platform** (Weblate, Crowdin): for human translators; not needed with agent
  translation, and a paid account is the owner's decision.
- **Language in the URL, or a server-read locale cookie**: rejected (non-goals).
- **Only `Accept-Language`, no account setting**: rejected; mail needs a stored value.
- **Human proofreading before a language is offered** (revision 1): rejected by the owner.
- **Generated classes in the UI language** (revision 1's ML6): deferred by the owner (section 6).

## Risks

- **Translations are not reviewed by any human** (owner decision, 2026-11-06). Wrong, unnatural or
  misleading Swedish and Danish text can reach users; the only checks are an independent Opus 5.5
  Reviewer and CI's structural checks. The owner accepted this; `CLASSBEAT_LOCALES` can switch a
  language off.
- **Legal and consent sentences** (the beta line in `AuthScreen.tsx`, Spotify pool consent, account
  deletion and export confirmations) are legal texts **without human or legal review** in Swedish and
  Danish (owner decision). The Reviewer checks they keep the English meaning; the English text stays
  the reference.
- **Extraction churn vs the redesign**: extracting screens the owner is about to redesign is wasted
  work (sequencing below).
- **Drift**: every English change needs `sv` and `da`; CI fails on a missing key, so the cost is paid
  in every UI PR (section 4's model rule).
- **Mixed-language screens**: a Swedish or Danish UI shows English class text (section 6), and English
  song and playlist names.
- **Longer strings** overflow buttons and tiles; the 390 px checks of `mu-pages.spec.ts` run once per
  new language.
- **e2e brittleness**: ≈ 580 English text queries; Playwright pins `en`, plus one spec per language.

## PR split (one issue per PR, own worktree, Reviewer per AGENTS.md, merge gate)

Order: ML0 → ML1 → ML2, ML3, ML4 → ML5a to ML5d; ML6 once ME1 to ME4 and ML2 are merged; ML7 after
ML5a to ML5d and ML6; then ML8.
**Before the planned UI redesign:** ML0 to ML4, and ML6 (mail is not part of the UI). **After it:**
ML5b to ML5d, ML7, ML8; ML5a before it only if the auth and landing screens are not redesigned.

| Issue | Title | Developer | Reviewer | Depends on |
|---|---|---|---|---|
| ML0 (#662) | `docs: plan multilingual support (ADR 0023, Amendment 16)` | Architect | Opus (new design) | — |
| ML1 (#663) | `feat(i18n): translation runtime and locale formatting` | Sonnet | Sonnet | ML0 |
| ML2 (#664) | `feat(account): language preference` | Sonnet | Opus (sign-up and OAuth flow tables, guard 12) | ML0; ML1 for the UI |
| ML3 (#665) | `test(i18n): hard-coded string guard and catalogue checks` | Sonnet | Sonnet | ML1 |
| ML4 (#666) | `feat(api): job and Spotify sentences as codes` | Sonnet | Sonnet | ML1 |
| ML5a–d (#667 to #670) | `refactor(i18n): move <area> strings to the catalogue` | Sonnet | Sonnet | ML3 (and the redesign for b–d) |
| ML6 (#671) | `feat(mail): Swedish and Danish account mail` | Opus 5.5 (translator) | Opus 5.5 | ME1 to ME4, ML2 |
| ML7 (#672) | `feat(i18n): Swedish and Danish app catalogues` | Opus 5.5 (translator) | Opus 5.5 | ML4, ML5a to ML5d, ML6 |
| ML8 (#673) | `test(i18n): ML done-when suite` | Sonnet | Sonnet | ML6, ML7 |

### ML1 translation runtime and locale formatting
`frontend/src/i18n/` (locales, `en.ts`, `t`, `plural`, `useT`, static import map, `Trans`),
`format.ts`, `public/locale-init.js` and `<html lang>`, browser-side resolution (storage →
`navigator.languages` → `en`); the ≈ 15 formatting sites moved; a test-only catalogue exercises
loading and fallback. Acceptance: no visible change in English (Vitest and e2e unchanged); a missing
key falls back to English; a non-English catalogue is its own chunk; bundle check green; CSP unchanged.

### ML2 language preference
Migration: `users.locale`, `pending_signups.locale`, `oauth_flows.locale` (nullable, format `CHECK`);
`CLASSBEAT_LOCALES` (default `en,sv,da`); offered languages = enabled ∩ in the build;
`PUT /api/account/locale`; locale and offered languages in the session answer (or `GET /api/locales`);
optional `locale` on the sign-up and OAuth start bodies; bounded `Accept-Language` fallback; GDPR export
and guard 12; route classification in `test_m6_security_routes.py`; account page "Language" select and
the logged-out picker. Acceptance: an unknown or disabled code is refused (account) or ignored (flows);
the session returns the stored value; export includes it; an 8 KiB header with 1,000 entries parses in
bounded time; two users never see each other's value.

### ML3 hard-coded string guard and catalogue checks
The shrinking-allowlist scan, completeness, placeholder parity, plural categories, no markup, Playwright
locale pinned to `en`. Acceptance: a new literal in a non-allowlisted file fails; adding a file to the
allowlist fails; a catalogue with a missing key or renamed placeholder fails.

### ML4 job and Spotify sentences as codes
Section 8. Acceptance: every code has an English entry equal to today's sentence; an unknown code shows
the server's English text; no provider text reaches the page (existing tests kept).

### ML5a to ML5d move strings to the catalogue
a: landing, auth pages, shell, banners; b: new class, job page, songs, `jobMessages.ts`,
`songMessages.ts`; c: class page, history, revisions, save, pattern notes; d: account, music, Spotify,
Swedebeat, plan and usage. English text unchanged, plurals through `plural`, the area's files leave the
allowlist. They touch only `en.ts`: `sv.ts` and `da.ts` do not exist before ML7.

### ML6 Swedish and Danish account mail
Section 7: `sv.py`, `da.py`, `format_when` per catalogue, `mail_locale` reading `users.locale`, `locale=`
on the send path from the flow rows for essential kinds, `Content-Language`, golden files per kind and
locale, the bare-placeholder test. Acceptance: each kind renders in all three languages with the same
links; essential kinds make no user lookup; an unknown stored code sends English. Independent of the
redesign; can start once ME1 to ME4 and ML2 are merged (ME5's kind is added by whichever merges last).
The mail catalogues are not used until ML7 adds the locales to `SUPPORTED_LOCALES`; ML6 tests render
them directly.

### ML7 Swedish and Danish app catalogues
Creates `sv.ts` and `da.ts` with every key, `docs/i18n/glossary.md`, adds `sv` and `da` to
`SUPPORTED_LOCALES` (the languages become selectable), one e2e spec per language (main flow,
`mu-pages` at 390 px once, axe `html-has-lang` / `valid-lang`). Acceptance: CI completeness green for
both catalogues and the supported-locale test (app and mail catalogue per locale); Opus 5.5 Reviewer
`PASS` covering meaning, register, glossary and the legal and consent sentences.

### ML8 done-when suite
e2e: pick Danish logged out, sign up, the confirmation mail is Danish (Mailpit), the account keeps it,
switch to Swedish on the account page, sign in on a fresh browser and the language follows the account;
a class designed on the recorded model shows a Swedish UI around English class text.

## Sequencing against ME, MH and the UI redesign

- **ME first for mail.** ML6 needs ME1 to ME4 merged (ME1 is merged, #660). ML0 to ML5 do not touch
  mail. New ME strings go into `en.py`; once ML6 is merged they need `sv` and `da` in the same PR.
- **Before the redesign**: ML0 to ML4 (and ML6). With ML1 and ML3 merged, the redesign writes every new
  component against the catalogue from day one; its screens need no extraction PR.
- **After the redesign**: ML5b to ML5d for whatever survived, then ML7 (translate a stable English
  catalogue once) and ML8.
- **MH (help)** follows ML0: short help in the catalogue, articles as per-locale bundled Markdown
  through a static import map, English fallback; the contact form's mail goes through ML6's path.

## Done when (milestone ML)

- An instructor can choose English, Swedish or Danish logged out and on the account; it is remembered
  on the account and follows them to a new browser; English is the fallback.
- Every instructor-facing screen is served from the catalogue (the guard has no non-admin file left);
  dates, numbers and money follow the language.
- Every mail kind renders in all three languages, golden-tested; essential kinds make no user lookup.
- CI fails on a missing key, a broken placeholder or a new hard-coded string. ADR 0023 accepted.
- Generated classes are unchanged (English).
