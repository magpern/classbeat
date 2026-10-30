# ADR 0013: UI design: the Night Studio look, navigation and components

Status: Proposed

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
   colours on the bars, or colours slightly off but similar.
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
- **One big next step.** Each screen has one primary button. New class, Account and usage are in
  the same place on every page.

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

**Effort zones.** MOWL's seven zones and names (`mowl.workout.ZONE_NAMES`), coloured as in the
**MOWL app** (owner, 2026-10-30): grey Z1, blue Z2, green Z3, yellow Z4, and **one red for Z5 to
Z7**. Above the bars, a **flame** marks zone 6 and a **bolt** marks zone 7. MOWL marks zone 7 with
an owl, but that is MOWL's mascot, so classbeat uses its own mark (§3). The values below are close
to the app's, not sampled from it. They are replaced by values sampled from a screenshot of a
workout in the MOWL app once the owner provides one (§12). They are the same in both themes:

| Zone | Name | FTP | Colour | Marker above the bar |
|---|---|---|---|---|
| 1 | Recovery | under 55 % | `#8E99A8` grey | — |
| 2 | Endurance | 56–75 % | `#3D8BFF` blue | — |
| 3 | Tempo | 76–90 % | `#2FBF71` green | — |
| 4 | Threshold | 91–105 % | `#FFD23F` yellow | — |
| 5 | VO2max | 106–120 % | `#FF4D4D` red | — |
| 6 | Anaerobic | 121–150 % | `#FF4D4D` red | flame (`lucide` `Flame`) |
| 7 | Neuromuscular | all out | `#FF4D4D` red | bolt (`lucide` `Zap`) |

Zones 5 to 7 share a colour, so they are told apart by bar height, the marker and the zone number
in every label. Zone colours are used only for effort, never for buttons or status.

**The stage.** A class profile always sits on a near-black stage (`stage #0B0C10`, grid lines
`#23252F`, text `#E9EBF1` / `#9AA0B2`), in the light theme too, like the studio screen. This also
keeps the yellow zone legible, since yellow on white would fail the 3:1 rule for graphics.

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
  owl, wordmark, typeface, screenshot or asset, and doesn't reproduce MOWL's app chrome. Where MOWL
  puts its owl over zone 7, classbeat puts its own bolt. "MOWL" appears only as text naming the service
  ("Save to MOWL", "Connected to MOWL"), and nothing may suggest classbeat is made by MOWL (roadmap
  "Development process").
- **Spotify.** Screens that show Spotify data follow the Spotify Design & Branding Guidelines for
  developers:
  - the official Spotify logo, from Spotify's brand resources and bundled with the app, next to
    Spotify metadata;
  - cover art shown unaltered: no crop, overlay, blur or tint, with a 4 px radius (8 px on large
    sizes);
  - track and artist names never rewritten;
  - links that say OPEN SPOTIFY or PLAY ON SPOTIFY.

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
| `/music` | Music: the user's MOWL playlists and the playlists classbeat made, with Open Spotify links; a "Connect Spotify" card while not connected |
| `/account` | Account, with tabs: Connections, Plan and usage, Sign-in and security, Your data, Appearance |
| `/account/spotify` | The Spotify wizard and settings. It stays where it is, because ADR 0009's callback ends there |
| `/admin` | Admin (M5), in the same shell |

### 5. Screens

Each item gives the content and the **one primary action**.

- **Landing.** A headline, an example class profile on the stage with its song list (synthetic and
  invented, like the e2e fake), three steps (describe, approve songs, save to MOWL) and **Sign up
  with an invite code**. Sign in and Sign up are focus pages using the same components.
- **Home.**
  - A greeting and a "Design a new class" card: **Design a class**, plus preset chips that prefill
    the form (45 min hills, 30 min intervals, 60 min endurance).
  - A "Get set up" checklist (account, MOWL, optional Spotify), shown until it is complete.
  - Recent classes as cards: a mini profile on the stage, title, length, TSS and song count, and a
    status chip.
