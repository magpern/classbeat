# M4 live activation: song picking and the Spotify connection

[ADR 0008](../adr/0008-song-picking.md) §11 and [ADR 0009](../adr/0009-spotify-connection.md). This
runbook is the owner's checklist for switching song picking on against a real Spotify and MOWL account.
**Technical completion** (the roadmap's M4 "done when") is tested against the fakes only and needs none
of this. **Live activation** is separate.

## Rules

- **Agents run none of these steps without the owner's go-ahead for that step.** A go-ahead for step 3 is
  not one for step 4. Each step that is run is logged under "Live activation log" in
  [docs/status.md](../status.md) with the date, who authorized it, what it created and what it measured.
- Steps 1 and 2 are the owner's own actions in Spotify's dashboard and on the owner's stack. Steps 3 to
  7 write to or read from the owner's real accounts: a private Spotify playlist, a MOWL attach, a MOWL
  program. Nothing here uses another person's account.
- `CLASSBEAT_SONG_PICKING` is off in every default (`compose.yaml`, `.env.example`, CI). It is on only
  in the test-only e2e stack (`compose.e2e.yaml`) and in the owner's own local stack after step 2.
- Never paste a token, an authorization code, a Client Secret (classbeat never needs one) or a signed
  link into a log, an issue or this file. The measured facts below are shapes and numbers, not secrets.
- If anything fails, stop, switch `CLASSBEAT_SONG_PICKING` off again, and record what happened under
  the log. The playlists classbeat made stay in Spotify (the Disconnect dialog says so); delete them by
  hand in Spotify if they are not wanted.

## Before step 1

- `main` is green, the e2e job passes, and `docs/status.md` shows M4 technically done.
- The owner's local stack runs the fake model or the real one by choice; step 6 needs the real one
  and the owner's go-ahead for that model run ([status.md](../status.md), "Waiting for the owner").
- The `song_picking` flag (ADR 0011 section 7) is `granted` by default: besides `CLASSBEAT_SONG_PICKING=true`,
  the owner's account needs a plan with the `song_picking` feature, or the flag set to `on` in the admin area.
- The stack's public address is `http://127.0.0.1:<WEB_PORT>` (not `localhost`: Spotify does not
  accept `localhost` as a redirect URI, and classbeat refuses to start with song picking on and a
  `localhost` base URL).

## The steps

| # | Step | Who | Writes to a real account? | What it measures | Where it is logged |
|---|---|---|---|---|---|
| 1 | In the Spotify Developer Dashboard, create a Development Mode app; register the callback URL shown on `/account/spotify`; under User Management add the owner's own Spotify account (name and email). The app owner needs Spotify Premium. | Owner | No (a Spotify app) | The Development Mode allow-list size and the Premium rule, as the dashboard states them | status.md log, measured table |
| 2 | Set `CLASSBEAT_SONG_PICKING=true` in the owner's local stack. Connect Spotify on `/account/spotify` with the app's Client ID (this reads `/me`). Then give the working-playlist consent, which **creates one private playlist** in the owner's account. | Owner | Yes: one private playlist | `/me` works for an allow-listed account; that `POST /me/playlists` creates; the playlist is private and non-collaborative | status.md log |
| 3 | One pick: a few real searches, one pool write, the MOWL attach and analysis. | Owner, or an agent with the go-ahead | Yes: items written to the pool playlist | See the table below: does MOWL follow later edits of the playlist (`pool_not_synced`), the real shape of `/items` (`item` against `track`), its page size and the owner and privacy fields, the first `/me` 403 and the token endpoint's `invalid_grant` body for a user off the allow-list and for a revoked token, how soon a new playlist appears in `/me/playlists` (sets the listing lag), that contents are returned only for owned or collaborative playlists, that search `limit` is at most 10 | status.md log, measured table |
| 4 | One Approve: the final playlist, its read-back verification, the attach, the design. | Owner, or an agent with the go-ahead | Yes: a private final playlist and a MOWL attach | The read-back matches exactly the approved ids in the approved order on the real API shape | status.md log |
| 5 | One Change songs (a new final playlist, a revised design). | Owner, or an agent with the go-ahead | Yes: another private playlist | That a revision with changed music works against the real services | status.md log |
| 6 | The real cost per song-picked class: read it from the usage page after steps 3 to 5 and compare with the ceiling of ADR 0008 §3 (`CLASSBEAT_SONG_JOB_COST_MICROS`, default $3) and `PRICES_VERSION` (as ADR 0007 §10). | Owner | The model run is the owner's go-ahead | Cost per song-picked class | measured table |
| 7 | Disconnect Spotify, then check that the playlists classbeat made are still in Spotify as the dialog says and that classbeat sent no further Spotify request. | Owner | No new writes | That the dialog's promise holds | status.md log |

The first testers are invited only after steps 1 to 7 are done and logged.

## Measured facts

Fill the table from the live runs. A cell stays empty until the owner has run the step; ADR 0009's
Context takes several of these from secondary sources, and this is where they are confirmed or
corrected. If a fact differs from the assumption, open an issue; do not change a setting on a guess.

| Fact | Assumed (ADR) | Step | Measured | Date |
|---|---|---|---|---|
| MOWL follows edits of an attached playlist (the pool's later writes show up in MOWL's track list), and how long it takes | MOWL re-reads; the pick waits up to `CLASSBEAT_SPOTIFY_POOL_SYNC_WAIT_SECONDS` (120 s) then fails `pool_not_synced` (ADR 0008 §2) | 3 | | |
| `/items` shape: the field is `item` (new) or `track` (legacy) | `item`, both read (ADR 0009 §11) | 3 | | |
| `/items` page size and the owner and privacy fields | 50 per page; owner id and `public` present | 3 | | |
| Contents returned only for owned or collaborative playlists | yes (ADR 0009 Context) | 3 | | |
| `POST /me/playlists` creates the playlist | yes | 2 | | |
| Search `limit` | at most 10 | 3 | | |
| First `/me` 403 for a user off the allow-list: status and body | 403, category `not_allowlisted` | 3 | | |
| Token endpoint `invalid_grant` body for a revoked token | 400 `invalid_grant`, category `invalid_grant` | 3 | | |
| Delay before a new playlist appears in `/me/playlists` | unknown; sets `CLASSBEAT_SPOTIFY_LISTING_LAG_SECONDS` (unset until measured, at most 86 400) | 3 | | |
| Development Mode allow-list size and the app owner's Premium rule | 5 people; Premium for the owner | 1, 2 | | |
| Cost per song-picked class | below the $3 ceiling | 6 | | |

## After the steps

- Update the table above in the same PR that records the log entries in `docs/status.md`.
- Adjust only what the measurements justify, each through its own reviewed change: the pool sync wait,
  the listing lag, the price entries, the ceiling.
- Song picking stays off for everyone but the owner until the owner invites testers.
