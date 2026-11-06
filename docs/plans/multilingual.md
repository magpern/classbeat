# Plan: milestone ML "Multilingual support" — revision 3

Issue #657 (umbrella). Design record: [ADR 0023](../adr/0023-multilingual.md); roadmap Amendment 16.
Where this plan and the ADR differ, fix the plan or amend the ADR in the PR that changes it.

Revision 3 (2026-11-06) applies the Architect's decisions on the first review of PR #674: the
language of mail sent before an account exists follows the code's real paths (section 2); a new
account keeps the language in effect at sign-up (section 2); Swedish and Danish strings are written
only in dedicated translation PRs (section 4); "the UI redesign" is defined (sequencing).

Revision 2 (2026-11-06) applied the owner's answers to revision 1's questions:

1. Languages: **English** (source and fallback), **Swedish (`sv`)** and **Danish (`da`)**, all three
   in this milestone.
2. **No human proofreading.** Translations are drafted and reviewed by Opus-class agents in separate
   contexts: a translator (the PR's Developer), then an independent Reviewer of the translations (the
   Reviewer role of AGENTS.md). CI completeness and placeholder checks are the quality gate.
3. Translator and translation reviewer: Opus 5.5 today. AGENTS.md names model classes, not versions,
   so this plan says **Opus-class**: the current Opus generation (Opus 5.5 at the time of writing).
4. **Generated classes stay English.** Brief, prompts and eval suites are unchanged in ML. A class
   language is a possible later milestone; nothing here blocks it (section 6).
5. The admin area is English only.
6. Legal and consent sentences: drafted by an Opus-class translator and checked by an independent
   Opus-class Reviewer; no owner sign-off.

## Context

classbeat is English only, and nothing outside mail is prepared for another language:

