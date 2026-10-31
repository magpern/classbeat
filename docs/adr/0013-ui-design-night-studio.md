# ADR 0013: UI design: the Night Studio look, navigation and components

Status: Accepted (2026-10-30, owner; milestone MU, before M6)

## Context

The roadmap lists the pages (§8) but never said how they should look. The frontend grew page by page
(M1, M3a U1, M3b FE1 and FE2) on a small token file (`frontend/src/styles/tokens.css`, teal and
amber, system fonts), with hand-written CSS, a hand-rolled router and no component library. What
exists today (2026-10-30):

- Navigation is a header with My classes, New class, the user's email and Sign out. The account page
  is one long column of sections (connections, Spotify, usage with tokens and cost, sign-in methods,
  email, password, two-factor, sessions).
- The class profile (`TimelineChart.tsx`) is an SVG with no axes, labels or legend: bar width is time,
  height is the zone (1 to 7), colour is the zone. It ignores cadence, standing, cue and songs.
- The landing page is one sentence. Songs for approval (ADR 0008 §9) has no UI yet.
- No Content-Security-Policy is set yet (planned, threat model). `src/no-third-party-hosts.test.ts`
  fails on any non-local URL in the frontend sources.
- Spotify track data in `mowl-workouts` 0.6.0 (`spotify_web._track`) keeps no cover art. MOWL's
  playlist list has names only.

**Owner decisions (2026-10-30, recorded in roadmap Amendment 8):**

1. A full redesign: modern, attractive to people who are not technical. Account and administrative
   things must be easy to reach.
2. People used to MOWL should feel at home. Generated classes look and feel like MOWL, with MOWL's
   colours on the bars, or colours slightly off but similar. Later the same day the owner narrowed
   this: nothing of MOWL's is copied exactly, because it is probably protected. The colours are
   similar, not identical, and the marks are icons classbeat chose, never MOWL's artwork.
3. The Spotify parts should feel like Spotify (choosing a playlist and so on).
4. Third-party UI components may be used. This overrides ADR 0001's "our own design tokens … system
   fonts" for the frontend.

## Decision

### 1. Principles

- **Instructor words.** Climbs, sprints, zones, RPM, songs, versions. Never jobs, tokens, credits,
  revisions, stages or error codes. Wording table in §6.
- **Feels like the bike.** Classes are coloured zone bars on a dark stage and read the same way as
  a MOWL program.
- **Music looks like music.** Cover art, track rows and playlist tiles, laid out the way Spotify
  users expect, within Spotify's rules (§3).
- **One big next step.** Each screen has at most one primary button. Settings and progress pages
  (Account, Designing) have none. New class, Account and usage are in the same place on every
  page.

### 2. Visual language

**Themes.** Dark is the default, because studios, MOWL and Spotify are dark. Light is available.
Account → Appearance offers Dark, Light and Same as device, kept per browser in `localStorage`.
Every colour lives in `tokens.css` as a CSS variable, in both themes:

| Token | Dark | Light | Use |
|---|---|---|---|
| `bg` | `#0E0F13` | `#F5F5F8` | page |
| `surface` | `#17181E` | `#FFFFFF` | cards, sidebar |
| `raised` | `#20222B` | `#EEEFF4` | inputs, secondary buttons, selected rows |
| `line` | `#2C2F3B` | `#DADCE5` | borders |
| `text` | `#F4F5F8` | `#15161B` | text |
| `muted` | `#A6ABBB` | `#5A6072` | secondary text (≥ 4.5:1 on `surface` and `raised`) |
| `brand` | `#8B7BFF` | `#5B4BDB` | primary buttons, links, selection (classbeat violet) |
| `on-brand` | `#0E0F13` | `#FFFFFF` | text on `brand` (5.8:1 and 6.0:1) |
| `ok` / `warn` / `bad` | `#3DDC97` / `#FFB547` / `#FF7A7A` | `#0F6E43` / `#9A5B00` / `#B32D24` | status, each with a `-soft` background |

Violet is classbeat's own colour. It is chosen so that it never competes with a zone colour, with
Spotify's green or with MOWL's branding.

