# ADR 0028: UI refresh: calmer screens, shorter copy, Connections as rows and dialogs

Status: Accepted (owner decisions 2026-11-09, roadmap Amendment 17). It refines
[ADR 0013](0013-ui-design-night-studio.md) (Night Studio) and does not replace it: the tokens, the
zone colours, the stage, the component stack, the CSP and the accessibility rules all stay.

Related: [ADR 0023](0023-multilingual.md) (every string in en, sv and da),
[ADR 0024](0024-mowl-qr-connect.md) (MOWL by QR), [ADR 0015](0015-swedebeat-connection.md)
(Swedebeat), [ADR 0009](0009-spotify-connection.md) (Spotify).

## Context

A review of the 2026-11-08 screenshot set (`classbeat-screenshots`, `review/2026-11-08-ui-review.md`)
found the UI solid underneath and unfinished on the surface: small visible glitches, three
different "pick one" controls on one form, the same class drawn two ways, desktop pages that use
half the width, and copy that narrates the system (about 12,000 English words; 65 strings over 25
words; 62 sentences that start with "classbeat"). A clickable design on a Claude Design canvas
explored the answer. The owner reviewed it on 2026-11-09.

## Decision

### 1. Owner decisions (2026-11-09)

1. The colours stay as designed: violet accent, the ADR 0013 zone colours and stage. Titles use
   Archivo at a wide setting (`font-stretch`), body text stays Inter.
2. **Swedebeat always shows in Connections**, as optional. The "I use Swedebeat" switch is no
   longer shown, and the rule "until then classbeat shows nothing about Swedebeat" in `swed.intro`
   is gone. The stored preference (#525) stays in the backend, where it still decides whether
   anything is sent to Swedebeat: the Connect dialog stores it as on, then connects; Disconnect
   stores it as off, then disconnects. No backend change. The deployment flag `swedebeat_enabled`
   and the admin "Swedebeat sign-in for" setting still decide whether Swedebeat can be
   **connected**: when it cannot, the row stays and says so, it is not hidden.
3. **MOWL by QR is on by default.** `mowl_qr_enabled` defaults to true (settings, compose,
   `.env.example`). This supersedes "off by default until the owner's live scan" in
   [ADR 0024](0024-mowl-qr-connect.md). The switch stays as an off-switch for an operator. The
   live scan is still worth doing; it is now a verification, not a gate.
4. No dark screenshot set is needed; both themes are checked in the component tests and by the
   token tests.

### 2. Principles added to ADR 0013 §1

- **Say it once.** A heading, a body line and a button never repeat each other.
- **Instructor words, second person, "we" for the product.** A screen does not use "classbeat"
  as the subject of a sentence ("classbeat could not tell …"). The product name stays in
  marketing, legal and mail text.
- **Length limits** for new and changed strings: button 1 to 3 words; label at most 4; helper
  text at most 12 words and only when the field is ambiguous; error = what happened and what to
  do, at most 20 words; confirmation at most 25 words. Edge cases, privacy mechanics and "what
  exactly is deleted" go in a "Details" disclosure or on the help page. Reviewers check this.
- **One control per job.** One segmented control for choosing one of a few options (sport,
  length, music source, effort, genre filters); cover tiles only where a cover is the choice
  (playlists). One stepper. One list row. One sticky action bar for the primary action of a long
  form or list.
- **The primary action is not buried.** Class page: Save to MOWL in the header. New class and
  song approval: a sticky bar (right column on desktop, bottom bar on phone).

### 3. Connections (Account)

A list of three rows: MOWL, Spotify, Swedebeat. Each shows a mark, the name, **Optional** where
it is, one line (what it is for, or "Connected" and the account) and one button, **Connect** or
**Disconnect**. Connect opens a dialog for that service:

- **MOWL:** "Scan code" (the default, when `mowl_qr_enabled`) and "Use password" tabs. A line
  that the password is not kept.
- **Spotify:** the three setup steps (create the app, add the redirect URI, paste the Client ID)
  and Continue to Spotify. The `/account/spotify` page stays reachable for the working-playlist
  consent and "Change Client ID".
- **Swedebeat:** email and password, with the existing "signed in on another device" step.

The consent switch for the working playlist and the long disconnect texts move into the
Disconnect confirmation and a "Details" disclosure. All existing security behaviour stays: step-up
after the recent-sign-in window, rate limits, no password stored, no secret in the browser.

### 4. Sign-in

"Sign in with Google" and "Sign in with Microsoft" use each provider's own button look and logo
(their branding guidelines), above "or with email". Both stay dark-theme buttons in both themes.

### 5. Delivery

One PR per row, each independent, each with its tests, in this order: glitch fixes; Connections
and the two defaults; sign-in branding; tokens and the shared components; the class page; New
class; song approval; Home and Landing; the copy pass (one PR per area). The Claude Design canvas
is the visual reference, not code: it uses inline styles, which the CSP forbids.

## Alternatives

- **A full rebuild of the frontend.** Rejected: the tokens, components, tests, i18n and
  accessibility work are the expensive part and are sound.
- **Keep Swedebeat hidden until switched on.** Rejected by the owner: users should see it exists.
- **Keep QR off until the live scan.** Rejected by the owner (2026-11-09).

## Consequences

- Every UI PR touches strings in en, sv and da and the e2e specs that find elements by name.
- Turning QR on by default means the MQTT connection to MOWL's broker is made on user request in
  every deployment that does not set `MOWL_QR_ENABLED=false`. The ADR 0024 controls (rate limits,
  allowlists, TLS-only, no secret to the browser) are unchanged and still apply.
- The Swedebeat preference becomes an implementation detail of Connect and Disconnect. Removing
  it from the backend is a later clean-up, not part of this work.
