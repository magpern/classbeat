# ADR 0010: Provider permissions for live activation

Status: Accepted (the table is updated as answers arrive)

## Context

The roadmap separates technical completion (tests against fakes) from live activation (real
MOWL, Spotify and model calls, owner-controlled; roadmap "Verification"). Live use of some
features needs a provider's permission first (§10, track F). This ADR is the single place the
live activation gate reads. The questions themselves are in
[docs/providers/questions.md](../providers/questions.md).

What the desk research found (2026-10-27, public pages only):

- Spotify's Developer Terms require a separate security code for each SDA and allow no more
  than one per SDA (§VI 1(b)), which leaves BYOK for a hosted service unclear (S1).
- Spotify's Developer Policy forbids ingesting Spotify Content into an AI model (§III 14),
  products targeted for use by businesses and playing Spotify publicly from a business
  (§III 10); commercial use by SDAs is not permitted (§IV 2) (S2, S3). Spotify Content may not
  be stored indefinitely (§IV 3.1(a)) (S4).
- Music in MOWL is normally an imported Spotify playlist, so its titles, artists, IDs and
  MOWL's analysis of those tracks are Spotify-derived; getting them via MOWL is no exemption
  (roadmap §10).
- Development Mode: 5 users per Client ID, allow-list, the app owner needs Premium; a developer
  may hold up to 25 Development Mode Client IDs (from July 2026). Extended Quota Mode needs an
  organization with at least 250k MAU.
- The February 2026 Web API changes rename playlist `/tracks` endpoints to `/items` and remove
  `POST /users/{user_id}/playlists` in favour of `POST /me/playlists`; the migration guide says
  existing Development Mode apps moved on 2026-03-09. M4 targets the new endpoints only.
- MOWL publishes no terms, API documentation or partner programme that could be found.

## Decision

The live activation gate allows a feature against real accounts only when its row below says
so **and** the owner authorizes the specific run (AGENTS.md, safety envelope). Until then the
feature runs against fakes only.

| Feature | Needs | Status | Allowed until answered |
|---|---|---|---|
| Save and update a class in the user's own MOWL account | MOWL M1 | unasked | Owner's own account only, owner-authorized runs; no testers |
| Design on music already in MOWL whose tracks come from Spotify | MOWL M1, Spotify S2, S3 | unasked | Fakes only: no Spotify-derived titles, artists, IDs or analysis to a model |
| Design on music in MOWL that doesn't come from Spotify | MOWL M1 | unasked | Owner's own account only, owner-authorized runs |
| Spotify sign-in with the user's own Client ID (BYOK) | Spotify S1 | unasked | Owner's own Spotify app and account only, owner-authorized runs |
| Claude choosing songs from Spotify metadata and MOWL analysis | Spotify S2, S3 | unasked | Fakes only; no real Spotify metadata to a model |
| Creating the pool and final playlists in a user's Spotify account | Spotify S1, S3 (and S2, since the pool is model-chosen) | unasked | Owner's own account only, owner-authorized runs, private playlists, songs chosen by hand or by fakes, never by a model on real metadata |
| Keeping track lists and timing for saved classes | Spotify S4 | unasked | Fakes only; until S4 is answered no real Spotify Content is stored |
| Any tester beyond the owner, any paid or public use | all of the above, S5, MOWL M3 | unasked | Not allowed |
| Swedebeat features | Swedebeat (not yet asked) | unasked | Disabled flag; fakes only |

Status values: unasked, asked (date), granted (date, with the reply's wording or a link),
refused (date). A refusal disables the feature's live path and goes to the owner as a product
decision.

## Alternatives

- **Go live and ask later**: rejected; the policy text already points at S2 and S3.
- **Route song choice around Spotify** (another catalogue or transfer service): out of scope by
  the roadmap; revisited only before M7.

## Consequences

- Development continues through M6 against fakes; no milestone waits on these answers.
- In practice the beta can't use Claude on real Spotify music until S2 and S3 are answered.
  The roadmap's "the beta runs on existing MOWL playlists/mixes" holds only for music that
  doesn't come from Spotify; this reading is recorded for the owner in `docs/status.md`.
- The owner sends the questions (waiting for the owner in `docs/status.md`) and records the
  replies here through a PR.
- M4 uses `POST /me/playlists` and the `/items` endpoints, with tests on the new response shape.