- **Frontend** (`frontend/`, React 19, Vite, no router or i18n library: `src/router/Router.tsx` is
  "a tiny history-API router"). Every string is a literal in a component or in one of seven
  code-to-sentence maps (`src/api/messages.ts` and the six `*Messages.ts`: `jobMessages.ts`, `songMessages.ts`, `saveMessages.ts`,
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
  user. Nothing reads `Accept-Language` today.
- **How accounts are created and pre-account mail is sent** (the paths section 2 follows):
  - email sign-up: `POST /api/auth/signup` (`auth/signup_routes.py` `sign_up`) either sends "account
    exists" and stores nothing (line 109, "nothing is stored") or inserts a `pending_signups` row and
    sends the confirmation; `POST /api/auth/signup/confirm` creates the user from that row
    (`_create_account`, `create_user` at line 210);
  - provider sign-in: `POST /api/auth/oauth/{provider}/start` stores an `oauth_flows` row; the callback
    (`GET /oauth/{provider}/callback`, `auth/oauth_outcomes.py`) takes that row and either creates the
    account at once for a verified provider email (`_create_account`, `create_provider_account`, with
    the "sign-in method added" notice, a welcome mail after ME3) or hands off to a browser-bound
    `pending_signups` row (`_hand_off`, `insert(pending_signups)` from `req.flow`);
  - the hand-off continues at `POST /api/auth/oauth/pending/email` (`auth/oauth_handoff.py` lines 84 to
    123), which finds that row by the pending cookie and sends either "account exists" (the row "keeps
    no email or token") or the provider sign-up confirmation; `confirm_provider_signup` then creates the
    user from the row.
- **Docs**: `docs/` and the READMEs are for developers and agents. There is no user help yet (MH #650
  is a placeholder). The landing page and the example class (`components/landing/exampleClass.ts`) are
  the only content pages.

## Inventory of user-visible text (rough counts, 2026-11-06)

| Area | Where | Rough size | In ML |
|---|---|---|---|
| Frontend app UI | 170 non-test `.ts/.tsx` files under `frontend/src` (≈ 21,600 lines) | ≈ 750 sentence-like literals, ≈ 340 JSX text runs, ≈ 160 `aria-label` / `placeholder` / `title`; **≈ 1,300 to 1,800 strings** | Yes |
| Code-to-sentence maps | seven: `src/api/messages.ts` and six `src/api/*Messages.ts` | ≈ 260 entries (jobs 102, songs 60, save 32, auth 29, class 16, plan 11, Spotify 8) | Yes |
| Admin pages | 13 files in `src/pages/admin`, `src/api/admin*.ts` | ≈ 100 to 150 strings | No (English) |
| Hand-rolled plurals | 24 sites | | Yes |
| Date, time, number, money formatting | 12 `toLocale*` / `Intl` sites, 8 `toFixed` | | Yes |
| Server sentences shown as-is | `jobs/sentences.py`, `spotify/errors.py` | ≈ 26 | Yes, as codes |
| Rule notes | `rationales.py` (31, model-facing), `lib/patternNotes.ts` (page) | 31 | Page copy only |
| API reason codes | 32 `HTTPException` sites, OAuth outcome codes | codes | Never translated |
| Mail | `emails/locales/en.py` | 79 ids now, more after ME3 to ME5 | Yes |
| Model-generated class text | name, description, segment names, cues, note | per class | No (English) |
| Logs, CLI, audit, developer docs, ADRs | | | No (English) |
| e2e assertions | 29 spec files, ≈ 580 `getByText` / role-name queries | | Stay on `en`; one spec per new language |

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
  build. **One shared manifest**, `frontend/src/i18n/locales.json` (checked in), lists the supported
  locales: `["en"]` until ML7 adds `sv` and `da`. The frontend test pins that its import map, its
  catalogue files and `public/locale-init.js`'s list equal the manifest; the backend reads the same
  file into `SUPPORTED_LOCALES` at build time (copied into the image next to the static files; a backend
  test reads it from the repository checkout, as CI has it) and a test pins that every listed locale
  has a mail catalogue in `LOCALES`. So neither side can offer a locale the other lacks. The
  setting is a plain operator switch (for example to turn a language off after a bad translation is
  found), not a review gate.
- A browser language that is not offered (for example Norwegian `nb`) falls back to English.

### 2. Where the language is stored and how it is chosen

One resolved value, from the first of:

1. **Signed in: `users.locale`** (new nullable `text`). A **new account starts with the language in
   effect at sign-up** (below). `NULL` means "follow this browser" and holds only for accounts that
   never chose one (every account that exists when ML2 migrates) or that chose "Follow this browser"
   on the account page. Set there ("Language": Follow this browser / English / Svenska / Dansk) with
   `PUT /api/account/locale` (body `{locale: "sv" | null}`, validated against the offered languages,
   CSRF as usual) and returned by `GET /api/auth/session`, so a signed-in page renders in it at once.
2. **This browser: `localStorage` key `cb-locale`**, the `cb-theme` pattern, set by a language picker
   on the logged-out pages (landing, sign-in, sign-up, reset, unsubscribe) and by the account page.
3. **The browser's languages** (`navigator.languages`), the first one offered.
4. `en`.

No new cookie: the server renders no HTML (`index.html` is static) and needs the language only for
mail. A pre-paint `public/locale-init.js` (the CSP-safe `theme-init.js` pattern) sets `<html lang>`
from storage or `navigator.languages`, **but only to a locale in its own list, which equals the
manifest** (section 1; `["en"]` until ML7), so a Swedish browser never gets `lang="sv"` on English
pages; the app updates it once the session is known. The frontend learns the offered (enabled and
supported) languages from a public `GET /api/locales` (or a field of the session answer).

**The request locale.** The server cannot read `cb-locale` (browser storage), so every POST below
carries the app's resolved locale as an optional body field `locale`. The server's resolution for a
request is: that field if it is offered; else `Accept-Language` (bounded parser: first 256 bytes, at
most 8 entries, q-values, exact match against the offered languages); else `en`.

**Pre-account mail and the new account's language**, path by path (Context lists the code):

| Path | Mail language | Stored | New account's `users.locale` |
|---|---|---|---|
| `POST /api/auth/signup`, address already has an account | "account exists" in the request locale | nothing (as today) | — |
| `POST /api/auth/signup`, new address | confirmation in the request locale | `pending_signups.locale` = request locale | `POST /api/auth/signup/confirm` copies `pending_signups.locale` in `_create_account` |
| `POST /api/auth/oauth/{provider}/start` | — | `oauth_flows.locale` = request locale | — |
| Callback, verified provider email (`oauth_outcomes._create_account`) | the notice (welcome after ME3) in `oauth_flows.locale` | — | `create_provider_account` sets it from `req.flow.locale` |
| Callback, hand-off (`oauth_outcomes._hand_off`) | — | the callback copies `req.flow.locale` into `pending_signups.locale` in the `insert(pending_signups)` | — |
| `POST /api/auth/oauth/pending/email`, address has an account | "account exists" in the request locale | nothing (as today) | — |
| `POST /api/auth/oauth/pending/email`, new address | provider confirmation in the request locale, falling back to the row's `pending_signups.locale` when the request has none | the same `update(pending_signups)` writes that locale | `confirm_provider_signup` copies `pending_signups.locale` into `create_provider_account` |

So `oauth_flows.locale` is kept: the callback is a provider redirect (a GET with no body, and the
server cannot see `cb-locale`), so the start POST is the only place the app's choice can enter the
OAuth path; the callback copies it to the user or to the hand-off row as above. Accounts created by
the CLI or an admin start at `NULL`.

The language follows the account from then on: a user who signed up in Danish gets Danish pages in a
new browser and Danish mail. Choosing "Follow this browser" sets `NULL`; such an account's mail is
English (section 7), which the account page says next to the option.

### 3. Translation runtime (frontend)

A small in-house typed catalogue, no library:

- `frontend/src/i18n/`: `locales.ts`; `en.ts`, the source catalogue (a nested `as const` object keyed
  by feature: `auth.*`, `newClass.*`, `jobs.failure.<code>.title` …); `sv.ts` and `da.ts` typed as a
  partial `Catalogue` of the shape of `en` (`tsc -b` fails on an extra or misspelt key; the
  completeness test with the untranslated list, section 4, catches a missing one);
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
- **CI quality gate** (frontend and backend): every catalogue has every key except those on its
  untranslated list (below); each translated string has exactly the placeholder names of the English
  one; placeholders are bare names with no format spec and no conversion (backend: every field from
  `string.Formatter().parse` must have an identifier `field_name` and an empty `format_spec` and
  `conversion`, so `{x.attr}`, `{x[0]}`, `{n:>999999999}` and `{n!r}` all fail; frontend: only
  `{name}`); no markup in any string; plural forms present for each language's `Intl.PluralRules`
  categories.
- **Translation by agents, reviewed by agents** (owner, 2026-11-06): a translation PR's Developer is an
  **Opus-class translator**; its Reviewer is an **independent Opus-class** context, the PR's one
  Reviewer under the unchanged review gate, that checks every changed string against the English for meaning,
  register (informal "du", as Swedish and Danish apps use), terminology from the glossary
  `docs/i18n/glossary.md` (zone, cooldown, FTP test, cadence, intervals, the MOWL app's own words where
  public), placeholders, and that security, consent and legal sentences keep their exact meaning.
  No human proofreads; there is no sign-off step beyond the agent `PASS`.
- **Swedish and Danish text is written only in dedicated translation PRs**: ML6 (mail), ML7 (app) and
  later **catch-up translation PRs**, each with an Opus-class Developer (translator) and an
  independent Opus-class Reviewer. These PRs run in **Claude Code**: the Codex and Cursor entry points
  have no Opus-class model (Amendments 10 and 11), so they never author or review `sv`/`da` content.
- **Feature PRs add English keys only**, whatever their Developer. To keep `sv`/`da` from falling
  behind silently, each catalogue has an **untranslated list** (`frontend/src/i18n/untranslated.json`,
  `emails/locales/untranslated.py`): keys present in English and allowed to be missing in `sv`/`da`,
  where they show the English text. A feature PR adds exactly the keys it introduces (a changed English
  string is listed too, and its stale `sv`/`da` text removed); CI fails on a missing key that is not
  listed and on a listed key that is translated or no longer exists. A catch-up translation PR empties
  the list; the Orchestrator opens one when the list is not empty, at the latest before a release.
  ML7 and ML6 merge with empty lists. The type check uses a partial `Catalogue` type for `sv`/`da`;
  completeness is the test's job.

### 5. Dates, times, numbers, units

- One module `src/i18n/format.ts`: `formatDate`, `formatDateTime`, `formatTime`, `formatMonth`,
  `formatNumber`, `formatMoney` (always `USD`, the currency costs are recorded in, formatted per
  locale), `formatDuration` (`mm:ss` and "45 min" from the catalogue), on `Intl` with the resolved
  locale and the browser's time zone, as today. The 12 `toLocale*` / `Intl` sites and the
  user-visible `toFixed` uses move to it; the guard bans bare
  `toLocaleString()` / `toFixed` for user-visible values outside it.
- Units need no conversion: minutes and seconds, rpm, BPM, zones, TSS, IF and an incline flag
  (`agent/schemas.py`); no distance, weight or speed. Only the abbreviations are translated.
- Mail keeps UTC (`format_when`), formatted per catalogue. `sv.py` and `da.py` take month names from
  their own catalogue (a tuple of twelve) and build the string by hand, never `strftime("%B")` with
  `locale.setlocale`, which is process-wide (today's `en.py` `format_when` relies on the C locale).
  Model-facing text is unchanged.

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
- `mail_locale(user)` returns `users.locale` when set and offered, else `en` (there is no browser at
  send time, so "Follow this browser" means English mail; new accounts carry their sign-up language,
  section 2).
- **Essential kinds never look up a user** (ADR 0022 §1), so `emails.send` (`emails/dispatch.py`) and
  `build` take `locale` as a keyword argument, filled as section 2's table says: the request locale for
  both "account exists" mails and the email sign-up confirmation; the request locale or the row's for
  the provider confirmation; `oauth_flows.locale` for the notice at a verified provider sign-up;
  `mail_locale(user)` of the user an account route already holds; the class owner's for "class ready"
  (ME5). A test pins that essential kinds still make no user lookup.
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
  names with no format spec or conversion, equal to English (section 4).
- No new cookie and no new log field. `users.locale` is in the GDPR export (the export's account
  columns are checked against the live schema). `pending_signups` and `oauth_flows` are not exported
  (they are listed with their reasons in `auth/export.py`: short-lived rows before or during sign-in);
  their `locale` columns add nothing to that, the rows expire and are purged as today, and guard 12,
  which checks user references and the exported tables, is unaffected by them.
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
  misleading Swedish and Danish text can reach users; the only checks are an independent Opus-class
  Reviewer and CI's structural checks. The owner accepted this; `CLASSBEAT_LOCALES` can switch a
  language off.
- **Legal and consent sentences** (the beta line in `AuthScreen.tsx`, Spotify pool consent, account
  deletion and export confirmations) are legal texts **without human or legal review** in Swedish and
  Danish (owner decision). The Reviewer checks they keep the English meaning; the English text stays
  the reference.
- **Extraction churn vs a redesign**: extracting screens the owner may redesign is wasted work
  (sequencing below).
- **Drift**: between a feature PR and the next catch-up translation PR, new keys show English to
  Swedish and Danish users. The untranslated list makes the gap visible and CI-checked; the
  Orchestrator keeps it short.
- **Codex and Cursor sessions cannot do translation PRs** (no Opus-class model); translation work
  waits for a Claude Code session.
- **Mixed-language screens**: a Swedish or Danish UI shows English class text (section 6), and English
  song and playlist names.
- **Longer strings** overflow buttons and tiles; the 390 px checks of `mu-pages.spec.ts` run once per
  new language.
- **e2e brittleness**: ≈ 580 English text queries; Playwright pins `en`, plus one spec per language.

## PR split (one issue per PR, own worktree, Reviewer per AGENTS.md, merge gate)

Order: ML0 → ML1 → ML2, ML3, ML4 → ML5a to ML5d; ML6 once ME1 to ME4 and ML2 are merged; ML7 after
ML5a to ML5d and ML6; then ML8.
**"The UI redesign"** is an owner idea, not a milestone (status, ML row): no issue or roadmap entry
exists. Nothing in ML waits on it unless it is planned. ML0 to ML4 and ML6 go first. ML5b to ML5d, ML7
and ML8 follow **after the redesign if and when it is planned, otherwise straight after ML5a**. ML5a
goes before a planned redesign only if the auth and landing screens are not part of it.

| Issue | Title | Developer | Reviewer | Depends on |
|---|---|---|---|---|
| ML0 (#662) | `docs: plan multilingual support (ADR 0023, Amendment 16)` | Architect | Opus (new design) | — |
| ML1 (#663) | `feat(i18n): translation runtime and locale formatting` | Sonnet | Sonnet | ML0 |
| ML2 (#664) | `feat(account): language preference` | Sonnet | Opus (sign-up and OAuth flow tables, guard 12) | ML0; ML1 for the UI |
| ML3 (#665) | `test(i18n): hard-coded string guard and catalogue checks` | Sonnet | Sonnet | ML1 |
| ML4 (#666) | `feat(api): job and Spotify sentences as codes` | Sonnet | Sonnet | ML1 |
| ML5a, ML5c, ML5d (#667, #669, #670) | `refactor(i18n): move <area> strings to the catalogue` | Sonnet | Sonnet | ML3 (c, d: after a planned redesign, if any) |
| ML5b (#668) | `refactor(i18n): move new class, job and songs strings to the catalogue` | Sonnet | Sonnet | ML3, ML4 (after a planned redesign, if any) |
| ML6 (#671) | `feat(mail): Swedish and Danish account mail` | Opus-class (translator) | Opus-class | ME1 to ME4, ML2 |
| ML7 (#672) | `feat(i18n): Swedish and Danish app catalogues` | Opus-class (translator) | Opus-class | ML4, ML5a to ML5d, ML6 |
| ML8 (#673) | `test(i18n): ML done-when suite` | Sonnet | Sonnet | ML6, ML7 |

### ML1 translation runtime and locale formatting
`frontend/src/i18n/` (locales, `en.ts`, `t`, `plural`, `useT`, static import map, `Trans`),
`format.ts`, `public/locale-init.js` and `<html lang>`, browser-side resolution (storage →
`navigator.languages` → `en`, limited to the manifest); `locales.json` (`["en"]`); the formatting
sites moved; a test-only catalogue exercises
loading and fallback. Acceptance: no visible change in English (Vitest and e2e unchanged); a missing
key falls back to English; a non-English catalogue is its own chunk; bundle check green; CSP unchanged.

### ML2 language preference
Migration: `users.locale`, `pending_signups.locale`, `oauth_flows.locale` (nullable, format `CHECK`);
`CLASSBEAT_LOCALES` (default `en,sv,da`); `SUPPORTED_LOCALES` from the manifest; offered = enabled ∩
supported; `PUT /api/account/locale`; locale and offered languages in the session answer (or
`GET /api/locales`); the request locale (optional `locale` on the bodies of `POST /api/auth/signup`,
`/api/auth/oauth/{provider}/start` and `/api/auth/oauth/pending/email`, then bounded
`Accept-Language`, then `en`); every copy of section 2's table (signup confirm, callback create and
hand-off, pending email, provider confirm); `users.locale` in the GDPR export; route classification in
`test_m6_security_routes.py`; account page "Language" select (with the "Follow this browser means
English mail" note) and the logged-out picker. Acceptance: an unknown or disabled code is refused
(account) or ignored (flows); an account created by each of the three sign-up paths carries the
sign-up locale; the session returns the stored value; the export includes `users.locale`; an 8 KiB
header with 1,000 entries parses in bounded time; two users never see each other's value.

### ML3 hard-coded string guard and catalogue checks
The shrinking-allowlist scan, completeness with the untranslated list, placeholder parity, plural
categories, no markup, the manifest checks, Playwright locale pinned to `en`. Acceptance: a new literal in a non-allowlisted file fails; adding a file to the
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
Section 7: `sv.py`, `da.py`, `format_when` per catalogue (month names from the catalogue),
`mail_locale` reading `users.locale`, `locale=` on the send path as section 2's table says,
`Content-Language`, golden files per kind and locale, the placeholder test (bare names, no format
spec, no conversion), the mail untranslated list (empty at merge). Acceptance: each kind renders in all three languages with the same
links; essential kinds make no user lookup; an unknown stored code sends English. Independent of the
redesign; can start once ME1 to ME4 and ML2 are merged (if ME5 merges first, its strings are translated
here; if later, they go on the mail untranslated list for a catch-up translation PR).
The mail catalogues are not used until ML7 adds the locales to the manifest; ML6 tests render them
directly.

### ML7 Swedish and Danish app catalogues
Creates `sv.ts` and `da.ts` with every key (the untranslated list empty), `docs/i18n/glossary.md`,
adds `sv` and `da` to `locales.json` and `locale-init.js` (the languages become selectable), one e2e spec per language (main flow,
`mu-pages` at 390 px once, axe `html-has-lang` / `valid-lang`). Acceptance: CI completeness green for
both catalogues and the manifest tests on both sides; Opus-class Reviewer
`PASS` covering meaning, register, glossary and the legal and consent sentences.

### ML8 done-when suite
e2e: pick Danish logged out, sign up, the confirmation mail is Danish (Mailpit), the new account's
language is Danish (it was in effect at sign-up) and a password reset mail is Danish; switch to Swedish
on the account page, sign in on a fresh browser and the language follows the account; choose "Follow
this browser" and the page follows the browser; a class designed on the recorded model shows a Swedish
UI around English class text.

## Sequencing against ME, MH and the UI redesign

- **ME first for mail.** ML6 needs ME1 to ME4 merged (ME1 is merged, #660). ML0 to ML5 do not touch
  mail. New ME strings go into `en.py` only; after ML6 they go on the mail untranslated list until a
  catch-up translation PR.
- **The UI redesign is an owner idea, not a planned milestone.** If it is planned, ML0 to ML4 (and ML6)
  go before it, so it writes every new component against the catalogue from day one, and ML5b to ML5d,
  ML7 and ML8 follow it (translating a stable English catalogue once). If it is not planned, they
  follow straight after ML5a; nothing waits.
- **MH (help)** follows ML0: short help in the catalogue, articles as per-locale bundled Markdown
  through a static import map, English fallback; the contact form's mail goes through ML6's path.

## Done when (milestone ML)

- An instructor can choose English, Swedish or Danish logged out and on the account; a new account
  keeps the language of its sign-up; the account's language follows them to a new browser and sets
  their mail; English is the fallback.
- Every instructor-facing screen is served from the catalogue (the guard has no non-admin file left);
  dates, numbers and money follow the language.
- Every mail kind renders in all three languages, golden-tested; essential kinds make no user lookup.
- CI fails on an unlisted missing key, a broken placeholder or a new hard-coded string; the
  untranslated lists are empty. ADR 0023 accepted.
- Generated classes are unchanged (English).