**Effort zones.** MOWL's seven zones and names (`mowl.workout.ZONE_NAMES`), coloured like the
**MOWL app's**, similar but not identical (owner, 2026-10-30): grey Z1, blue Z2, green Z3, yellow Z4, and **one red for Z5 to
Z7**. Above the bars, a **flame** marks zone 6, as in MOWL, and a **bolt** marks zone 7, where
MOWL shows its owl mascot. Both are lucide icons (ISC) chosen by classbeat, never MOWL's artwork (§3).

**Similar, never identical** (owner, roadmap Amendment 8). The owner shared a low-resolution
screenshot of MOWL's desktop editor in chat. It was not committed. Read off it, MOWL shows roughly:
- blue L2;
- lime L3;
- lemon-yellow L4;
- one orange-red for L5 to L7;
- bars that fade from the zone colour at the top to a pale tint at the bottom, on a neutral
  charcoal.

classbeat's values sit next to those and are each moved in hue or lightness, so that none is
MOWL's. The fade (§7) is classbeat's own, lighter one. The values are the same in both themes:

| Zone | Name | FTP | Colour | Marker above the bar |
|---|---|---|---|---|
| 1 | Recovery | 0–55 % | `#8E99A8` grey | — |
| 2 | Endurance | 56–75 % | `#3F9CE8` blue | — |
| 3 | Tempo | 76–90 % | `#7DC05A` lime green | — |
| 4 | Threshold | 91–105 % | `#F9DF4A` yellow | — |
| 5 | VO2max | 106–120 % | `#F0483C` red | — |
| 6 | Anaerobic | 120–150 % | `#F0483C` red | flame (lucide `Flame`) |
| 7 | Neuromuscular | all out | `#F0483C` red | bolt (lucide `Zap`) |

FTP ranges are those of `mowl.workout.ZONES` (0.6.0). Zones 5 to 7 share a colour, so they are
told apart by bar height, the marker and the zone number in every label. Zone colours are used only for effort, never for buttons or status.

**The stage.** A class profile always sits on a charcoal stage, in the light theme too, like MOWL's
editor and the studio screen:
- `stage` `#26272B`, a cool charcoal of classbeat's own;
- grid lines `#383A42`;
- text `#ECEEF2` and `#A3A8B5`.

A dark stage in both themes also keeps the yellow zone legible, since yellow on white would fail
the 3:1 rule for graphics.

**Type.** Inter (UI, body, numbers with `tabular-nums`) and Archivo 700/800 (titles). Both are
bundled with `@fontsource` (SIL OFL) and served by us, never from a font CDN. Scale: 60/46/40 titles,
30/24/20 sections, 17/16/15 body, 13/12 labels.

**Shape and motion.** Radius 12–24 px for cards, 999 px pills for buttons and chips, 4–8 px for cover
art (§3). Targets are at least 44 px. Focus rings are visible. Motion is limited to progress
skeletons and short transitions, and is turned off under `prefers-reduced-motion`.

**Icons.** `lucide-react`, 2 px stroke. The logo stays classbeat's own: a rounded violet tile with
four bars.

### 3. Brand boundaries (MOWL and Spotify)

- **MOWL.** The familiarity comes from the zone colours and from MOWL's words (program, segments,
  My Workouts, RPM, TSS), and from markers above the hardest bars. classbeat uses no MOWL logo,
  owl, wordmark, typeface, icon, screenshot or asset, and doesn't reproduce MOWL's app chrome.
  Nothing of MOWL's is copied exactly, colours included. MOWL's zone 6 flame becomes a lucide flame,
  and its zone 7 owl becomes a lucide bolt. "MOWL" appears only as text naming the service
  ("Save to MOWL", "Connected to MOWL"), and nothing may suggest classbeat is made by MOWL (roadmap
  "Development process").
