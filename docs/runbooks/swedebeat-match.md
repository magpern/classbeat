# Swedebeat matching: the owner's live checks

[ADR 0016](../adr/0016-swedebeat-music.md) sections 5 to 7 and section 10, step 2. **Technical
completion** for path (b) is tested against the fakes only: the backend suites
(`test_swedebeat_match*.py`, `test_swedebeat_replace.py`, `test_swedebeat_verify.py`,
`test_swedebeat_match_done_when.py`, `test_swedebeat_match_job_isolation.py`) and Playwright
(`e2e/tests/swedebeat-match.spec.ts`). This runbook is for the live checks that only the owner can
authorize, because they use a real Swedebeat account, a real MOWL account and a real Spotify account.

## Rules

- Agents run none of these steps without the owner's go-ahead for that step. Log each one under
  "Live activation log" in [docs/status.md](../status.md) (the coordinator updates that file after
  the merge; an agent never edits it in a feature PR).
- Never paste a Swedebeat or MOWL token, a password, a temporary link or its signature into chat,
  an issue or a log. A result is a count, a verdict word or a song position, never a title list from
  the account.
- Matching **reads** the Swedebeat catalogue (titles and artists leave, nothing else). Nothing is
  written to Swedebeat by classbeat: the user builds the mix by hand in the Swedebeat app. The
  steps below write only what the app already writes: a private Spotify playlist (song picking),
  and a MOWL attach of the mix when it is analysed.
- Use a short list (8 songs or fewer) and your own accounts.

## Before the checks

- `main` is green and holds B1 to B5 (`#383`, `#384`, `#385`, `#386`, `#387`).
- Swedebeat connect works for you ([swedebeat-connect.md](swedebeat-connect.md): the release image
  carries the app key, `SWEDEBEAT_ENABLED=true`, the admin flag `swedebeat_enabled` allows it).
- Song picking works for you ([m4-live-activation.md](m4-live-activation.md)): MOWL and Spotify are
  connected, and you have a class designed on picked songs with its songs approved.
- For step 5 the media host is already in `MOWL_MEDIA_HOSTS`
  ([swedebeat-mix-media-host.md](swedebeat-mix-media-host.md)); without it a new mix ends
  `media_host_not_allowed`, which is the expected and safe answer, and the check stops there.

## The checklist

| # | Step | Writes to a real account? | What it measures | Pass when |
|---|---|---|---|---|
| 1 | Open the class, press **Match with Swedebeat**. | No (a read of the catalogue) | That the real catalogue answers as the library expects and the parser keeps title, artist and length | The job completes; the page shows found, unsure and not-in-Swedebeat counts that add up to the song count; no sentence about a wrong answer. A song you know is in the app shows **Found** with its length |
| 2 | Compare three songs by eye with the app: one found, one unsure (if any), one missing. | No | The 0.85 title threshold on real titles | An unsure song is one whose Swedebeat title is a different version or song; a found one is the same song. Note any song that is wrong either way (position only, not title) |
| 3 | If something is missing or unsure, press **Replace the missing and unsure songs**, approve the new list (a new private Spotify playlist is written, as in a song change), and read the new match. | Yes: one private Spotify playlist | That the status-only request works end to end and the re-match runs | The new list matches better; at most 3 rounds are offered |
| 4 | Press **Approve this selection**. | No | That approval is a pointer and nothing is written to Swedebeat | **Next, in the Swedebeat app** appears with the playlist name, the song count and the expected length |
| 5 | In the Swedebeat app, match that playlist and build the mix by hand, let it sync, then press **I made the mix** (**Check again** if it is not there yet). | Yes: a MOWL attach of the mix, the app's own write | That MOWL lists the new Swedebeat mix without classbeat's Swedebeat token, that `PlaylistID` appears in the listing, and the length verdict | A new mix is found, analysed, and the verdict is **ok** for a mix built from the full selection. Record the analysed length against the expected length (the page shows the expected one) so the "10 seconds per change" estimate can be corrected |
| 6 | Press **Check again** after deliberately leaving one song out of a second mix, or pick a mix that is shorter. | Yes: one more attach, only if the mix is new | The **too short** verdict and the positions it names | The verdict is too short and names the position(s) near the song you left out. **This is the right mix** still works |
| 7 | Press **Design the class on this mix**. | Yes: nothing new in MOWL until you save | That the class is timed on the Swedebeat mix, not on the Spotify playlist | The class is designed; its music source reads as the Swedebeat mix |
| 8 | Press **Disconnect MOWL**, then look at the class. | No | Retention | The hand-off is gone (the page offers the step again from the start), the match and the class stay, no Swedebeat call was made |

## What to record under "Live activation log"

Dates and these facts only: the song count, found / unsure / missing counts, the analysed length
and the expected length of the mix (seconds), the verdict, whether `PlaylistID` appeared in the MOWL
listing at once or after a wait (and how long), and any step that did not pass. No titles, no ids,
no tokens.

## If a step fails

| Sign | Meaning | What to do |
|---|---|---|
| Step 1 ends with the job page saying Swedebeat could not be reached | `upstream_unavailable` (5xx, a timeout or a refused connection) | Try once later; do not retry in a loop. Report the time |
| Step 1 (or a later step) asks to reconnect Swedebeat | Swedebeat answered 401 or 403 | Reconnect on the account page; if it repeats at once, report it (the session may be held by the phone app) |
| Step 1 says Swedebeat asks to slow down, with a time | Swedebeat's 429 with its `Retry-After`; three in a row stop the match | Wait the time shown. Do not press again before it |
| Step 1 completes but every song is **Not in Swedebeat** though the app has them | The answer's shape differs from the library's (`parse_hits` keeps nothing) | Stop and report: an agent compares the shape offline. Never paste the answer |
| Step 5 never finds the mix | MOWL's listing may not show it yet, or lacks `PlaylistID` | Check again after a few minutes; then report |
| Step 5 ends **media_host_not_allowed** | `MOWL_MEDIA_HOSTS` is still empty | Do [swedebeat-mix-media-host.md](swedebeat-mix-media-host.md) first |

## After the checks

Report the facts above to the Architect. Any change they need (a corrected change allowance in the
expected length, a different match threshold, the listing field) is a normal reviewed PR; none of it
is a live change, and an agent switches nothing on (ADR 0016 section 10, step 3).
