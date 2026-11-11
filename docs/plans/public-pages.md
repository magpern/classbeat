# Plan: milestone PP "Public Pages & SEO" (revision 2): APPROVED / FROZEN / NOT STARTED

Design record: [ADR 0030](../adr/0030-public-pages-prerender.md); roadmap Amendment 19. Where this
plan and the ADR differ, the ADR wins; fix the plan in the PR that changes it.

Revision 2 was approved and frozen by the owner on 2026-11-10 after one Architect review round
(AMEND on revision 1: two blocking and three minor findings, all folded in) and one correction at
the final review (numbers in example content). Baseline: `main` at 3121b33. The fresh Reviewer of the
planning PR found two errors of fact in the approved text, corrected here by the Architect without
changing the architecture: the translation gate now applies at publication so ADR 0023's English-only
path stays open, and a leave confirmation that doesn't exist is no longer assumed.

**Execution boundary.** Implementation is deferred. The milestone and issues below exist so the
work is ready, and creating them authorizes nothing. No PR from PP1 to PP8 starts without a new
explicit instruction from the owner. Milestone PG is documented here and is not scheduled.

GitHub: [milestone PP](https://github.com/magpern/classbeat/milestone/20), umbrella issue #755.

## Context

Most of classbeat sits behind sign-in, so crawlers see only the landing, privacy and terms pages.
Issue #755 and the owner's proposal (`public-pages/README.md` and three SVG concepts in the
`magpern/classbeat-screenshots` repository) want useful public content in `/en`, `/sv` and `/da`
that shows the real product and leads into the AI-first designer of milestone MA. Search traffic is
the outcome, not the goal: no thin SEO pages. The SVGs show structure only.

## What the investigation found

| Area | Finding | Consequence |
|---|---|---|
| Raw HTML | Every path returns an empty `<div id="root"></div>`, `<title>classbeat</title>` and one English description (`frontend/index.html`). The server rewrites neither. Only `og:*`, canonical and hreflang are per page. No `document.title` anywhere in the client | Today's "indexable" pages have no content for a non-rendering crawler, and no per-page title even for Google |
| Server head | `spa.py` pre-builds variants once at start from `PUBLIC_PATHS` and `spa_preview.COPY`. No request value reaches a response | Right shape to extend. A body can be one more pre-built field |
| ADR 0027 | Fixes "three public pages" and language-neutral paths, and rejects SSR as out of proportion (the objection in ADR 0001 is a second server runtime) | Build-time prerender adds no runtime, but needs ADR 0030 amending ADR 0027 §6 |
| CSP | `style-src 'self'`, no `unsafe-inline`. `style="…"` in served HTML is refused; React's client `style={}` is not | `MiniProfile`, `ProfileChart` and `ZoneTime` set zone colours through `style={}`. They cannot be prerendered unchanged |
| i18n | First client render is English, then the sv/da chunk loads. Long copy follows the `legal.*.ts` / `help.*.ts` pattern. Feature PRs add English only; sv/da only in dedicated translation PRs (ADR 0023) | Takeover must load the catalogue first. Publication waits for the translation PR |
| Build and deploy | Node only in the Docker `frontend` stage. swag proxies every path to web with no allowlist. Cloudflare leaves HTML uncached | Prerender belongs in `npm run build`. No proxy or deploy change |
| Designer | MA2 is not merged. `/classes/new` is still the form. `return_to` works for sign-in and OAuth, but email sign-up drops it | The CTA depends on MA2 and needs more than `return_to` |
| Sign-up | `compose.prod.yaml` defaults to `SIGNUP_MODE=invite`. Landing says "Private beta" and "Join the beta" | Stays so (D1). Public CTAs say "Join the beta" |
| Existing `/help` | Already a three-language Q&A page, but `noindex` | Becomes indexable instead of a new `/faq` (D3) |
| Existing copy | Landing says "We pick the songs" and the link-preview description repeats it, without qualification | Overstates song picking. Corrected in PP4 |

## Evaluation of the proposal

Adopted: the product framing, four-step How it works, four synthetic examples, a shared fixture for
text and chart, no auto-create, EN/SV/DA published together, raw-HTML tests.

Changed:

1. **Slugs stay English in every language** (`/sv/how-it-works`). ADR 0027 §1 and §4 need
   language-neutral paths for `return_to`, the picker and hreflang.
2. **No `/faq`.** `/help` becomes indexable in P0 and grows in P1.
3. **The chart can't be reused as is**, only after the CSP change (PP1).
4. **The CTA carries a slug, never prompt text.** A prompt in a URL lets anyone share a link that
   pre-fills arbitrary text.
5. **No structured data in P0.** None of these pages has a clearly eligible rich result. Revisit
   per page.