- **New class** (one scrolling page of cards, not a multi-step wizard):
  - name;
  - length as chips plus "Other length";
  - music: **Pick songs for me** (song picking, ADR 0008) or **Use my playlist**, with style chips
    for era and genre plus free text;
  - the user's playlists as a Spotify-like tile grid;
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
  - **Approve songs** (round, violet), Ask for changes, OPEN SPOTIFY, and the "waiting until"
    date;
  - the track rows, each with a replace toggle and its note;
  - a request box;
  - the privacy sentence.
- **Class.**
  - A header with the title, save-state chips ("Version 3 saved to MOWL", "Version 4 has changes
    not in MOWL yet") and **Save to MOWL** / **Update in MOWL**, with Change songs and a ··· menu
    (Versions, Delete).
  - Stat tiles: Length, TSS, Songs, Climbs, Sprints, Hardest zone.
  - The profile (§7) and "Time in each zone".
  - Notices, such as a song with no measured tempo.
  - "Song by song" rows with a zone strip, start time, part and song, zones, RPM, position and cue.
  - A side column with the **Change it** chat (bubbles, what changed, changes left) and Versions.
  - The uncertain panels keep their meaning, restyled as warning cards headed "Check needed".
- **My classes.** A card grid like Home's, with filters (All, Saved to MOWL, Drafts) and search.
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
  zone, as today). **Colour is the zone** (§2). Bars sit on the stage with faint horizontal grid
  lines and a time axis every 5 minutes.
- **Markers** (§2): a flame sits above every zone 6 bar and a bolt above every zone 7 bar,
  centred, in the stage's text colour. On a bar narrower than the icon, the icon overflows it
  evenly. Bar heights leave room for the marker (about 6 % plus 10.5 % per zone).
- **Songs** run along the top as a strip of alternating blocks, each with the title and BPM, as
  wide as the song's time in the class. Before the class API exposes the song list with start
  times, the strip is hidden.
- **Selecting a bar.** Hover, tap, or left and right arrow keys once the chart has focus. The chosen
  interval is outlined, and a detail bar below the chart shows its zone and name, its times, RPM
  (range or "on the beat"), seated or standing, and its cue.
- **Time in each zone:** a stacked bar plus a legend with times.
- **Accessible:**
  - the chart is `role="img"` with a summary;
  - the "Song by song" list is its text alternative;
  - a zone is never told by colour alone (height, markers and "Z4" labels);
  - the stage colours meet 3:1 against `#0B0C10`.
- **Own code, no chart library:** an SVG component in React.

### 8. Component stack

- **Tailwind CSS v4.** `tokens.css` stays the one source of colour, spacing and type: Tailwind's
  `@theme` maps to its variables, and nothing else defines a colour.
- **Radix UI primitives**, in shadcn/ui-style components copied into `src/components/ui/` (code
  we own): Button, Card, Chip and ToggleGroup, Dialog, DropdownMenu, Tabs, Switch, Tooltip, Toast
  (`sonner`), Progress, Select.
- **Libraries:** `lucide-react`; `@fontsource-variable/inter` and `@fontsource/archivo`.
- **Not added:** chart, state or form libraries; the router stays (it is small and tested). Drag
  and drop (`@dnd-kit`) only if reordering songs is ever asked for.
- **Bundling and checks.** Everything is bundled by Vite and served from our origin. `npm audit`
  keeps gating; licences must be MIT, ISC, Apache-2.0 or OFL. `no-third-party-hosts.test.ts` stays,
  with exactly one allowed host (§9).

### 9. Cover art

- **Library change.** `mowl-workouts` gains cover-art URLs on track and playlist entries: the
  smallest image of at least 64 px, from Spotify's `album.images` and `images`. That is a library
  release: 0.7.0, a rebuilt wheel, `uv lock`.
- **Storage.** classbeat stores the URL only in the owned rows that already hold the song (the
  `song_list` revision, ADR 0008 §8). It is deleted with them, under the same Spotify-derived
  retention rules (ADR 0010).
- **Validation.** Before storing, and again before returning one, the backend accepts only
  `https://i.scdn.co/image/<hex>`. Anything else is dropped. It is never taken from model output.
