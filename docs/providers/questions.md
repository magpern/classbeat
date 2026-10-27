# Questions for the providers (track F)

Ready for the owner to send. Agents never contact providers (AGENTS.md, safety envelope).
Sources were read on 2026-10-27; the owner should confirm the quoted wording on the live pages
before sending, because it was retrieved through an automated reader.

## Spotify (developer support)

Context to include: classbeat is an invite-only, free, non-commercial beta for a handful of
indoor-cycling instructors. Each instructor creates their own Spotify developer app and
connects it to classbeat (Authorization Code with PKCE, no client secret). classbeat creates
private playlists in the instructor's own account.

| # | Question | Term it concerns |
|---|---|---|
| S1 | Is a hosted service where **each user registers their own Client ID** and connects it to our site permitted, given that a developer "may not use more than one Security Code for each SDA"? Is each user's app their own SDA, or is our site one SDA using many Client IDs? | [Developer Terms](https://developer.spotify.com/terms) v10 (15 May 2025), §VI 1.1–1.2; §II 9 (SDA definition) |
| S2 | Our AI model chooses songs from track metadata (title, artist, length) and from tempo and section data that a third-party app (MOWL) derives. Is sending that metadata to a model **at request time, without training**, "ingesting Spotify Content into a machine learning or AI model"? If yes, which data, if any, may be used? | [Developer Policy](https://developer.spotify.com/policy) (15 May 2025), §III item 14 |
| S3 | Instructors play the music in classes at gyms. Does the clause that Spotify "can't be broadcast or played publicly from a business" (and the commercial-use rule for SDAs) make classbeat impermissible, even though classbeat itself plays no audio and only builds playlists in the instructor's own account? | Developer Policy §III item 10; §IV item 2 |
| S4 | How long may we keep track IDs, titles and derived timing data for a class the user saved? Until the user deletes the class or disconnects? | Developer Policy §I 1.2; Developer Terms §V |
| S5 | Is there a route for a small non-commercial service to go beyond 5 users per app, given Extended Quota Mode needs an organization with 250k MAU? | [Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes); [Feb 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) |

## MOWL (Intelligent Training Group ApS)

No public terms, developer API or partner programme was found (mowl.com and
intelligent-cycling.com terms pages returned 404 on 2026-10-27; the app's store listing links
only a privacy policy). The owner may find the end-user terms in the app's settings.

| # | Question |
|---|---|
| M1 | May a third-party website use the MOWL API with each user's own sign-in to create and update that user's private workouts and attach playlists, for a small invite-only beta of instructors? |
| M2 | Is there a partner or developer programme, documented API, or rate limits we should respect? |
| M3 | Would you object to a later paid service doing this, and on what terms? |
| M4 | Where are the current end-user terms published? |

Swedebeat questions wait until its features are planned (they stay behind a disabled flag).