6. **Mockups are structure only.** They are light-first with a "Guides" link and placeholder
   thumbnails. Production is Night Studio, dark by default. P0 nav is How it works, Examples,
   Features. Library cards use `MiniProfile` from the real fixture. Copy follows ADR 0028 (second
   person, "we", lowercase "classbeat").
7. **Examples are validated against the product's own rules.** ADR 0017's data says one zone step
   at a time, first zone 4+ after about 6 minutes, cool-down 2 to 3 minutes in zone 1. The proposal's
   45-minute placeholder sums correctly, but its jumps into climbs and finish need ramps.
8. **Generated classes stay English** (ADR 0023 §7), so sv/da example pages must not imply otherwise.

## Architecture

Build-time prerender, then client takeover. The decision and its alternatives are in
[ADR 0030](../adr/0030-public-pages-prerender.md).

## Milestone PP: public pages (P0)

11 paths × 3 languages = 33 URLs: `""`, `/privacy`, `/terms`, `/help`, `/how-it-works`, `/features`,
`/examples`, four `/examples/<slug>`.

| PR | Issue | Scope | Depends on | Dev / Reviewer |
|---|---|---|---|---|
| PP0 | #889 | This plan, ADR 0030, Amendment 19, status, milestone and issues | none | Architect / Opus |
| PP1 | #890 | Prerender-safe components | #889 | Sonnet / Sonnet |
| PP2 | #891 | Prerender pipeline and catalogue takeover | #890 | Sonnet / Sonnet |
| PP3 | #892 | Backend injection, path lists, document navigation | #891 | Sonnet / **Opus** |
| PP4 | #893 | How it works, Features, public copy corrections | #891, #882 (MA2, for wording) | Sonnet drafts / **Opus** editorial and product accuracy |
| PP5 | #894 | Examples and the takeover spec | #890, #892 | Sonnet drafts / **Opus** editorial and product accuracy |
| PP6 | #895 | "Create a class like this" | #894, #882 (MA2) | Sonnet / **Opus** (`return_to`) |
| PP7 | #896 | sv/da translation | #893, #894, #895 | Sonnet / **Opus**, Claude Code only |
| PP8 | #897 | Publish | #892, #896, takeover gate, final visual review, #885 (MA5), #886 (MA6) | Sonnet / Sonnet |

PP1 to PP3 don't depend on MA. They make landing and legal text crawlable on their own. PP1 touches
`ProfileChart`, which MA also changes, so it should merge early once work is authorized. PP4 and PP5
merge as `draft`. Review, CI and merge follow AGENTS.md.

### Acceptance criteria

The GitHub issues carry the same criteria.

**PP1.** No visible change.
- `MiniProfile`, `ProfileChart` and `ZoneTime` use static class maps or SVG attributes, with no
  `style` on any rendered element.
- The chart components import `isRunningDraft` from the pure `api/draftTypes`, not `api/jobs`, so
  they load no API client.
- `Router` accepts an initial location; `useLayout`, `resolvedTheme`, `CookieNotice` and
  `SlotPicture` are safe without `window`.
- Test: e2e computed `fill` of a bar is the zone colour in both themes.

**PP2.**
- The build writes `prerender.json` for the current three paths. Build fails on a throw, on a page
  without exactly one `<h1>`, on any `style=`, or if a published sv/da body equals the English one.
  A key on `untranslated.json` doesn't fail the build, so ADR 0023's English-only feature PRs stay
  possible on published pages.
- `fetch` throws during prerender. A guard test fails if `publicSite/`, `components/landing/`, the page
  modules of listed paths, their content components or the chart components import runtime code from
  an API client module. The guard checks the files they import directly; the pure `api/draftTypes` is
  allowed. The shell, `SupportLink` and `useSupportAddress` are the named exemptions: they call the API
  only from effects, which don't run in prerender, so Help, Privacy and Terms are prerendered without a
  support address and the client fills it in after takeover (Help, Privacy and Terms are checked for
  that in the takeover spec once PP3 lists them).
- The prerender records the catalogue keys each page rendered into `frontend/build/prerender.json`,
  and PP8 adds `check:published-translations` that reads them.
- `main.tsx` awaits the catalogue when `#root` has children.
- `react-dom/server` is not in the client bundle; `check:bundle` passes.

**PP3.**
- Anonymous raw GET of `/sv`, `/sv/privacy`, `/sv/terms` holds the language's heading and text, one
  `<title>`, one description.
- App pages keep an empty root and `noindex`. Signed-in `/en` ends on Home. `draft` paths are
  `noindex`, absent from the sitemap.
- `PUBLIC_PATHS` and `COPY` are gone. Literal pin on `/en/privacy` and `/en/terms`, which the owner
  gave to Google.
