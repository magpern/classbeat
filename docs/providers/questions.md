# Questions for the providers (track F)

**Not sent.** The owner decided on 2026-10-28 not to involve the providers while classbeat is
a personal app for the owner and invited friends ([ADR 0010](../adr/0010-live-activation-permissions.md)).
The questions are kept for a later public or paid phase. Agents never contact providers
(AGENTS.md, safety envelope).
Sources were read on 2026-10-27; the owner should confirm the quoted wording on the live pages
before sending, because it was retrieved through an automated reader.

## Spotify (developer support)

Context to include: classbeat is an invite-only, free, non-commercial beta for a handful of
indoor-cycling instructors. Each instructor creates their own Spotify developer app and
connects it to classbeat (Authorization Code with PKCE, no client secret). classbeat creates
private playlists in the instructor's own account.

| # | Question | Term it concerns |
|---|---|---|
| S1 | Is a hosted service where **each user registers their own Client ID** and connects it to our site permitted, given "You must use a separate Security Code for each SDA" and "You may not use more than one Security Code for each SDA"? Is each user's app their own SDA, or is our site one SDA using many Client IDs? | [Developer Terms](https://developer.spotify.com/terms) v10 (15 May 2025), §VI 1(b); §II 9 (SDA definition) |
| S2 | A third-party hosted AI model (Anthropic's Claude, through its API) would choose and order songs. Per request it would receive track IDs, titles, artists, lengths, the contents of a candidate playlist, and tempo and section data that a third-party app (MOWL) derives from those tracks. No training. Is that "ingesting Spotify Content into a machine learning or AI model"? If yes, may any of this data be used, and how? | [Developer Policy](https://developer.spotify.com/policy) (15 May 2025), §III item 14 |
| S3 | classbeat's users are indoor-cycling instructors who may play their playlists in classes at gyms; classbeat plays no audio and only builds playlists in the instructor's own account. Does §III item 10 ("Do not build products or services which are targeted for use by businesses"; Spotify "can't be broadcast or played publicly from a business") or the commercial-use rule for SDAs apply to classbeat? | Developer Policy §III item 10; §IV item 2 |
| S4 | How long may we keep track IDs, titles and derived timing data for a class the user saved? Until the user deletes the class or disconnects, or a shorter period? | Developer Policy §IV 3.1(a) ("Do not store Spotify Content indefinitely"), §IV 3.2; Developer Terms §V 7 and Appendix A (deletion periods) |
| S5 | Is there a route for a small non-commercial service to go beyond 5 users per app, given Extended Quota Mode needs an organization with 250k MAU? | [Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes); [Feb 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) |

## MOWL (Intelligent Training Group ApS)

No public terms, developer API or partner programme was found (mowl.com and
intelligent-cycling.com terms pages returned 404 on 2026-10-27; the app's store listing links
only a privacy policy). The owner may find the end-user terms in the app's settings.

| # | Question |
|---|---|
| M1 | May a third-party website use the MOWL app's private API (learned by observing the app; it isn't documented) with each user's own sign-in to create and update that user's private workouts and attach playlists, for a small invite-only beta of instructors? |
| M2 | Is there a partner or developer programme, documented API, or rate limits we should respect? |
| M3 | Would you object to a later paid service doing this, and on what terms? |
| M4 | Where are the current end-user terms published? |

Swedebeat questions wait until its features are planned (they stay behind a disabled flag).