- **Spotify.** Screens that show Spotify data follow the Spotify Design & Branding Guidelines for
  developers:
  - the official Spotify logo, from Spotify's brand resources and bundled with the app, next to
    Spotify metadata;
  - cover art shown unaltered: no crop, overlay, blur or tint, with a 4 px radius (8 px on large
    sizes);
  - track and artist names never rewritten;
  - links that say OPEN SPOTIFY or PLAY ON SPOTIFY. They appear only where the API returns a
    link, such as a final playlist's `link` (ADR 0008 §9). The frontend never builds a Spotify URL,
    and no raw Spotify id reaches the page, except inside a link the API returns. Whether Songs for
    approval needs a link back to Spotify is settled in UI8 against the guidelines' current text. If
    it does, the link is built by the API, which needs an ADR 0008 §9 change.

  Spotify green appears only inside Spotify's logo. classbeat's own controls (Approve songs, the
  sidebar) stay violet, so the app never looks like a Spotify product. The "Spotify feel" is in the
  layout: a playlist header with cover mosaic, numbered track rows (cover, title, artist, part of
  the class, BPM, length) and a grid of playlist tiles. **Before UI7 and UI8 merge, the Developer
  checks the guidelines' current text.** This ADR's reading comes from search summaries, because
  `developer.spotify.com` was not reachable from the design session.

### 4. Navigation

- **Desktop (≥ 1024 px): a left sidebar.** From top to bottom:
  - the logo;
  - a **New class** pill;
  - Home, My classes, Music and Account;
  - a "Classes this month" meter, then the user's avatar and name linking to Account.
  - Admin appears only for admins, under the avatar.
- **Tablet (640–1023 px):** the sidebar collapses to an icon rail with the same items.
- **Phone (< 640 px):** a bottom tab bar with Home, Classes, a round **+** (New class), Music and
  Account, plus the avatar top right. Detail pages (a class, songs for approval) replace the tab
  bar with a sticky bar holding their one or two actions.
- **Focus mode.** New class and Designing hide the sidebar and show a slim bar with the logo and
  Close / Back to home, so the instructor isn't distracted mid-task.

| Route | Page |
|---|---|
| `/` | Landing (signed out) or Home (signed in) |
| `/classes` | My classes |
| `/classes/new` | New class (focus mode) |
| `/jobs/:id` | Designing (focus mode) |
| the approval route of ADR 0008 §9 | Songs for approval |
| `/classes/:id` | Class |
| `/music` | Music: the user's MOWL playlists and the playlists classbeat made (OPEN SPOTIFY where the API gives the final playlist's link); a "Connect Spotify" card while not connected. The playlists classbeat made come from a new read-only, owner-scoped class-API read (UI8) |
| `/account` | Account, with tabs: Connections, Plan and usage, Sign-in and security, Your data, Appearance |
| `/account/spotify` | The Spotify wizard and settings. It stays where it is, because ADR 0009's callback ends there |
| `/admin` | Admin (M5), in the same shell |

### 5. Screens

Each item gives the content and its **primary action**, if any (at most one, §1).

- **Landing.** A headline, an example class profile on the stage with its song list (synthetic and
  invented, like the e2e fake), three steps (describe, approve songs, save to MOWL) and **Sign up
  with an invite code**. Sign in and Sign up are focus pages using the same components.
- **Home.**
  - A greeting and a "Design a new class" card: **Design a class**, plus preset chips that prefill
    the form (45 min hills, 30 min intervals, 60 min endurance).
  - A "Get set up" checklist (account, MOWL, optional Spotify), shown until it is complete.
  - Recent classes as cards: a mini profile on the stage, title, length, TSS and song count, and a
    status chip. A TSS with any zone 7 interval shows as "≈ 52", with ADR 0012 §7's note as its
    accessible label (so on My classes too).
- **New class** (one scrolling page of cards, not a multi-step wizard):
  - name;
  - length as chips plus "Other length";
  - music: **Pick songs for me** (song picking, ADR 0008) or **Use my playlist**, with style chips
    for era and genre plus free text;
  - the user's MOWL playlists as a Spotify-like tile grid, each tile showing the playlist's
    initials (no playlist covers, §9);
  - effort as three cards (Easy, Moderate, Hard), each showing its zone range;
  - climbs, sprints and races as − / + steppers;
  - FTP test;
  - "Anything else?".

  A side card, "Your class so far", shows a rough sketch on the stage, a summary, **Design my class**
  and "Uses 1 of your N classes left this month". The fields and limits are those of today's form
  and API.
- **Designing.** The title ("Finding songs for …", then "Designing …") and "Step n of m". A pulsing
  ghost profile on the stage. A checklist of stages in plain words (§6), from the job's
  stage events. The text "You can close this page …". Secondary action: Stop designing.