- **Browser.** The browser loads covers straight from `https://i.scdn.co`. `<img
  referrerpolicy="no-referrer" loading="lazy">` is used in one `CoverArt` component. The app's CSP
  (when it lands) has `img-src 'self' https://i.scdn.co`. The guard test exempts that host in that
  one file only.
- **Fallback.** With no URL, or a failed load, a neutral tile is shown with the playlist's
  initials. MOWL playlists without Spotify connected always use the tile.

This is the one place the browser contacts a third party. The user has connected that Spotify
account, and the request sends no referrer, cookie or classbeat data. It changes a guard test, so
it is reviewed by Opus.

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

New milestone "UI Night Studio". Developers are Sonnet, except UI7. A fresh Sonnet reviews each
issue, except UI7 (Opus: a guard test and CSP).

| # | Issue | Depends on |
|---|---|---|
| UI1 | Tailwind v4, tokens (§2), fonts, `src/components/ui` primitives, theme switch | — |
| UI2 | App shell: sidebar, icon rail, tab bar, focus layout; Home; My classes cards | UI1 |
| UI3 | Class profile (§7) and the class page layout (§5) | UI1, UI2 |
| UI4 | New class page (§5), same API | UI2 |
| UI5 | Designing page (§5), wording table (§6) | UI2 |
| UI6 | Account with tabs, connection cards, usage details tucked away, Appearance | UI2 |
| UI7 | Cover art: `mowl-workouts` 0.7.0 images, URL validation, `CoverArt`, guard-test exemption, CSP `img-src` | UI1; M4 SJ1 |
| UI8 | Songs for approval in the Spotify-like layout; Music page | UI7; M4's approval API |
| UI9 | Landing, Sign in and Sign up restyle | UI2 |
| UI10 | Playwright viewport and axe pass | UI3 to UI6 |

UI1 to UI6 and UI9 can start now beside M4. M4 issues that add UI (song picking choice, Songs for
approval, Change songs) use the new components once UI1 has merged.

### 12. Owner answers (2026-10-30) and what is still open

1. **Zone colours:** the MOWL app is the reference. *Open:* a screenshot of a workout in the MOWL
   app to sample the exact values. §2 holds close values until then. The screenshot is shared in
   chat, never committed, because it is MOWL's material.
2. **Zones 5 to 7:** one red, as in MOWL. MOWL shows a flame above zone 6 and an owl above zone 7;
   classbeat shows a flame and its own bolt (§2, §3). *Open:* the owner described the zone 6 mark
   as a "flamenco", read here as a flame, to be confirmed.
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
- **Copy MOWL's exact palette and app look.** Rejected: it makes classbeat look like a MOWL
  product. The rainbow zone convention gives the familiarity without copying.
- **Spotify green as the accent.** Rejected: it breaks Spotify's branding rules, and it would
  clash with zone 3.
- **Proxy cover art through our backend.** Rejected: it adds web-process egress to a new host and
  caches Spotify content. Loading directly with no referrer is simpler and keeps nothing.
- **A chart library (Recharts, visx).** Rejected: the profile is a row of bars plus a song strip.
  Our own SVG keeps the exact look, the keyboard behaviour and a small bundle.

## Consequences

- **ADR 0001 is superseded in part.** Its Frontend bullet "Our own design tokens … System fonts; no
  runtime requests to third-party hosts. No MOWL logo, owl, colours or copied assets" now reads:
  our own tokens and brand colour, MOWL-like zone colours (§2), bundled fonts, third-party UI
  libraries allowed, no runtime requests to third-party hosts except Spotify cover art (§9), and
  no MOWL logo, owl or copied assets.
- **Roadmap §8's** "Own name, colours, fonts and icons" is amended the same way (Amendment 8).
- **AGENTS.md's** frontend notes ("System fonts and local assets only …") are updated by UI1 and
  UI7 when they land.
- `tokens.css` is rewritten in UI1. Every component moves to the new tokens. The old teal zone
  ramp goes.
- The bundle grows by Tailwind's output, Radix primitives, two font families and icons. The
  target is under 250 kB gzip of JavaScript for the first load.
- The threat model gains an entry for the cover-art host in UI7: a malicious image URL, tracking
  by the CDN, and its mitigations from §9.