- Hostile `Host` and paths give byte-identical output. CSP and injected script count are unchanged.
- `scripts/ci-changes.sh` treats `publicPaths.json` and the example fixture as backend changes.
- `lang-head`, `lang-axe` and `test_web_language` read the list. An e2e CSP-violation watcher runs on
  every public URL.
- `/help` is added to `draft`, so it is prerendered until PP8 publishes it. It keeps the footer link
  it already has, and the tests that pin that link stay as they are.
- **Navigation.** A unit table covers the decision function, including the signed-in Home exception.
  `linkGuard` keeps the document-load call in that one function.
- **Metadata e2e, in en, sv and da.** Clicking public → public, public → language switch, public →
  app page, app page → public, and back and forward. After each step the live `<title>`, description,
  canonical, hreflang set and `og:*` equal those in a raw GET of that URL, each tag appears once, and
  `noindex` is present exactly on non-public pages. Run signed out, and signed in through the footer.

**PP4.** English only. The Reviewer checks wording against shipped behaviour.
- Four steps per the proposal, a list of required connections, Features grouped by outcome.
- The PR includes a claim table: each statement mapped to a shipped route, flag or plan limit, and
  classed **generally available**, **needs a connection** or **gated by plan or flag**.
- A feature's class is data that the page renders as a visible note. A test fails if a Spotify
  song-picking string appears on a public page without its note saying a Spotify connection with the
  user's own Client ID and access through the plan are required.
- Landing's "We pick the songs", its form-era How it works copy and the link-preview descriptions are
  corrected to the same standard.
- CTA band reads "Join the beta" with one line saying sign-up is by invitation. No waitlist.
- New nav links in `SignedOutHeader` outside the session gate, and new footer links, with their
  labels. Each new link renders only when its path is in `published`, so PP8 adds no strings and these
  stay hidden until then. The existing Privacy, Terms and Help footer links don't change.
- Corrections to existing published pages go on `untranslated.json` until PP7, per ADR 0023 §8.
- Word-limit test extended, with a long-form allowance for body text.

**PP5.** English only.
- Four examples in one versioned `examples.json`: synthetic, slug-validated, cycling ×3 and running
  ×1, no games on running.
- Each page has brief, audience, sport, duration, goals, ordered segments, chart, and a real table as
  the chart's text equivalent.
- **Same source.** Workout measurements in prompts and prose are validated against the structured
  fixture in every language. Legitimate phrases such as "45-minute", "90s" and "Zone 3" are allowed:
  the test parses measurements (duration, climb count, zone, repeats) from the example strings and
  compares them with the fixture, and a decade such as "90s" is not a measurement. Tests per slug and
  language: prompt minutes = displayed duration = table total = chart axis end = sum of segment
  seconds; table rows match chart segments in count, order, duration and zone; the library card's
  profile equals the detail page's.
- **Validation.** A backend test runs every fixture through `workout.validate` and the soft-rule
  engine: valid, no `warn`.
- A scan of fixtures and `prerender.json` refuses UUIDs, emails, `/api/`, `spotify:`, `i.scdn.co`,
  token-like strings, and real song or artist names.
- Adds the takeover spec (below), running in English on the draft pages.

**PP6.**
- CTA goes to `/classes/new?example=<slug>`, checked against the fixture list; unknown slugs are ignored.
- The slug survives sign-in and OAuth via `return_to`. `RequireAuth` builds `return_to` from the path
  alone today, so PP6 carries the validated `example` value and nothing else from the query. Email
  sign-up and MOWL connection are covered by a `localStorage` slug with a 24-hour expiry, in try/catch,
  cleared on use.
- It fills the prompt in the page's language and focuses it. Test: the text equals the prompt shown
  on the example page, and no job, class, credit or MOWL write happens until the user submits.
- `RequireAuth`'s card offers "Join the beta" as well as signing in.
- The privacy page lists browser storage (`privacy.cookies.p3`), so it gains this entry, on
  `untranslated.json` until PP7.

**PP7.** Empties `untranslated.json` for PP4 to PP6, per ADR 0023 and the glossary, including chart
descriptions and metadata. Availability notes keep their English meaning. Turns on the full takeover
matrix.

**PP8.** The only PR that makes new pages public. It adds no strings.
- Moves the eight paths from `draft` to `published`, which makes PP4's nav and footer links appear.
  No page is orphaned.
- `npm run check:published-translations` compares the keys the prerender recorded for each published
  page with `untranslated.json` and reports none at this PR's head, so every page goes live fully
  translated. It runs on demand, and the Reviewer re-runs it. It is not a CI step, so English-only
  feature PRs stay possible afterwards. If later PRs added keys, a catch-up translation PR comes first.
- e2e: all 33 URLs in raw HTML, `sitemap.xml` and reciprocal hreflang; axe at 390 and 1440 px in both
  themes; language switch keeps the page; every link resolves.
