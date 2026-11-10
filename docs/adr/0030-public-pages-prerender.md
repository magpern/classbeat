# ADR 0030: Public pages: build-time prerender, client takeover and document navigation (#755)

Status: Accepted (owner approved the frozen plan on 2026-11-10, roadmap Amendment 19). **Not
implemented**: the work is planned in milestone PP and deferred until the owner instructs otherwise.
The plan and the PR split are in [docs/plans/public-pages.md](../plans/public-pages.md).

Amends: [ADR 0027](0027-language-prefixed-urls.md) §6 (the fixed list of three public pages, and head
tags without a body), §3 only as far as its remark on server-side rendering, and §8 (`Link` and
`navigate` do a full page load at public-page boundaries);
[ADR 0001](0001-stack-and-layout.md) (build-time HTML from the one frontend).
Related: [ADR 0013](0013-ui-design-night-studio.md) (CSP, tokens), [ADR 0023](0023-multilingual.md)
(translation gate), [ADR 0028](0028-ui-refresh.md) (copy rules),
[ADR 0029](0029-ai-first-class-design.md) (the designer the pages lead to).

## Context

Issue #755 wants public, indexable pages in `/en`, `/sv` and `/da`. Today every path returns an empty
`<div id="root"></div>`, one `<title>classbeat</title>` and one English description. ADR 0027 added
per-page canonical, hreflang and Open Graph tags from a fixed list. A crawler that doesn't run
JavaScript sees no content, and no page has its own title.

Constraints:

- The final image has no Node. Node exists only in the Docker `frontend` stage (ADR 0001).
- `style-src 'self'` with no `unsafe-inline`: a `style="…"` attribute in served HTML is refused, while
  React's client `style={}` goes through CSSOM and is allowed.
- Nothing from a request may enter a response. The path only selects a pre-built page (ADR 0027 §6).
- The i18n runtime first renders English and then loads the sv/da chunk (ADR 0023 §3).
- Feature PRs add English only. sv/da comes from dedicated translation PRs (ADR 0023 §8).

## Decision

**1. Prerender at build time.** `npm run build` also runs a Vite SSR bundle and
`scripts/prerender.ts`. With `react-dom/server` it renders each listed `(language, path)` to
`{title, description, body}` and writes one `frontend/build/prerender.json`, outside `dist` so it is
never served as a file. A throw, a page without exactly one `<h1>`, any `style=`, or any `fetch`
during render fails the build.

**2. One list of paths.** `frontend/src/publicSite/publicPaths.json` replaces `PUBLIC_PATHS`, following
the `locales.json` precedent. The Dockerfile copies it and `prerender.json` into `/app`. Backend,
Vitest, pytest and e2e all read it. It has two lists:

- `published`: in every supported language, with canonical, hreflang and a sitemap entry. The build
  fails if a published sv/da body equals the English one. A page moves here only when no key it
  renders is on `untranslated.json`. After that, ADR 0023 §8 applies unchanged: a feature PR may
  change English on a published page and list the key, and a catch-up translation PR empties the list
  before a release.
- `draft`: the same prerendered body and takeover, but `noindex`, no canonical or hreflang, not in
  the sitemap, and no new links to it. `/help` keeps the footer link it already has. Real pages can
  then be tested on the real serving path before publication.

**3. One source of copy.** Title and description move from `spa_preview.COPY` into catalogue keys,
verbatim, so the translation guard covers them and Python holds no copy.

**4. The server stays the only author of the head.** `spa.build_pages` replaces `<title>` and the
description exactly once and fills `#root`, from the manifest. Selection stays a dictionary lookup.
Canonical, hreflang and Open Graph tags are built as ADR 0027 §6 says. If `index.html` exists and the
manifest doesn't, web refuses to start, so a bad image can't silently de-index the site.

**5. The client takes over with `createRoot`, not `hydrateRoot`.** When `#root` has children, `main.tsx`
first awaits the catalogue, so sv/da never flash English. Hydration doesn't patch attributes, so a
mismatch would stay wrong permanently (light-theme images, for example). This choice is conditional
on the gate in item 7.

**6. Full document navigation at public-page boundaries.** One function in `Router.tsx`, used by
`Link`, `navigate` and `switchLanguage`, decides.

- A real page load happens when the current or target path is in `published` or `draft`.
- The one exception is an authenticated user moving between Home (`/<lang>`) and app pages, which
  stays client-side so signed-in users never see Landing on each return to Home. No crawler can be
  in that state.
- Query and fragment are kept. The language picker's write finishes before the page load. The app
  has no leave confirmation today, and this adds none.
- Back and forward cross the boundary as real document loads too.

**7. Takeover gate.** `e2e/tests/public-takeover.spec.ts` runs against the real image with script
loading delayed independently of the catalogue chunk, across en, sv, da × dark, light × Landing, How it
works and an example with a chart × 390 and 1440 px. This list is a summary; the plan's "Takeover
gate" has the full checks, and both bind. It requires:

- `#root` is never empty after first paint and never shows a loading card;
- the `<h1>` holds one value through the transition, with no English on sv/da;
- `layout-shift` entries sum under 0.05, and heading and chart boxes stay within 2 px;
- no request for the other theme's image, and chart bars have the zone colour before and after;
- no CSP violation.

The thresholds are starting values. **A failure blocks publication and this mechanism is revised
before release**, with this ADR amended. The order to try: fix the specific cause; render both theme
variants of an image with CSS hiding the wrong one; `hydrateRoot` on public routes.

**8. Boundaries.** URLs stay language-neutral with English slugs (ADR 0027 §1). No structured data in
the first release. No analytics: Search Console only. The page modules of listed paths, their content
components and the chart components import no runtime code from an API client module, checked on the files they import directly (the pure `api/draftTypes` is allowed; `SupportLink` and `useSupportAddress` are the one named exemption, like the shell, because they call the API only from an effect and render without an address in the prerender), the prerender makes no
network call, and fixtures are synthetic, scanned for identifiers and real song names.

## Alternatives

- **Client-only head tags.** Leaves the body empty for non-rendering crawlers.
- **`hydrateRoot`.** Every public page must render identically on server and client, and mismatches
  are silent. The fallback if the gate fails.
- **Client-side head updates on navigation.** Would duplicate `spa.py` in TypeScript, and the client
  doesn't know the server's enabled languages or the configured base URL.
- **A separate static-site generator.** A second route table and CSS pipeline.
- **Python-rendered HTML.** Duplicates the components, and Tailwind can't see the classes.
- **Runtime SSR.** Node in the image, which ADR 0001 avoids.
- **Localized slugs.** Break language-neutral `return_to`, the picker and hreflang.

## Consequences

- The prerender becomes part of every frontend build, and public components must render without
  `window` and without inline styles.
- Moving a path from `draft` to `published` is the single publication step. Moving it back is the
  rollback.
- Navigation that enters or leaves a public page reloads the document. Bundles are cached, and the
  server's head is right after every step.
- Images appear only after JavaScript, because the theme is unknown at build time, unless the gate
  forces the both-variants fallback.
- On `/<lang>`, signed-in users see Landing's prerender slightly longer before Home.

## Questions for the owner

None. The decisions are recorded as D1 to D6 in the plan.
