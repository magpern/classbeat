# Ideas

This file collects ideas that are **not planned or committed**. Plan decisions live in the
[roadmap](roadmap.md), the [ADRs](adr/) and [status.md](status.md). Nothing here is authorised
work. To add an idea, add a section at the end with the same fields: one-line description,
Status, Notes/constraints, Decision needed from the owner. Anything that touches a provider's
terms must re-read [ADR 0010](adr/0010-live-activation-permissions.md) first.

## 1. Funding and monetisation (brainstorm with the owner, 2026-10-31)

### 1a. Rewarded ads for free-tier classes

- **Description:** "Watch an ad to generate a class."
- **Status:** idea, not planned. Verdict so far: not recommended; revisit after cost per class is measured.
- **Notes/constraints:**
  - Rewarded video typically pays roughly $10-30 per 1,000 completed views in the US and
    western Europe (about $0.01-0.03 per ad, up to about $0.05 in top markets). These are rough
    figures from memory; verify before relying on them. Web pays less than in-app, and ad
    blockers cut it further.
  - Providers: Google AdMob, AppLovin, Unity Ads and ironSource for apps; Google Ad Manager
    rewarded ads and the AdSense game placement API for the web; AdinPlay and Playwire for
    small publishers. Small sites may not be accepted.
  - A class costs real model money (not yet measured; it is measured at live activation), so a
    cent or two per ad likely does not cover it.
  - Conflicts: ADR 0010 says the beta is free and non-commercial, and ads are commercial use;
    third-party scripts and cookies sit near the MOWL and Spotify connections; a GDPR consent
    banner would be needed; the CSP (`style-src 'self'`, no inline scripts) would have to be
    loosened for ad domains.
- **Decision needed:** whether to look at this again once the cost per class is known.

### 1b. Tip jar ("buy me a beer")

- **Description:** A plain outbound link where users can give a small amount.
- **Status:** idea, not planned.
- **Notes/constraints:**
  - The URL is an admin setting, empty by default. No embeds, no perks or gating: a perk would
    make it a sale.
  - Options: Swish or a Stripe payment link, Ko-fi (no fee on donations), Buy Me a Coffee
    (about 5% fee), GitHub Sponsors.
  - Donations are taxable income in Sweden. It will not cover the model bill at beta size;
    quotas are what control cost.
- **Decision needed:** which platform, and who receives the money.

### 1c. Class packs

- **Description:** One-off packs of extra classes.
- **Status:** idea, not planned.
- **Notes/constraints:** This is a sale, so the ADR 0010 "beyond the circle" rules and the M7
  agreements apply.
- **Decision needed:** whether to sell anything before M7.

### 1d. Paid tiers via Paddle (Instructor, Pro)

- **Description:** Subscriptions for higher quotas.
- **Status:** prepared and switched off (roadmap section 9, M7).
- **Notes/constraints:** Nothing new here; listed so the funding options sit in one place.
- **Decision needed:** when, if ever, to switch it on.

### 1e. Employer or club pays

- **Description:** The roadmap's Club tier: a flat fee per instructor paid by a gym or club
  (for example a gym-chain studio). Fits Swedebeat-only mode.
- **Status:** idea, not planned.
- **Notes/constraints:** Avoids consumer billing. Probably the most realistic route.
- **Decision needed:** whether to approach a gym, and what a first offer would be.

### 1f. Bring your own model key

- **Description:** Instructors enter their own model API key, so classbeat carries no model cost.
- **Status:** idea, not planned.
- **Notes/constraints:** Removes the cost, but is unlikely to work for non-technical instructors.
- **Decision needed:** none until another route fails.

### 1g. Sponsorship

- **Description:** A gym or music supplier covers the model bill in return for a credit on the page.
- **Status:** idea, not planned.
- **Notes/constraints:** No ads in the app; a credit line only.
- **Decision needed:** whether a credit line counts as commercial use under ADR 0010.

## 2. Class poster export and merch

### 2a. Export class poster

- **Description:** A high-resolution PNG or SVG of the class profile (own palette) with class
  name, duration and TSS.
- **Status:** idea, not planned.
- **Notes/constraints:** No payments and no third parties. Useful for sharing. The smallest
  first step of this section.
- **Decision needed:** whether to file it as an issue.

### 2b. Print-on-demand shirt with the class outline

- **Description:** A shirt printed with the class outline; the provider handles printing,
  shipping and VAT.
- **Status:** idea, not planned.
- **Notes/constraints:**
  - Providers: Printful, Printify, Spreadshirt (EU). Hand off the print file through their API
    or link out. Margin is a few euros per shirt.
  - Needs the owner's provider account and payment and VAT reporting.
  - No song titles, artists or album covers (Spotify-derived content). No MOWL name, logo or
    branding. The class title only if the user types their own text.
  - Send the provider only the image and order details (GDPR).
  - Selling merch is a sale: note it in ADR 0010 first.
  - Start with 2a and a link-out.
