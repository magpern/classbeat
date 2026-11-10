# ADR 0027: Language-prefixed URLs (`/en`, `/sv`, `/da`) for the whole site (#719)

Status: Accepted (Architect's design, 2026-11-07, Opus review of the design PR; implemented by P1 #743, P2 #744 and P3, which confirms the proxy and records the owner steps). Owner
decision of 2026-11-07 (issue #719): the site is public at launch, so every language gets its own
URL. This amends [ADR 0023](0023-multilingual.md) section 2 ("no language in the URL") and the
non-goal of the same name in [docs/plans/multilingual.md](../plans/multilingual.md).
[ADR 0030](0030-public-pages-prerender.md) amends this ADR once implemented (milestone PP, deferred):
section 6's fixed list of public pages and head-only pages, section 3's remark on server-side
rendering, and section 8's `Link` and `navigate`, which do a full page load at public-page boundaries.

Related: [ADR 0003](0003-accounts-and-sessions.md) (sign-in, `return_to`, OAuth callbacks),
[ADR 0013](0013-ui-design-night-studio.md) (CSP, router), [ADR 0022](0022-account-emails.md) (mail
links and `check_link`), [ADR 0023](0023-multilingual.md).

## Context

- The frontend has a 150-line history-API router (`frontend/src/router/Router.tsx`) and flat routes in
  `App.tsx` (`/`, `/signin`, `/signup`, `/signup/confirm`, `/reset`, `/email-change`, `/unsubscribe`,
  `/privacy`, `/terms`, `/help`, `/classes[/new|/:id]`, `/jobs/:id`, `/music`, `/account[/spotify]`,
  `/admin/*`).
- The backend serves the SPA: `web._add_single_page_app` returns `index.html` for any GET outside
  `/api`. swag/nginx proxies everything to it, so a new path shape needs no proxy change (to be
  confirmed in the proxy PR).
- The language is resolved on the client: `users.locale`, `cb-locale` in `localStorage`,
  `navigator.languages`, `en` (ADR 0023 section 2); `public/locale-init.js` sets `<html lang>` before
  paint.
- Mail links are `{public_base_url}{path}#token=...` (`emails/layout.py`); the token is in the
  **fragment** so it never reaches a server or log. Mail already sent is out there for days (reset,
  verify, unsubscribe, which is long lived) and must keep working.
- OAuth callbacks (`/oauth/{provider}/callback`) are registered with the providers and end in a 303 to
  `/signin`, `/signup`, `/account?oauth=...` or the flow's `return_to`, which is checked as a relative
  plain path on both sides (`safe_return_to`, `safeReturnTo`).

## Decision

**1. Shape.** Every app page lives at `/<lang>/<path>` with `<lang>` in the supported manifest
(`frontend/src/i18n/locales.json`): `/sv/classes/new`, `/en/privacy`, `/da` (the landing or home).
The path part is today's path, unchanged. **Not prefixed:** `/api/...`, `/oauth/...` (the provider
callbacks; the MOWL QR routes are under `/api`), `/email/...` and other static assets, `/health` and
`/ready` (under the API router), `/sitemap.xml`, `/robots.txt`, and **`/admin/...`** (English-only and
never public; a prefix would only suggest a language choice that does not exist; `/<lang>/admin` is not
a route and redirects to `/admin`). The canonical form has **no trailing slash** (`/sv`, `/sv/classes`);
a trailing slash is `replaceState`d away. The full route list, with the sub-pages `/signin/email`,
`/signup/confirm` and `/account/spotify`, is `App.tsx`'s table; the prefix applies to all of it.

**2. The prefix is the language of the view.** The URL wins over the account and the browser for what
the page shows. Opening `/sv/...` as a `da` account shows Swedish and **writes nothing** (a shared
link does not change anyone's settings). Only the picker writes: it sets `cb-locale` and, signed in,
`users.locale` as today, then navigates to the same page under the new prefix (query and fragment
kept). A language that is supported but not enabled (`CLASSBEAT_LOCALES`) is replaced by the best
offered language, same page.

**3. Unprefixed URLs redirect, in the client, to the best language.** A route table match without a
prefix (`/classes`, `/reset#token=...`, `/privacy`) is `replaceState`d **once**, to
`/<lang><path><search><hash>`, where `<lang>` is resolved by the existing order (`users.locale`,
`cb-locale`, `navigator.languages`, `en`). Because `users.locale` is only known once the session has
loaded, **an unprefixed URL renders nothing but the shell's loading state until the session answers**
(it is one request that `RequireAuth` already waits for; signed out it answers at once), then redirects.
This is what makes the backend's own unprefixed redirects (`/account?oauth=...`,
`/account/spotify?...`) and mail links to `/account` land in the account's language. **`AccountLocale`
changes accordingly:** on a prefixed URL it does nothing (the prefix wins, item 2); the account
language is used only by this redirect and by the picker. `navigator.languages` is the same data as
`Accept-Language`; the client is the only place that can read `cb-locale`, which is why the redirect is
not a server 302 (a server 302 on `Accept-Language` alone would override a language the visitor picked).
**After a password sign-in the account's language wins once:** `SignInPage` navigates to `return_to` (or the default page) with the prefix of the account's saved language, as a provider sign-in does through the backend's unprefixed redirect; an account that follows the browser (no saved language) keeps the prefix of the sign-in URL. Otherwise the prefix wins. `return_to` stays language-neutral. The **hash is copied explicitly**, so mail tokens survive (a test pins `/reset#token=x` and
`/unsubscribe#token=x`). These unprefixed paths are a **permanent contract**: mail already sent uses
them. A test lists every path any mail kind can emit and asserts it redirects.
Rejected: a server 302 for the three public pages (loses `cb-locale`; crawlers that run JavaScript see
the same result) and server-side rendering (out of proportion, ADR 0001).

