# Plan: milestone ML "Multilingual support" — revision 1 (for the owner's review)

Issue #657. Architect plan only: no ADR, roadmap amendment or issues yet. They follow once the owner
answers the questions at the end (expected numbers: ADR 0023, roadmap Amendment 16).

## Context

classbeat is English only, and nothing in the code is prepared for another language:

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
- **Mail**: 11 plain-text builders in `backend/src/classbeat/auth/mails.py` today. ME1 (#652, PR #660
  open) moves them to `backend/src/classbeat/emails/` with a catalogue `emails/locales/en.py` (ids as an
  `Enum` `S`, about 80 ids, `str.format` placeholders, `format_when` in UTC), `LOCALES = {"en": EN}`,
  `translator(locale)` and `mail_locale(user) -> "en"` in `emails/locales/__init__.py` (ADR 0022 §2).
- **Model output**: the stages write the user-visible class text in English by default and nobody
  asks otherwise. `agent/schemas.py`: workout name (≤ 100), description (≤ 500), segment names
  (≤ 60), cues (≤ 120) and a revision note (≤ 300). These are shown on the class page **and saved
  into the user's MOWL workout**. The system prompts (`agent/design.py` `SYSTEM_PROMPT`, `revise.py`,
  `running.py`, `soundless.py`, `pick_pool.py`, `choose_songs.py`) are English and say nothing about
  language. The user's brief text and "Change it" chat can already be in any language
  (`clean_text` keeps every letter, it only drops control and format characters).
- **Evals assume English**: `evals/checks.py` counts a climb as a segment "named "climb"" and
  `no_music_reference` uses the English regex `MUSIC_WORDS` / `NO_MUSIC`. A prompt change makes model
  routes stale (ADR 0012 §5 and its amendments: used-and-flagged for cycling, refused for running and
  soundless coverage) until a recorded live suite pass.
- **Where a preference could live**: `users` (`auth/tables.py`: email, password hash, role …) has no
  settings column; the theme is a per-browser `localStorage` key `cb-theme`, applied before first paint
  by `public/theme-init.js` (CSP `script-src 'self'`, `headers.py`). `GET /api/auth/session`
  (`auth/routes.py`) returns the user; the sign-up (`auth/signup_routes.py`, `pending_signups`) and
  OAuth start (`POST /api/auth/oauth/{provider}/start`, `oauth_flows`) both begin with a POST body,
  before any user exists. Nothing reads `Accept-Language` today.