- **Decision needed:** whether to open a provider account, and whether to amend ADR 0010.

## 3. Already tracked elsewhere

- Passkeys with cross-device sign-in: issue #311, after M6.
- Swedebeat connection and music features: milestone S1, issues #362 and #363.
- Ideas derived from flywheel live in the mowl-workouts repo notes, not here.

## 4. SwedeBeat as the song source when Spotify is not connected (Amendment 28, parked 2026-11-17)

- **Description:** a class with music could use a connected SwedeBeat account when Spotify is not
  connected: the model proposes songs, SwedeBeat's `/catalog/match` verifies them (score 0.85 or more, only
  title and artist sent), and the approved list stays in classbeat with no playlist written anywhere.
- **Status:** idea, **parked**. It was planned (PR #1181, issues #1176 to #1180), then parked by the owner
  after an Opus review (FAIL) showed it deviates from the architecture more than first said. The owner's
  condition was "only if our architecture does not deviate too much".
- **Notes/constraints (from that review):**
  - **The design needs music facts a match does not give.** `choose_songs` and `fit_to_music` run on a
    per-track map with sections, drops and breakdowns, from MOWL's analysis of a Spotify playlist
    (`pool_write`, `pool_analyse`). A match gives title, length and sometimes BPM. Options: the studio's
    download-and-analyse path (`track_analysis`, a separate music kind, and it needs the owner gate **L4a**,
    the rights decision to download catalogue tracks), or a new class shape for a map without sections.
  - **Songs are identified by Spotify id** in about 28 backend modules and in the approval hash. A neutral
    `SongRef` would be needed (the planned #1177), with the source in the hashed bytes and never in the
    class API (ADR 0036 §3.4).
  - **Gates:** the existing `song_picking_admits` check (the `CLASSBEAT_SONG_PICKING` kill switch) would
    gate it too; "SwedeBeat usable" means the effective `swedebeat_enabled` flag, the gateway URL and key,
    the user's "I use Swedebeat" preference and a connection that is not `needs_reconnect`; a stored Spotify
    connection would always mean the Spotify source. A named Spotify playlist still needs Spotify
    (Amendment 25).
  - **Outage rule:** its own, not ADR 0036 §3.2's: count all-missing across the whole stage (at least 3
    songs sent), and end the stage on a 401 (the ADR 0035 §5 check, then `needs_reconnect`), a 403 or unknown
    client (`app_misconfigured`), a 429 (`rate_limited`) and a malformed answer (`invalid_response`), never
    as tool errors the model retries. `swedebeat_unavailable` would need a migration of the failure
    categories.
  - **Smaller:** a 200-character bound on the titles sent; what a SwedeBeat approval binds; the switch
    `CLASSBEAT_SWEDEBEAT_SONG_SOURCE` off in `compose.prod.yaml` and `scripts/check-prod-compose.sh`.
- **Decision needed from the owner:** L4a, or a decision to accept a map with no sections. Until then,
  Spotify stays required for a class with songs (Amendment 25).

## 5. Music providers behind one interface, or as separate services (discussed with the owner, 2026-11-17)

- **Description:** a classbeat interface that every music provider implements: checking that a song exists
  (and with which facts: duration, BPM, year), free search, playlist writes, playback, covers. Spotify and
  SwedeBeat would be the first two, so a new provider would be one more implementation. A further step
  would move each integration into its own service, like the SwedeBeat gateway (`sb-spotify-service`,
  `sb-mowl-service`).
- **Status:** idea, not planned.
- **Notes/constraints:**
  - The SwedeBeat gateway exists so that classbeat calls no SwedeBeat endpoint directly and holds no
    SwedeBeat secret (the AES login key, the S3 key, the gateway's Spotify app; ADR 0035). Spotify has a
    public API with per-user PKCE, and MOWL is reached through `mowl-workouts`.
  - A Spotify or MOWL service would move encrypted user tokens, owner isolation and key handling behind a
    new network boundary, with its own service-to-service authentication. External writes, which are at most
    once with a job lease and reconcile before retry, would cross a process boundary. Each service is another
    deploy unit on one VPS. All of that is security-critical work.
  - MOWL is the workout store, not a music provider. A separate interface for it pays off only with a
    second workout platform.
  - Even with an interface, a new provider still needs its own sign-in, consent, write safety and a fake
    for tests. Capabilities really differ: SwedeBeat gives no year, so era checks are weaker there.
- **Decision needed from the owner:** whether to plan an in-process provider interface. The recommendation
  is adapters in the same process, not services, unless a provider needs a proxy as SwedeBeat does.