**4. No open redirect, by construction.** The redirect target is built only from an allowlisted code
plus `location.pathname + search + hash` of the *current* origin; it always begins with
`/<code>/`, so a path of `//evil.example` becomes `/sv//evil.example`, same origin. It is never built
from a query value. `return_to` keeps its rules (a plain relative path, no `//`, no backslash, no `://`,
checked on both sides) and is **always language-neutral**: the frontend builds it from the router's
unprefixed `path` (`RequireAuth`, `JobPage`, `GuardProvider`), the backend stores and returns it
unchanged, and `navigate(returnTo)` adds the prefix of the language in effect. A deep link therefore
survives the sign-in round trip and a language switch in the middle of it. As a safety net `navigate`
and `Link` add a prefix **only when the target does not already start with an allowlisted
`/<code>/` (or equal `/<code>`)**, so a mixed or old value cannot become `/sv/sv/...`; no neutral route
is two letters, so the check is unambiguous. The backend's OAuth redirects stay unprefixed (`/signin`, `/account?oauth=...`)
and the client adds the language; the backend needs no language there.

**5. Unknown first segment.** A first segment that is two lowercase letters and not in the manifest
(`/fr/privacy`) is a **404**: the server answers 404 with `index.html` and the client shows the not-found
card in the best language. Any other unknown path stays the SPA's not-found card (status 200 as
today). No existing route is two letters, so nothing collides. Two guard tests keep it so, owned by P1 (the
frontend route table has no two-letter first segment) and P2 (the server's 404 rule and the files in
`frontend/public`).

**6. Head tags from the server, for public pages and the language.** The SPA's `index.html` is read once
at start; a response with injected head carries `Cache-Control: no-cache` (the file itself is
unchanged, the hashed assets keep their caching); for a prefixed path the server fills in `<html lang>` and, for the public pages (`/<lang>`,
`/<lang>/privacy`, `/<lang>/terms`), a canonical link and `hreflang` alternates for every **enabled**
language plus `x-default` (→ `/en/...`). Every URL is built from `public_base_url`, the manifest and a
fixed list of public paths, **never from the request's Host or raw path**. Other prefixed pages get
`<meta name="robots" content="noindex">` (signed-in app, sign-in flows), as do unprefixed ones; no
inline script is added (CSP `script-src 'self'` unchanged). `locale-init.js` reads the URL prefix first
and only then falls back to today's order. `GET /sitemap.xml` is generated from the same lists (the three
public pages × enabled languages, with `xhtml:link` alternates) and `GET /robots.txt` is `Allow: /` plus the `Sitemap:` line: nothing is
blocked, because a crawler must fetch a page and the JavaScript it needs to see its `noindex`, and
the public pages need their assets. Both routes precede the SPA catch-all (like the OAuth callback).

**7. Mail.** New mail links use the **recipient's mail locale**, in both places that build them:
`Ctx.url` (`emails/layout.py`: reset, verify, email change, new-network notice, class ready; the footer's
bare site link does not go through `Ctx.url`, stays unprefixed and is redirected by the client like
any old link) and the unsubscribe **page** link in `emails/dispatch.py` (`unsubscribe_links(...).page`,
plus `sample_unsubscribe_links`): `{base}/{mail_locale}/reset#token=...`. The RFC 8058 **one-click URL
`{base}/api/mail/unsubscribe?token=...` stays unprefixed** (it is an API route a mail client POSTs to;
prefixing it would break one-click unsubscribe). `check_link` still accepts only our origin; its test
adds the prefixed forms and keeps refusing every other host. The "every path a mail can emit" test
enumerates both builders. Old links keep working through item 3.