- **Songs for approval.** Everything ADR 0008 §9 requires, laid out like a Spotify playlist:
  - a header with a 2×2 cover mosaic, "Songs for your class", the class title, the count, music
    time against class time, and the source with the Spotify logo;
  - **Approve songs** (round, violet), Ask for changes and the "waiting until" date. There is no
    OPEN SPOTIFY here: no final playlist exists yet, and the working playlist stays internal;
  - the track rows, each with a replace toggle and its note;
  - a request box;
  - the privacy sentence.
- **Class.**
  - A header with the title, save-state chips ("Version 3 saved to MOWL", "Version 4 has changes
    not in MOWL yet") and **Save to MOWL** / **Update in MOWL**, with Change songs and a ··· menu
    (Versions, Delete).
  - Stat tiles: Length, TSS, Songs, Climbs, Sprints, Hardest zone. With any zone 7 interval, the
    TSS tile keeps ADR 0012 §7's note "approximate: zone 7 has no exact TSS weight".
  - The profile (§7) and "Time in each zone".
  - Notices, such as a song with no measured tempo.
  - "Song by song" rows with a zone strip, start time, part and song, zones, RPM, position and cue.
  - A side column with the **Change it** chat (bubbles, what changed, changes left) and Versions.
  - The uncertain panels keep their meaning, restyled as warning cards headed "Check needed".
- **My classes.** A card grid like Home's, with filters (All, Saved to MOWL, Drafts) and search.
  Primary action: **Design a class**.
- **Account.** A profile header (avatar, name, email, Change email), then the tabs:
  - **Connections:** cards for MOWL, Spotify (with the working-playlist consent as a switch) and
    Swedebeat (coming later).
  - **Plan and usage:** a meter, the renew date and "Have a code?". Tokens and cost move behind
    "Show usage details".
  - **Sign-in and security:** rows for password, linked providers, two-step sign-in and signed-in
    devices.
  - **Your data:** Download my data, Delete my account and Sign out.

### 6. Wording

| Internal | On screen |
|---|---|
| job, run, stage | "designing", "Finding songs that fit", "MOWL is listening to the music", "Choosing the best songs for each part", "Building your class" |
| revision | version |
| credit, quota | "classes this month", "changes left for this class" |
| uncertain operation | "Check needed": what we know, then "Check again" / "Do it anyway" |
| `waiting_for_approval` | "Songs ready for you to approve" |
| pool playlist | "working playlist" |
| connection `needs_reconnect` | "Connect again" |

TSS, FTP, RPM and zone numbers stay, because MOWL users know them.

### 7. The class profile

- **x is time** (bar width is the interval's `sec`). **y is the zone** (bar height grows with the
  zone, as today). **Colour is the zone** (§2). Each bar fades from its zone colour at the top to
  that colour mixed 60/40 with white at the bottom (`color-mix`), so the effort reads at a glance as
  in MOWL. Bars sit on the stage with 1 px gaps, faint horizontal grid lines and a time axis every
  5 minutes.
- **Markers** (§2): a flame sits above every zone 6 bar and a bolt above every zone 7 bar,
  centred, in the stage's text colour. On a bar narrower than the icon, the icon overflows it
  evenly. Bar heights leave room for the marker (about 6 % plus 10.5 % per zone).
- **Songs** run under the time axis, where MOWL users look for them, as a strip of alternating
  blocks. Each block has the title and BPM and is as wide as the song's time in the class. Before the class API exposes the song list with start
  times, the strip is hidden.
- **Selecting a bar.** Hover, tap, or left and right arrow keys once the chart has focus. The chosen
  interval is outlined, and a detail bar below the chart shows its zone and name, its times, RPM
  (range or "on the beat"), seated or standing, and its cue.
- **Time in each zone:** one row per zone (name, a bar scaled to the largest zone, the time).
- **Accessible:**
  - the chart is `role="img"` with a summary;
  - the "Song by song" list is its text alternative;
  - a zone is never told by colour alone (height, markers and "Z4" labels);
  - the zone colours (4.0 to 11.1:1) and the stage text meet 3:1 against `#26272B`; the grid
    lines are decorative;
- **Own code, no chart library:** an SVG component in React.

### 8. Component stack

- **Tailwind CSS v4.** `tokens.css` stays the one source of colour, spacing and type: Tailwind's
  `@theme` maps to its variables, and nothing else defines a colour.
- **Radix UI primitives**, in shadcn/ui-style components copied into `src/components/ui/` (code
  we own): Button, Card, Chip and ToggleGroup, Dialog, DropdownMenu, Tabs, Switch, Tooltip, Toast
  (`sonner`), Progress, Select.
- **Libraries:** `lucide-react`; `@fontsource-variable/inter` and `@fontsource/archivo`.
- **Dialog under `style-src 'self'` (amended by #331).** Radix's modal mode locks the page scroll
  with an injected `<style>`, which the policy refuses (a console error per open, no lock). The
  CSP is not loosened (no nonce, no `'unsafe-inline'`). `Dialog` runs Radix non-modal and adds the
  modal behaviour in code we own (`components/ui/dialog.tsx`, `lib/modalLock.ts`): its own overlay
  (a click closes), `overflow-hidden` on the root element, `inert` on every other child of
  `<body>` (no focus, no clicks, out of the accessibility tree), a Tab loop, `aria-modal`, and a
  dialog taller than the window scrolls inside itself. Any new modal component uses `Dialog`; a
  library that injects a `<style>` or sets a `style` attribute is not used. The e2e console check
  has no allow-list. `DropdownMenu` is non-modal for the same reason; Radix `Select` (not used by a
  page yet) has the same scroll lock and needs the same treatment before a page uses it.
- **Not added:** chart, state or form libraries; the router stays (it is small and tested). Drag
  and drop (`@dnd-kit`) only if reordering songs is ever asked for.
- **Bundling and checks.** Everything is bundled by Vite and served from our origin. `npm audit`
  keeps gating. Package licences must be MIT, ISC, Apache-2.0 or OFL; the Spotify logo is the one
  asset under its owner's brand terms. The Reviewer checks licences from `package-lock.json` in
  each PR that adds a package. `no-third-party-hosts.test.ts` stays, with exactly one allowed host
  (§9).

### 9. Cover art

Only **album covers of chosen songs** are shown, and only from data classbeat already keeps:

- **Where they come from.** The track entries of `mowl-workouts` (contract rows M-1 and M-5 of ADR
  0009) gain one field, `image`. It holds the URL of the album's smallest image that is at least
  64 px, or `null`. Getting it is a library release: 0.7.0 by the local Architect, then the wheel
  pin (issue L2 in §11). Playlist images are not used: they would need a Spotify read from the web
  process, which ADR 0009 does not allow, or a stored copy. They are also often served from other
  hosts.
- **Storage.** The URL is kept only as a new `cover` field on each song of the `song_list` revision
  (ADR 0008 §5). The content becomes `v: 2`, `v: 1` stays readable, and `content_hash` covers the
  field like the rest. Under the retention rules (ADR 0008 §8, guard 10's `RETENTION` registry,
  ADR 0010), it is deleted with the class and the account, like the title and artist beside it.
- **Validation.** The backend keeps a URL only if the whole string matches
  `https://i\.scdn\.co/image/[0-9a-f]{40}` (`re.fullmatch`, so not `$`, which also matches before
  a trailing newline). It checks both before storing and before returning. Anything else becomes `null`. The URL is never taken
  from model output and never put into model input: the stages' track summaries leave it out.
- **Browser.**
  - The browser loads covers straight from `https://i.scdn.co`, through one `CoverArt` component
    with `<img referrerpolicy="no-referrer" loading="lazy">`.
  - UI7 adds the app's first Content-Security-Policy as a **response header** from the web process,
    not a `<meta>` in `index.html`. Its `img-src` is `'self' https://i.scdn.co`; the other
    directives follow the threat model's strict CSP, and the e2e suite runs with the header on.
  - UI7 also adds a page-wide `Referrer-Policy: no-referrer` header.
  - The URL check stays the main control. The guard test exempts that host in `CoverArt` only.
- **Where covers appear.** Covers show in the Songs for approval rows and its 2×2 mosaic (the first
  four songs). They also show on the Class page's song list and on the Music page's tiles for
  playlists classbeat made, as a mosaic from their `song_list`. MOWL playlists (the New class
  picker, Music's "Your playlists") always use a neutral tile with the playlist's initials, and so
  does a song without a cover or with a failed load.

The request carries no referrer, no classbeat cookie and no classbeat data. The viewer's IP address
and browser still reach Spotify's CDN; the M6 privacy notice says so. This is the one place the
browser contacts a third party, and it changes a guard test, so UI7 has an Opus review.

**Contracts this amends in part** (each carries an "amended by ADR 0013 §9" note):

- **ADR 0009 §10.** "`images` … values from responses are not requested, stored or shown" gains
  one exception: the album image URL on M-1 and M-5 track entries. It is never requested by the
  server. It is stored only in `song_list` after the host-pinned check above, and shown only through
  `CoverArt`.
- **ADR 0009, contract table.** Rows M-1 and M-5 gain `image`. M-3 is unchanged.
- **ADR 0008 §5.** `song_list` content `v: 2` with `cover` per song.
- **ADR 0008 §8.** The first retention row adds "album cover URL".
- **Threat model T73.** It is narrowed the same way, and UI7 adds an entry for the cover-art host.

### 10. Accessibility and devices

- **WCAG 2.2 AA:**
  - the contrast pairs in §2;
  - 44 px targets;
  - real buttons, links and labelled inputs;
  - visible focus;
  - `aria-pressed` on chips, `role="switch"` on switches, tabs with the Radix roles;
  - reduced motion respected.
- **Phones first.** Instructors prepare classes on phones and tablets, so every page is designed
  at 390 px first.
- **Tests.**
  - Vitest and Testing Library keep testing behaviour.
  - Playwright adds a viewport pass at 390 px and 1440 px for Home, New class and Class.
  - Playwright adds an axe check (`@axe-core/playwright`, dev dependency, e2e only) on those pages.

### 11. Delivery

Milestone **MU Night Studio UI** (roadmap Amendment 8): it runs beside M4 and M5 and is done
before M6 starts. Its issues are #242 (L2), #243 (UI1), #246 (UI2), #247 (UI3), #248 (UI4),
#249 (UI5), #250 (UI6), #251 (UI7), #253 (UI8), #254 (UI9) and #255 (UI10). Each issue's
"Depends on" line names the in-flight M4, M3c and M5 issues it waits for.

- **Developers** are Sonnet, except UI7 and L2 (Opus). L2 runs in the private library repo with
  the local Architect, as L1 did (ADR 0008).
- **Reviewers** are a fresh Sonnet, except L2, UI2, UI6, UI7 and UI8, which get a fresh Opus. L2
  changes the library the worker runs with users' tokens. UI2 may add an owner-scoped field to the
  class list, which is tenant isolation. UI7 changes a
  guard test and adds the CSP. UI6 restyles the consent switch, two-step sign-in, sessions and
  account deletion. UI8 sends the approval hash.
- **API calls.** UI6, UI8 and every other issue keep each API call, CSRF and step-up exactly as they
  are; a restyle changes no existing request. Any new read is named in its issue, is owner-scoped
  and has a cross-user test. There are two: UI2's optional `profile` field on the class list, and
  UI8's list of the playlists classbeat made.

| # | Issue | Depends on |
|---|---|---|
| UI1 | Tailwind v4, tokens (§2), fonts, `src/components/ui` primitives, theme switch | — |
| UI2 | App shell: sidebar, icon rail, tab bar, focus layout; Home; My classes cards | UI1 |
| UI3 | Class profile (§7) and the class page layout (§5) | UI1, UI2; the in-flight M4 class-page work and M3c's Z7 (ADR 0012) |
| UI4 | New class page (§5), same API | UI2; M4 FE3 (#184), which owns the form until it merges |
| UI5 | Designing page (§5), wording table (§6) | UI2; M4 FE3 (#184), which owns the job page until it merges |
| UI6 | Account with tabs, connection cards, usage details tucked away, Appearance | UI2; M5 FE1 (#224), which adds the plan and usage parts |
| L2 | `mowl-workouts` 0.7.0: `image` on M-1 and M-5 track entries (local Architect, private repo), then the wheel pin and `uv lock` | — |
| UI7 | Cover art: `song_list` `v: 2` `cover`, URL validation, `CoverArt`, guard-test exemption, CSP header, `Referrer-Policy`, threat model | UI1, L2; the in-flight M4 song work that owns `classbeat/songs/` (ADR 0012) |
| UI8 | Songs for approval in the Spotify-like layout; Music page | UI7; M4's approval API |
| UI9 | Landing, Sign in and Sign up restyle | UI2 |
| UI10 | Playwright viewport and axe pass; the first-load bundle size from the Vite build report | UI3 to UI6 |

Nothing starts before this ADR is accepted. After that:
- UI1, UI2, UI9 and L2 can run beside M4 and M5.
- UI3, UI4, UI5, UI6, UI7 and UI8 wait for the in-flight M4, M3c and M5 work they touch, as their
  rows and issues say.
- M4 and M5 issues that add UI (the song picking choice, Songs for approval, Change songs, the plan
  and admin pages) use the new components once UI1 has merged. UI2 restyles any that merged
  before it.

### 12. Owner answers (2026-10-30)

Nothing is open.

1. **Zone colours:** the MOWL app is the reference, but nothing is copied exactly ("probably
   copyrighted"). §2's values are similar, not identical. They were set next to the owner's
   screenshot of MOWL's editor, which was shared in chat and not committed.
2. **Zones 5 to 7:** one red, as in MOWL. MOWL shows a flame above zone 6 (confirmed) and an owl
   above zone 7. classbeat shows a lucide flame and a lucide bolt (§2, §3).
3. **Default theme:** Dark, with Light and Same as device in Account. Accepted.
4. **The Music page in the main navigation:** no preference. The Architect keeps it, because it is
   where playlists and song picking live, the part the owner wants to feel like Spotify. Without
   Spotify it shows the MOWL playlists and the connect card. To be revisited after the testers have
   used it.
5. **Fonts:** no preference, so Inter and Archivo are kept.

## Alternatives

- **Keep the current tokens and restyle lightly.** Rejected: it doesn't meet the owner's brief
  (MOWL-like classes, Spotify-like music, easier account).
- **MUI or Mantine.** Rejected: both are full design systems with their own look, which is harder
  to bend into a MOWL-like stage and Spotify-like lists. Radix primitives give accessible behaviour
  with no look, and the components stay in our repo.
- **Copy MOWL's app look, colours or marks exactly** (layout, chrome, typeface, icons, the owl,
  the exact zone colours). Rejected by the owner: it is probably protected, and it would make
  classbeat look like a MOWL product. classbeat takes the ideas (rainbow zones, one red for the
  hardest, a mark above zones 6 and 7), with its own values and icons it chose.
- **Spotify green as the accent.** Rejected: it breaks Spotify's branding rules, and it would
  clash with zone 3.
- **Proxy cover art through our backend.** Rejected: it adds web-process egress to a new host and
  caches Spotify content. Loading directly with no referrer is simpler and keeps nothing.
- **A chart library (Recharts, visx).** Rejected: the profile is a row of bars plus a song strip.
  Our own SVG keeps the exact look, the keyboard behaviour and a small bundle.

## Consequences

- **ADR 0001 is superseded in part.** Its Frontend bullet "Our own design tokens … System fonts; no
  runtime requests to third-party hosts. No MOWL logo, owl, colours or copied assets" now reads:
  our own tokens and brand colour, zone colours similar to MOWL's but not identical (§2), bundled fonts, third-party UI
  libraries allowed, no runtime requests to third-party hosts except Spotify cover art (§9), and
  no MOWL logo, owl or copied assets.
- **Roadmap §8's** "Own name, colours, fonts and icons" and its Class row's "own palette" are
  amended the same way (Amendment 8).
- **ADR 0009 §10 and its contract rows M-1 and M-5, ADR 0008 §5 and §8, and threat-model T73**
  are amended in part by §9. Each carries an "amended by ADR 0013 §9" note (#241).
- **AGENTS.md's** frontend notes ("System fonts and local assets only …") are updated by UI1 and
  UI7 when they land.
- `tokens.css` is rewritten in UI1. Every component moves to the new tokens. The old teal zone
  ramp goes.
- The bundle grows by Tailwind's output, Radix primitives, two font families and icons. The
  target is under 250 kB gzip of JavaScript for the first load.
- The threat model gains an entry for the cover-art host in UI7: a malicious image URL, the CDN
  seeing the viewer's IP address, and the mitigations from §9.
