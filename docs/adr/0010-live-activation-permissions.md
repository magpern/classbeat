# ADR 0010: Provider permissions for live activation

Status: Accepted (amended 2026-10-28: owner's risk acceptance for the beta circle)

## Context

The roadmap separates technical completion (tests against fakes) from live activation (real
MOWL, Spotify and model calls, owner-controlled; roadmap "Verification"). Live use of some
features needs a provider's permission first (§10, track F). This ADR is the single place the
live activation gate reads. The questions themselves are in
[docs/providers/questions.md](../providers/questions.md) (written for track F, which was closed
unsent on 2026-10-28; the research below is kept as the record of what the terms say).

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

**Owner decision (2026-10-28): the providers are not contacted.** classbeat starts as a
small personal app for the owner and friends the owner invites: invite-only, free,
non-commercial. For that circle the owner accepts the provider-terms risks below instead of
waiting for answers, so every feature's live path except Swedebeat may be switched on once it
is technically complete. The testers are the owner's friends and are told directly; the app shows no
separate risk notice.

Accepted risks (also in the threat model):

- **Spotify:** song picking sends Spotify-derived metadata and MOWL's analysis of it to Claude,
  which the Developer Policy forbids (§III 14); each tester connects their own Spotify
  developer app (BYOK), whose fit with the one-Client-ID-per-application rule (§VI 1(b)) is
  unconfirmed. The likely consequence is Spotify disabling a developer app or, less likely,
  an account; each tester carries it for their own app and account.
- **Spotify, business use and storage:** commercial use is not permitted for developer apps
  and the policy restricts products for businesses (§III 10, §IV 2), and Spotify Content may not
  be stored indefinitely (§IV 3.1(a)). The beta is free and non-commercial; testers play
  Spotify music only where their own Spotify terms allow it (not publicly in a gym); a user's
  own track lists and timing are deleted with their class or account, and a shared music
  analysis is deleted once no class of any user refers to it, so nothing Spotify-derived is
  kept indefinitely.
- **MOWL:** the API is unofficial and undocumented. MOWL may block an account or change the
  API without notice, breaking the app until it is adapted.

Features are switched on for the testers by the owner (the deploy and its settings); agents
never switch a live path on themselves. The live activation gate still requires, for every
agent-run test against a real account, the owner's authorization for that specific run
(AGENTS.md, safety envelope). Anything beyond the circle needs the providers first:

| Feature | Beta circle (now) | Beyond the circle |
|---|---|---|
| Save and update a class in the user's own MOWL account | Allowed | Needs MOWL M1, M3 |
| Design on music in MOWL that comes from Spotify, with Claude | Allowed | Needs MOWL M1, Spotify S2, S3 |
| Design on music in MOWL that doesn't come from Spotify, with Claude | Allowed | Needs MOWL M1 |
| Spotify sign-in with the user's own Client ID (BYOK) | Allowed | Needs Spotify S1 |
| Claude choosing songs from Spotify metadata and MOWL analysis | Allowed | Needs Spotify S2, S3 |
| Creating the pool and final playlists in a user's Spotify account (private) | Allowed | Needs Spotify S1, S2, S3 |
| Keeping track lists, timing and shared analyses for saved classes | Allowed while a class refers to them, then deleted | Needs Spotify S4 |
| Any paid or public use, or users the owner doesn't know | Not allowed | Needs all of the above, S5, MOWL M3, and agreements (M7) |
| Swedebeat features | Disabled flag; fakes only (its app key isn't used, see the threat model) | Needs Swedebeat |

The questions stay in [docs/providers/questions.md](../providers/questions.md), unsent, for a
later public or paid phase.

## Alternatives

- **Ask the providers first** (this ADR's first version): the owner chose not to involve them
  for a personal app; the questions are kept for a public or paid phase.
- **Go live without recording the risk**: rejected; the decision and its risks are written down
  here so a later public phase starts from them.
- **Route song choice around Spotify** (another catalogue or transfer service): out of scope by
  the roadmap; revisited only before M7.

## Consequences

- Development continues through M6 against fakes; no milestone waits on these answers.
- Live use in the beta circle needs no provider answer, only the owner's authorization for
  each agent-run live test. Milestone F closes as not pursued.
- Going beyond the circle (public, paid, or strangers) reopens this ADR: the questions are sent
  first and the "Beyond the circle" column applies.
- M4 uses `POST /me/playlists` and the `/items` endpoints, with tests on the new response shape.
