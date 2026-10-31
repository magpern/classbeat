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
  (for example a SATS-type gym). Fits Swedebeat-only mode.
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