- Status and "Waiting for the owner" updated. Closes #755.

### Takeover gate (blocks PP8)

`e2e/tests/public-takeover-cells.ts` (one spec file per language, see ADR 0030) runs against the real image with script loading delayed
independently of the catalogue chunk.

Matrix: en, sv, da × dark, light × Landing, How it works, an example with a chart × 390 and 1440 px.
First-visit and stored-theme cases included.

| Risk | Check |
|---|---|
| Content flash | Observer installed before load: `#root` is never empty after first paint and never shows a loading card |
| Language change | The `<h1>` holds one value through the transition; no English text on sv/da |
| Layout shift | `layout-shift` entries sum under 0.05 across takeover; heading and chart boxes stay within 2 px |
| Wrong theme imagery | No request for the other theme's image; `currentSrc` matches the theme; chart bars have the zone colour before and after |
| Security policy | No CSP violation at any point |
| Visual | Before and after screenshots per combination, compared in the run with image slots masked, and kept as artifacts |

Thresholds are starting values, set in ADR 0030. A failure blocks release and triggers ADR 0030's
revision path.

### Final visual review

After PP7 and a green gate, the Architect prepares all pages at phone and desktop width in both
themes and three languages, with the takeover before-and-after pairs. The owner reviews the completed
pages. Findings become a fix PR under the normal gates. The item waits under "Waiting for the owner"
in `docs/status.md`. This is a product check and does not replace the Reviewer's `PASS`.

### Milestone done when

- All 33 URLs give an anonymous crawler real, unique, translated content without JavaScript.
- Every criterion in #755 holds and app pages stay `noindex`.
- Metadata is correct after every navigation path tested.
- The takeover gate is green and the owner has reviewed the completed pages.
- A visitor can go from an example to a pre-filled designer without anything being created.
- Owner steps: deploy, resubmit the sitemap, check coverage in Search Console.

**Rollback.** Before PP8, the public sees only richer HTML on three existing pages and the copy
corrections of PP4 and PP6. After it, moving a
path back to `draft` and releasing makes the page `noindex`. Deployments pin versions.

## Milestone PG: instructor resources (P1), future and separate

Documented only. Not started, not scheduled, and no GitHub milestone or issues exist for it.

| PR | Scope | Key criteria |
|---|---|---|
| PG0 | Content delivery | Long copy moves to per-page lazy chunks, loaded before takeover, keeping guards. Needed first: `en.ts` is in the first-load bundle (250 kB budget). Takeover gate re-run |
| PG1 | Games guide | Four cycling games only, ADR 0029 facts G1 and G2 stated as MOWL's and our advice labelled as ours. Licence line, no claim of checking it, no running. Our own illustrations, no MOWL logo. Link to MOWL's article needs a guard exception |
| PG2 | Two guides first | "Designing a 45-minute class" and "Structuring climbs and recovery". Examples drawn from the fixtures. Sonnet drafts, Opus reviews. ADR 0017 aggregates may inform, studio data never copied |
| PG3 | `/help` growth | Answers the proposal's FAQ list from shipped behaviour, with the same availability classes |
| PG4, PG5 | Translation, publish | As PP7 and PP8, with a final visual review |

More guides are decided from Search Console evidence, not in advance.

## Owner decisions (2026-11-10)

- **D1.** Invite-only registration stays. CTA is "Join the beta". No new waitlist.
- **D2.** Publication after MA is ready: PP8 waits for MA5 and MA6.
- **D3.** `/help` becomes indexable, no separate FAQ.
- **D4.** Separate PP and PG milestones.
- **D5.** Sonnet drafts copy, Opus does editorial and product-accuracy review, and the owner reviews
  the completed pages in the final visual review. No separate owner approval of each text. sv/da stays
  Sonnet translator plus Opus reviewer (ADR 0023).
- **D6.** Search Console only. No new analytics system.

## Verification

- Per PR: the CI jobs for its paths, plus the criteria above.
- End to end on a worktree stack: `docker compose up -d --build --wait`, then
  `curl -s http://127.0.0.1:$WEB_PORT/sv/how-it-works` shows Swedish heading, text, canonical and
  hreflang with no JavaScript. Then the `public-takeover`, `lang-*` and `mu-pages` specs.
- Before PP8: the takeover gate and the final visual review.

## To confirm during implementation

- React's takeover behaviour and the CSP effect on prerendered markup come from documentation and
  spec and were not run during planning. The takeover gate and PP3's watcher prove them.
- The takeover gate's thresholds are untested starting values.
- Current bundle size was not measured, so headroom for P0 copy is unknown; `check:bundle` decides.
- The `classbeat-screenshots` tooling was not inspected, so the final review set may need a change
  in that repository.
- Whether e2e can import a file outside `e2e/` is unchecked; if not, it reads the JSON from disk.