- **Docs**: everything in `docs/` and the READMEs is for developers and agents. There is no user help
  yet (MH #650 is a placeholder). The landing page copy and the example class
  (`components/landing/exampleClass.ts`) are the only "content" pages.

## Inventory of user-visible text (rough counts, 2026-11-06, `main` at 6355be6)

| Area | Where | Rough size | Note |
|---|---|---|---|
| Frontend app UI | 170 non-test `.ts/.tsx` files under `frontend/src` (≈ 21,600 lines) | ≈ 750 sentence-like string literals, ≈ 340 JSX text runs, ≈ 160 `aria-label` / `placeholder` / `title` attributes; **≈ 1,300 to 1,800 strings** after de-duplication | Counted with greps; a real number comes out of the extraction PRs |
| Code-to-sentence maps | seven `src/api/*Messages.ts` | ≈ 260 entries (jobs 102, songs 60, save 32, auth 29, class 16, plan 11, Spotify 8) | Already keyed: the cheapest part to move |
| Admin pages | 13 files in `src/pages/admin`, `src/api/admin*.ts` | ≈ 100 to 150 strings | Proposed to stay English (non-goal) |
| Hand-rolled plurals | 24 sites | | Need plural rules |
| Date, time, number, money formatting | ≈ 15 sites (list above), 8 `toFixed` | | Need one formatting module |
| Server sentences shown as-is | `jobs/sentences.py`, `spotify/errors.py` | ≈ 26 sentences | Move to codes worded on the frontend |
| Rule notes | `design/rules/rationales.py` (31, model-facing), mirrored in `lib/patternNotes.ts` | 31 | Model copy stays English; the page copy is translated |
| API reason codes | 32 `HTTPException` sites, OAuth outcome codes | codes only | Never translated: they are identifiers |
| Mail | ME1 catalogue `emails/locales/en.py` (PR #660); ME3 adds five kinds | ≈ 80 ids now, ≈ 110 after ME3 to ME5 | The one place ADR 0022 prepared |
| Model-generated class text | name, description, segment names, cues, revision note | per class | Language chosen per class (below) |
| Logs, CLI, audit, developer docs, ADRs | backend logs, `cli.py`, `keys_cli.py`, `docs/` | | Stay English (non-goal) |
| e2e assertions | 39 specs, ≈ 580 `getByText` / role-name queries | | Keep running in English; one Swedish (or first locale) smoke spec added |

## Goals

1. An instructor can use the whole instructor-facing app (landing, sign-in and sign-up, onboarding, new
   class, designing, songs for approval, class page, my classes, music, account) in a second language,
   chosen once and remembered on the account, with English as the fallback for anything missing.
2. The classes classbeat designs (names, description, segment names, cues, notes) can be written in
   the instructor's language, because they end up on the screen of the MOWL app in front of riders.
3. Account mail is sent in the account's language, through ADR 0022's catalogue and `mail_locale`.
4. Dates, times, numbers and money follow the chosen language.
5. Adding a third language later is a catalogue file plus a review, not a code change in components.
6. CI keeps it that way: no new hard-coded user-visible string, no missing or broken translation.

## Non-goals (for ML)

- The admin area, CLI, logs, audit log, API reason codes and developer docs stay English (operator
  only; translating them doubles review work for no user).
- No right-to-left languages (layout work of its own; revisit if ever requested).
- No language in the URL (`/sv/...`): the app is invite-only and not indexed, and a URL prefix would
  touch the router, `safeReturnTo` and every link. Revisit only if a public, indexed landing page
  needs it (M7).
- No machine translation at runtime and no translation service in production (ADR 0001: everything
  bundled and served from our origin).
- No per-user time zone setting (mail keeps UTC, labelled; the page uses the browser's zone, as now).
- No translation of third-party content: song titles, artists, playlist and mix names, MOWL's own
  texts.
- No change to the brief: the instructor may already write it in any language.

## Recommended design

### 1. Languages, default and fallback

- **English (`en`) stays the source language and the fallback** for every string, in the app and in
  mail. A missing key in another catalogue shows the English text (and fails CI, section 4).
- **The second language is the owner's choice** (question 1). Swedish (`sv`) is the default proposal:
  the beta runs on a `.se` domain and Swedebeat is a Swedish product, so the first testers are likely
  Swedish-speaking. The design does not depend on which language it is.
- Locale codes are bare language codes (`en`, `sv`), no region, until a language needs two regions.
- A language is **offered only when it is complete and reviewed**: a server setting
  `CLASSBEAT_LOCALES` (default `en`) lists the languages users may pick; the build can carry a
  catalogue that is not yet offered. That lets translation PRs merge before the language goes live.

### 2. Where the language is stored and how it is chosen

One resolved value per request, from the first of:

1. **Signed in: `users.locale`** (new nullable `text` column; `NULL` = "follow this browser"). Set on
   the account page ("Language": Automatic / English / Svenska) through `PUT /api/account/locale`
   (body `{locale: "sv" | null}`, validated against `CLASSBEAT_LOCALES`, CSRF as usual) and returned by
   `GET /api/auth/session` with the user, so the app has it before it renders a signed-in page.
2. **This browser's choice: `localStorage` key `cb-locale`**, the same pattern as `cb-theme`, set by a
   small language picker on the logged-out pages (landing, sign-in, sign-up, reset, unsubscribe) and
   mirrored from the account value after sign-in.
3. **The browser's languages** (`navigator.languages`), first one we offer.
4. `en`.

No new cookie: the server renders no HTML (the SPA's `index.html` is a static file) and needs the
language only for mail. A pre-paint `public/locale-init.js` (same CSP-safe pattern as
`theme-init.js`) sets `<html lang>` from storage or `navigator.languages` so the first paint and
screen readers are right; the app updates it once the session is known.

**Mail before an account exists** (sign-up confirmation, provider sign-up confirmation, "account
exists"): the POST that starts the flow carries the app's current `locale` (optional body field,
validated against the allowlist, anything else ignored), stored on `pending_signups.locale` and
`oauth_flows.locale` for the mails of that flow only. A new account still starts at `users.locale =
NULL` ("Automatic"), so a shared computer does not pin a language; the account page sets it.
If the field is missing (old tab, API client), the server falls back to `Accept-Language` (parsed
with a bounded parser: first 256 bytes, at most 8 entries, q-values, exact match against the
allowlist) and then `en`.

### 3. Translation runtime (frontend)

**Recommendation: a small in-house typed catalogue, no library** (alternatives below).

- `frontend/src/i18n/`: `locales.ts` (the list), `en.ts` (the source catalogue, a nested `as const`
  object of keys grouped by feature: `auth.*`, `newClass.*`, `jobs.failure.<code>.title` …), one file
  per other language typed as `Catalogue` (`satisfies` the shape of `en`, so `tsc -b` already fails on
  a missing or extra key), `t(key, params)` with `{name}` placeholders, `plural(key, n)` on
  `Intl.PluralRules` with `one`/`other` (and `few`/`many` when a language needs them), and a
  `useT()` hook bound to the resolved locale.
- Non-English catalogues are loaded with a **static import map** (`{ sv: () => import('./sv') }`),
  never a path built from the locale, so each is its own chunk and English users download nothing
  extra. The first-load budget (`npm run check:bundle`, 173 of 250 kB today) only grows by the
  runtime (a few hundred bytes) and the English strings that move out of components.
- Text is rendered as React text, never `dangerouslySetInnerHTML`. A sentence with a link or bold
  part uses a `<Trans>`-like helper that takes React elements for named slots, so a translator never
  writes markup.
- The `*Messages.ts` maps become catalogue lookups by code; the code lists stay where they are.
- The style chips (`StylePicker.tsx`: "80s", "Pop", "Hip hop" …) and presets (`lib/presets.ts`) show a
  translated label but write the same value into the brief, so a language switch never changes what
  the model or a Spotify search receives.

### 4. Workflow, key extraction, completeness and review

- **Extraction is a refactor, PR by PR** (no extraction tool): a Developer moves one area's
  literals into `en.ts` with the English text **unchanged**, so the existing Vitest and Playwright
  assertions keep passing and prove nothing moved.
- **A guard test** in the style of `src/styles/tokens.test.ts`: it scans non-test `.tsx` files for
  JSX text and user-visible attributes (`aria-label`, `placeholder`, `title`, `alt`, `label=` props of
  our components) holding letters, outside `src/i18n/`. It starts with an allowlist of every file not
  yet extracted and **only shrinks** (a file may leave the list, never join it); admin pages and brand
  names ("classbeat", "MOWL", "Spotify", "Swedebeat") are permanent exemptions.
- **Completeness and safety in CI** (frontend and backend): every offered locale has every key; a
  translated string has exactly the placeholder names of the English one; backend mail placeholders
  are bare names (checked with `string.Formatter().parse`: no `{x.attr}` or `{x[0]}`, which would let a
  catalogue reach object attributes through `str.format`); no translated string contains markup.
- **Who translates**: agents draft (an Explorer or Developer, from the English catalogue and a short
  glossary `docs/i18n/glossary.md`: zone, cooldown, FTP test, cadence, intervals, the MOWL app's own
  words in that language). **Who reviews**: the agent Reviewer checks keys, placeholders and that
  nothing changes a security or legal meaning ("nothing was deleted", consent, the beta line), but
  cannot judge fluency; a native speaker named by the owner (question 3) proofreads each language once
  before it is offered, and later changes in batches. Until then the language is in the build but not
  in `CLASSBEAT_LOCALES`.
- The legal and consent sentences (the beta line in `AuthScreen.tsx`, pool consent, Spotify notes) are
  translated only with the owner's sign-off, like the legal line itself (status, "Legal line
  placeholder").

### 5. Dates, times, numbers, units

- One module `src/i18n/format.ts`: `formatDate`, `formatDateTime`, `formatTime`, `formatMonth`,
  `formatNumber`, `formatMoney` (always `USD`, the currency costs are recorded in, formatted with the
  locale: `$1.20` / `1,20 US$`), `formatDuration` (`mm:ss` and "45 min" from the catalogue), all on
  `Intl` with the resolved locale, the browser's time zone as today. The ≈ 15 sites above switch to it;
  a guard bans bare `toLocaleString()` / `toFixed` for user-visible values outside it.
- Units need no conversion: classes use minutes and seconds, rpm, BPM, zones 1 to 7 (running 1 to 5),
  TSS, IF and an incline flag (`agent/schemas.py`); there is no distance, weight or speed. Only the
  abbreviations are translated.
- Mail keeps UTC (ADR 0022's `format_when`), formatted per catalogue ("6 nov. 2026 14:05 UTC").
- Model-facing text keeps `.` decimals and English (it is input, not display).

### 6. Model output language

- **The class language is stored on the class's brief** as `output_language` (a code from the
  allowlist, set by the New class form from the UI language, shown there as "Class language" so an
  instructor can write classes in English while using a Swedish UI). Revisions and "Change it" use
  the class's stored value, so a class never changes language halfway. Question 4 asks whether the
  form shows the choice or always follows the UI language.
- **It is passed as data, not by editing the prompt per user**: `brief_for_model`
  (`agent/model_input.py`) adds `"output_language": "Swedish"` from a fixed code-to-name table, and
  each stage's system prompt gets one fixed sentence ("Write the class name, description, segment
  names, cues and note in the language named by output_language; keep tool arguments and rule names
  as given"). One prompt change per stage, so one prompt version per stage, not one per language.
  The code is an enum, so it cannot carry an injection; the user's words stay data, as now.
- The search queries and song roles of the song stages (`pick_pool.py`, `choose_songs.py`) are not
  user text and stay as they are; the brief's style value is unchanged (section 3).
- **Evals**: the prompt change bumps the stages' prompt versions, so every route is stale until an
  owner-authorized live suite run records a pass (ADR 0012; cycling keeps working flagged, running and
  soundless coverage stop). The suite gets: a `language` check (the text fields are in the asked
  language, a cheap script check on letters and a small stop-word list per language, never a model
  judge), per-language word lists for `MUSIC_WORDS` / `NO_MUSIC` and the "climb" segment name (or the
  climb check drops the name rule and uses the shape only), and a few cases with a non-English brief and
  `output_language`, in the smoke set and the suite (`suite_version` bump). Rule metrics
  (`design/rules`) read numbers only and need no change.
- Whether MOWL stores and shows non-ASCII names (å, ä, ö) correctly is **not verified**. It is checked
  once with the owner's go-ahead (one private test workout, or a read of an existing workout with such
  a name) before the class language is offered; the fakes cannot answer it.

### 7. Mail (ADR 0022 hook)

- `emails/locales/sv.py` (or the chosen language) next to `en.py`, same `S` ids; `LOCALES` gains it.
  `format_when` moves into each catalogue.
- `mail_locale(user)` returns `users.locale` if set and offered, else `en` (there is no browser at
  send time, so "Automatic" means English unless the account or the flow recorded a language).
- **Essential kinds never look up a user** (ADR 0022 §1). So the send path takes the locale as an
  argument: `emails.send(kind, recipient, params, *, prefs=None, locale=None)`; the sign-up and OAuth
  routes pass the flow's stored locale; the account routes pass `mail_locale(user)` of the user they
  already hold. A test pins that essential kinds still make no user lookup.
- `Content-Language` (already planned in ME1) is set from the allowlisted code only. Golden files
  per kind and locale. The `List-Unsubscribe` page and the unsubscribe landing page follow the
  browser's language (section 2), not the token.

### 8. Server sentences

`jobs/sentences.py` and `spotify/errors.py` return a `sentence` code (and its numbers, e.g. the
retry-after minutes) next to the existing English text; the frontend words the code from the
catalogue and falls back to the English text for a code it does not know. The rule `RATIONALES` stay
as the model's English; the page keeps using `patternNotes.ts` keyed by rule id.

### 9. Security notes

- The locale is an **allowlisted code everywhere**: a `Literal` in the API models, a format `CHECK`
  (`^[a-z]{2}$`) on the columns, and dropped (not echoed) when unknown. It never builds a file path,
  import specifier, SQL text, header name or prompt text: lookups go through fixed maps.
- `Accept-Language` is parsed with hard bounds (section 2); headers are not covered by the 64 KiB
  body cap (`bodylimit.py`).
- Translations are untrusted text in the sense of output encoding: React text only, mail escaped in
  the one place ADR 0022 has, placeholders bare names and equal to English (section 4).
- No new cookie and no new log field; the locale is not personal data in itself but is exported with
  the account (GDPR export, guard 12 sees the new columns on `users`, `pending_signups`, `oauth_flows`).
- A translation can change meaning: security and consent sentences are reviewed against the English
  meaning by the Reviewer and signed off by the owner (section 4).
- Approval hashes (`lib/canonical.ts`, the song list content hash) and anything compared or stored
  must never contain translated text: a test pins that switching locale leaves the hash of the same
  list unchanged.

## Alternatives considered

- **i18next + react-i18next** (≈ 15 to 20 kB gzip, JSON catalogues, plurals, interpolation, typed keys
  with extra setup). Mature and translator-friendly, but runtime weight, a second way of doing
  interpolation next to the mail catalogue, and features (namespaces, backends, detection) we would
  turn off. The fallback choice if the in-house runtime grows past a few hundred lines.
- **Lingui / FormatJS (ICU messages)**: ICU plurals and select, PO or JSON files and extraction
  tools. Lingui needs a compile-time macro (a Babel or SWC plugin next to `@vitejs/plugin-react` 6 and
  Vite 8, a compatibility risk); FormatJS's runtime is the heaviest. ICU's power is not needed for
  one or two languages with `one`/`other` plurals.
- **A translation platform (Weblate, Crowdin, Lokalise)**: useful with outside translators; a paid
  service or account is an owner decision and not needed while agents draft and one person proofreads.
  JSON export from `en.ts` keeps the door open.
- **Language in the URL** or **a locale cookie read by the server**: rejected for now (non-goals).
- **Language only from `Accept-Language`, no account setting**: rejected; mail needs a stored value
  and a shared or English-configured browser would be stuck.
- **Model output always in English** (only the UI translated): cheapest (no prompt change, no stale
  routes) but riders see the class text on the MOWL screen; offered as the fallback answer to
  question 4.
- **Translating the class text after design with a second model call**: doubles cost and can break
  the 60/100/120-character limits; rejected.

## Risks

- **Stale model routes**: the output-language prompt sentence stales every route until an
  owner-authorized live run (cost and a go-ahead). Mitigation: one prompt change for all stages in one
  PR, timed with another planned prompt change if one is coming.
- **Extraction churn vs the redesign**: extracting strings from screens the owner is about to redesign
  is wasted work (sequencing below).
- **Translation quality and drift**: agents cannot judge fluency; every English change needs the other
  catalogues updated. Mitigation: CI fails on a missing key, the glossary, the native proofread before a
  language is offered, and `CLASSBEAT_LOCALES` keeping unreviewed languages hidden.
- **Longer strings** (Swedish compounds, German) overflow buttons and tiles; the 390 px checks in
  `mu-pages.spec.ts` should also run once in the second language.
- **MOWL and non-ASCII** (section 6), unverified.
- **e2e brittleness**: ≈ 580 English text queries. Playwright stays on `en` (locale pinned in the
  config), plus one second-language smoke spec.
- **Eval drift**: an English-only check silently passing or failing on another language (the
  "climb" name rule, `MUSIC_WORDS`). Mitigation in section 6.

## Proposed PR split (one issue per PR, own worktree, fresh Reviewer, merge gate)

Order: ML0 → ML1 → ML2, ML3 (any order) → ML4a to ML4d (any order, redesign permitting) → ML5,
ML6, ML7 (any order once their dependencies merge) → ML8 → ML9.

### ML0 `docs: multilingual support (ADR 0023, Amendment 16)` — Architect, Opus reviewer
- ADR 0023 from this plan and the owner's answers; roadmap Amendment 16 and the ML row; status row.
  ADR 0022 gets a note that ML adds `locale=` to the send path; ADR 0012 a note on the language check.
- Acceptance: owner answers recorded; every decision in sections 1 to 9 has a home in the ADR.

### ML1 `feat(i18n): translation runtime and locale formatting` — Sonnet dev, Sonnet reviewer
- `frontend/src/i18n/` (locales, `en.ts`, `t`, `plural`, `useT`, static import map, `Trans` helper),
  `format.ts`, `public/locale-init.js` and `<html lang>`, browser-side resolution (storage →
  `navigator.languages` → `en`); the ≈ 15 date, number and money sites moved to `format.ts`; a test
  catalogue `xx` only in tests to exercise loading and fallback.
- Acceptance: no visible change in English (Vitest and e2e unchanged); a missing key falls back to
  English; a non-English catalogue is a separate chunk; bundle check green; CSP unchanged.
- Depends on ML0.

### ML2 `feat(account): language preference` — Sonnet dev, Opus reviewer (sign-up and OAuth flow tables, guard 12)
- Migration: `users.locale`, `pending_signups.locale`, `oauth_flows.locale` (nullable, format
  `CHECK`); `CLASSBEAT_LOCALES` setting; `PUT /api/account/locale`; locale in `GET /api/auth/session`;
  optional `locale` on the sign-up and OAuth start bodies; bounded `Accept-Language` fallback; GDPR
  export and guard 12 entries; route classification in `test_m6_security_routes.py`; account page
  "Language" select and the logged-out picker.
- Acceptance: unknown or unoffered codes are refused (account) or ignored (flows); the session returns
  the stored value; export includes it; a header of 8 KiB with 1,000 entries is parsed in bounded time;
  two users never see each other's value.
- Depends on ML0 (ML1 for the frontend part).

### ML3 `test(i18n): hard-coded string guard and catalogue checks` — Sonnet dev, Sonnet reviewer
- The shrinking-allowlist scan (section 4), catalogue completeness and placeholder parity for every
  locale file, Playwright locale pinned to `en`.
- Acceptance: a new literal in a non-allowlisted file fails; adding a file to the allowlist fails; a
  catalogue with a missing key or a renamed placeholder fails.
- Depends on ML1.

### ML4a to ML4d `refactor(i18n): move <area> strings to the catalogue` — Sonnet dev, Sonnet reviewer
- a: landing, auth pages, shell, banners (logged-out first); b: new class, job page, songs,
  `jobMessages.ts`, `songMessages.ts`; c: class page, history, revisions, save, pattern notes;
  d: account, music, Spotify, Swedebeat, plan and usage.
- Each: English text unchanged, plurals through `plural`, the area's files leave the allowlist.
- Acceptance: the area's Vitest and e2e specs pass unchanged; its files are off the allowlist.
- Depends on ML3; screens the redesign will replace wait for it (sequencing).

### ML5 `feat(api): job and Spotify sentences as codes` — Sonnet dev, Sonnet reviewer
- `jobs/sentences.py` and `spotify/errors.py` return a code and numbers next to the text; the job
  page words them (section 8).
- Acceptance: every code has an English catalogue entry equal to today's sentence; an unknown code
  shows the server's English text; no provider text reaches the page (existing tests kept).
- Depends on ML1.

### ML6 `feat(agent): class content language` — Sonnet dev, Opus reviewer (prompts and eval gate)
- `output_language` on the brief (form field per question 4), stored with the class, used by revise;
  the fixed prompt sentence in every stage that writes user text; the code-to-name table; eval
  `language` check, per-language music words and climb rule, new smoke and suite cases,
  `suite_version` bump; `docs/runbooks/m3c-model-enable.md` note that the routes need a new recorded
  pass.
- Acceptance: on the replayed provider, a `sv` brief yields a class whose text fields pass the
  `language` check and every existing check; an English class is unchanged; the language value cannot
  be anything but an allowlisted code (schema test); the content hash is unaffected.
- Depends on ML2 (the allowlist). Live: one owner-authorized `suite --live` run and the MOWL non-ASCII
  check before the class language is offered.

### ML7 `feat(mail): localized account mail` — Sonnet dev, Opus reviewer (mail and format strings)
- Second-language mail catalogue, `format_when` per catalogue, `mail_locale` reading `users.locale`,
  `locale=` on the send path from the flow rows for essential kinds, golden files per locale, the
  bare-placeholder test.
- Acceptance: each kind renders in both languages with the same links; essential kinds make no user
  lookup; an unknown stored code sends English; `Content-Language` matches.
- Depends on ME1 to ME4 (ME5 if merged first adds its kind) and ML2.

### ML8 `feat(i18n): <language> catalogue` — Sonnet dev, Sonnet reviewer, native proofread
- The full app catalogue in the chosen language, the glossary, the second-language smoke spec
  (main flow, `mu-pages` at 390 px once, axe `html-has-lang` / `valid-lang`).
- Acceptance: CI completeness green; the proofreader's sign-off recorded on the PR (owner's
  comment); then `CLASSBEAT_LOCALES` may include the language in the deploy config.
- Depends on ML4a to ML4d, ML5 (and ML7 for the mail half, or a follow-up PR).

### ML9 `test(i18n): ML done-when suite` — Sonnet dev, Sonnet reviewer
- e2e: pick a language logged out, sign up, confirm (mail in that language, Mailpit), the account
  keeps it, a class designed on the recorded model in that language, sign in on a fresh browser and
  the language follows the account.
- Depends on ML6, ML7, ML8.

## Sequencing against ME, MH and the UI redesign

- **ME first.** ML7 needs ME1 to ME4 merged; ML0 to ML6 do not touch mail and can run beside ME.
  New ME strings go into `en.py` as planned, so nothing is redone.
- **Before the redesign** (layout-independent, worth doing now): ML0, ML1, ML2, ML3, ML5 and ML6.
  With ML1 and ML3 merged, the **redesign writes every new component against the catalogue from day
  one** (the guard refuses new literals), so its screens never need an extraction PR. ML4a (auth and
  landing) can go before the redesign only if those screens are not being redesigned.
- **After the redesign**: ML4b to ML4d for whatever survived it, then ML8 (translate a stable English
  catalogue once, not twice) and ML9.
- **MH (help)** is planned after ML0: help text lives in the catalogue (short help) or in per-locale
  bundled Markdown loaded through a static import map (articles), with English fallback, and MH's
  contact form sends in the user's language through ML7's path.
- Expected shape: ML0 to ML3 and ML5 are a few days of agent work; ML6 adds one live run; ML8's
  length depends on the proofreader.

## Done when (milestone ML)

- An instructor can choose the second language logged out and on the account; it is remembered on the
  account and follows them to a new browser; English remains the fallback.
- Every instructor-facing screen is served from the catalogue (the guard has no non-admin file left);
  dates, numbers and money follow the language.
- Classes are designed in the class language on the recorded and replayed providers, and the eval
  suite checks the language; the owner-authorized live pass is recorded before the class language is
  offered.
- Every mail kind renders in each offered language, golden-tested, and essential kinds still make no
  user lookup.
- CI fails on a missing key, a broken placeholder or a new hard-coded string. ADR 0023 accepted.

## Owner questions (product decisions; agents build to the default until answered)

1. **Which languages, and which first?** Default: Swedish as the only second language in ML; others
   later as catalogue-only PRs.
2. **Is a language shown to testers only after a native proofread, or as soon as an agent translation
   passes CI?** Default: only after a proofread (`CLASSBEAT_LOCALES` keeps it hidden until then).
3. **Who proofreads each language?** Default: the owner for Swedish, once before launch and then in
   batches; agents draft every translation.
4. **Do generated classes follow the UI language, or does the instructor choose per class?** Default:
   the New class form has a "Class language" field that starts at the UI language and can be changed;
   revisions keep the class's language. (Alternative: always the UI language; or classes always
   English and only the UI translated, which avoids the prompt change and the live re-run.)
5. **Is the admin area English only?** Default: yes.
6. **May the legal and consent sentences be translated by agents for your sign-off, or do you provide
   them?** Default: agents draft, you sign off before the language is offered.