**8. Links inside the app.** `Link` and `navigate` take a language-neutral path and add the prefix
themselves (`useRouter` exposes `lang` and `path` without the prefix, and `App.tsx` matches on `path`),
so no component writes a prefix. A guard test (like the string guard) fails on a raw `<a href="/...">`,
`window.location` assignment or `navigate(...)` that bypasses them, and on a **hard-coded prefix** in a
link (`'/en/...'`), with an allowlist for `/api`, `/oauth` and `/admin` and for the provider start
(`goToProvider`, a full-page navigation to `/api/auth/oauth/...`).

**9. Proxy and static serving.** Nothing to route differently: swag proxies all paths to web, and web's
catch-all serves `index.html` (the Vite dev server falls back the same way). The proxy PR confirms that
`swag-classbeat.subdomain.conf` and `scripts/check-prod-compose.sh` have no path allowlist, and records
the **owner steps** in `docs/runbooks/vps-deploy.md` (sections 5a and 9) and "Waiting for the owner": enter
`https://<domain>/en/privacy` and `/en/terms` in the Google consent screen (the verifier may not run
JavaScript, so the unprefixed redirect is not enough for it), and submit `https://<domain>/sitemap.xml` to
search consoles. No DNS, TLS or Cloudflare change. Agents do not deploy.

## PR split (all depend on this ADR; #718/#720 is merged)

1. **P0, this ADR** and the doc edits (plan, ADR 0023 note, ADR index, status). Opus review.
2. **P1, frontend router and links** (items 1 to 5, 8): prefix-aware `RouterProvider`, `Link`,
   `navigate`, the redirect, `App.tsx` matching, picker, `return_to` flows, `locale-init.js`, the
   raw-link guard, unit tests (redirect never leaves the origin, hash kept, unknown code, disabled
   language, `/api` and `/admin` untouched).
3. **P2, backend** (items 5 to 7, the server half): 404 for an unknown language code, head injection,
   `/sitemap.xml`, `/robots.txt`, prefixed mail links with the `check_link` tests. Parallel to P1.
4. **P3, e2e, axe and docs** (item 9): the compose-stack suite in en, sv and da (deep link through
   sign-in, language switch keeps the page, old mail link, `<html lang>`, hreflang, 390 px axe), runbook
   and status updates, ADR 0013 and 0023 references.

Staffing: Sonnet Developer for each; **Opus Reviewer** for P1 and P2 (redirects, auth return URLs,
head built from configuration); Sonnet for P3 unless it changes a guard.

## Alternatives

- **A server 302 from unprefixed URLs.** Works without JavaScript, but cannot read `cb-locale` and
  would override a picked language; fragments are kept by browsers, but a mail scanner following the
  link gains nothing. Rejected for app pages; the public pages get correct head tags instead.
- **A cookie for the language** so the server could redirect. ADR 0023 deliberately added no cookie
  (consent notice, #715); unchanged.
- **Subdomains per language** (`sv.classbeat.eu`). Needs DNS, TLS and OAuth redirect changes and
  splits the origin that CSP, cookies (`__Host-`) and passkeys rely on. Rejected.
- **Leaving admin under `/en/admin`.** No benefit; unprefixed keeps the admin links, bookmarks and the
  runbooks as they are.

## Consequences

- Every in-app link goes through one place that adds the prefix; the guard keeps it so.
- The unprefixed routes and the `#token=` fragment handling are a compatibility contract with all mail
  ever sent; removing either is a breaking change needing its own ADR.
- Search engines get one canonical URL per language and page; signed-in pages are `noindex`.
- A shared link fixes the viewer's language to the sender's; the picker is one click and keeps the page.
- The server now builds a few HTML strings from configuration; they are escaped and tested, and no
  request value reaches them.

## Questions for the owner

None. One step is the owner's at deploy time (item 9): the Google consent-screen URLs and the sitemap
submission.

## Confirmation (P3, #719, 2026-11-07)

- **No path allowlist in the proxy files.** `docs/runbooks/swag-classbeat.subdomain.conf` has two
  locations, the SSE regex `^/api/jobs/[^/]+/events$` and `/` (everything else, to web), so `/en/...`,
  `/sitemap.xml` and `/robots.txt` pass as they are. `scripts/check-prod-compose.sh` reads the swag
  file for headers, server-level directives and the SSE location only; it has no rule on which paths
  exist (its own self-test accepts an added `location = /health`). Compose and the guard have no
  path rule either. No change was needed.
- **Owner steps** are in the runbook (5a, 9 and the post-deploy checklist) and "Waiting for the owner".
- **Tests.** The e2e specs `e2e/tests/lang-*.spec.ts` (en, sv, da) cover the deep link through sign-in
  (also with a language switch in the middle), the picker keeping page, query and fragment, old
  unprefixed mail links reaching their page with the token (the page then removes the fragment itself, `useMailToken`; the spec compares what the page shows with and without the token), `<html lang>`, canonical and hreflang, the sitemap and
  robots, and axe at 390 px. `backend/tests/test_mail_links.py` reads `frontend/src/App.tsx`'s route
  table and checks every path a mail can emit.
