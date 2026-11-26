# MOWL playback capture: how MOWL plays part of a track (classbeat#580)

Captured by the account owner on their own Mac (native MOWL app, own MOWL and Spotify accounts),
read only, through a redacting mitmproxy (`mitmdump`, only `*.mowl.com` and `api.spotify.com`
decrypted). During the capture the investigator made no calls to MOWL or Spotify. Afterwards, at the
owner's request, one read of the played workout's metadata was made with the `mowl` tool (no writes).
Redacted: Authorization, Cookie and Set-Cookie headers, any field named like token, secret, password,
session or key (this also redacts a musical `key` field, if one exists), signed-URL parameters,
emails, user and device ids. Ids appear only as labels (`track#1`). The raw capture was never written to disk; only a
redacted copy was kept in a temporary folder, which was deleted after this report was written
(together with the proxy's CA key). The system proxy was switched off and the mitmproxy certificate
removed from the System keychain.

## Answers

| | Question | Answer |
|---|---|---|
| (a) | Start seek: how does MOWL give a mid-track position? | **Yes, but not to Spotify directly.** MOWL calls its own API, `POST api.mowl.com/v1/Spotify/Player/SeekToPosition`, with a position on the **playlist's whole timeline** in milliseconds. MOWL's side turns that into track plus offset. No `api.spotify.com` call was seen from this Mac. |
| (b) | Does the downloaded workout data hold a start, offset or end per track or section? | **No, in everything seen.** The workout's metadata (read afterwards with the `mowl` tool's `get_workout`, see below) holds a start time, length, zone, position and cadence per interval and per segment, and for music only a reference to the playlist. The playlist's track list holds a `Duration` per track. Neither holds a start, offset or end for a track. Caveat: that tool shows MOWL's workout data in its own shape; the proxy never saw the raw workout download (see "What was not seen"). |
| (c) | Does a section that ends before its track ends skip early? | **No (one boundary tested).** At a block change mid-song no call at all was sent, and the same song kept playing (owner's observation). |

## Evidence

### Call sequence when a workout starts (redacted)

1. `GET /v1/Users/Me/Connections/`, `GET /v1/Playlists/<playlist>/tracks`,
   `GET /v1/Spotify/Player/Devices`, `POST /v1/Spotify/Player/SelectedDevice` (body: device id, masked).
2. `GET /v1/Playlists/<playlist>/availability` (all tracks available).
3. `POST /v1/Player/Play` with `ProgramID`, `PlayerType: ThePlayer`, `PlayMedia`, `IsPublic: false`,
   `GenerateConnectKey`, `HostMQTTFlowVersion: 1`, ... The answer holds a `WorkoutPlay` record
   (play id, zone set `IntensityZoneSet: 1`, `IntensityUnit: ftp`, `PlayMedia`, ...). It holds no music position.
4. `POST /v1/Spotify/Player/SeekToPosition`, body
   `{"FromPositionInMilliSeconds":"0","PositionInMilliSeconds":"0","ITCPlayListID":"<playlist id>","CrossFadeInMilliSeconds":"9540"}`
   (all values are strings).
5. `GET /v1/Spotify/Player/Playback`, answer `Data.ProgressMS` plus `Data.item` (`Name`, `Uri`, `DurationMs`,
   `Artist`, `IsPlayable`) plus `Data.device`. Also seen: `GET /v1/Spotify/Player/Pause` (answer `Data: false`).

Every answer carries a MOWL server envelope (`EMN`, `ASV`, `FromCache`, `MS`), so these calls are
answered by MOWL's servers, not by the Spotify API directly.

### (a) The position is on the playlist timeline

The playlist had three tracks of 184413, 189374 and 241133 ms. Reading the position as time on one
timeline where each track is shortened by the 9540 ms crossfade, track starts fall at 0, 174873 and
354707 ms. Each seek then matched the next `Playback` reading (about 0.6 s later in these rows):

| Seek position (ms) | Expected: track, offset | Observed next `ProgressMS`, track |
|---|---|---|
| 83000 | track#1, 83000 | 83603, track#1 |
| 134001 | track#1, 134001 | 134619, track#1 |
| 178001 | track#2, 3128 | 3744, track#2 |
| 213001 | track#2, 38128 | 38799, track#2 |
| 265001 | track#2, 90128 | 90701, track#2 |
| 318394 | track#2, 143521 | 144110, track#2 |
| 361001 | track#3, 6294 | 6900, track#3 |

So a section that starts mid-track is reached by one `SeekToPosition` to a timeline position;
the track and offset are worked out on MOWL's side. The seek body carries no track id and no offset
inside a track.

The seven first-run seek positions are 83000, 134001, 178001, 213001, 265001, 318394 and 361001.
Five are whole seconds plus 1 ms, 83000 is a whole second, and 318394 is
neither: it is not an interval start (the nearest is 311001). Its observed progress (144110) fits a
seek to 318394 (143521 expected, 589 ms lag, like the other rows) and does not fit 311001 (136128
expected). What caused that one seek cannot be checked now that the capture no longer exists, so no
cause is claimed. The values 178001, 213001 and 265001 came back in two separate runs, while the
track boundaries are 174873 and 354707. Apart from 318394, the seeks are therefore the workout's own
interval boundaries on the shared timeline, not track boundaries. Each forward-button press sent
exactly one `SeekToPosition` to the next boundary, with no skip or next-track call. A later test of forward and backward presses (4 to
5 s apart; I count 4 forward and 5 backward seeks where the owner said 3 and 4, so the owner's count
was probably off by one) gave the boundary sequence 361001, 406001, 435001, 455001 going forward and
435001, 406001, 361001, 311001, 265001 going back. The first forward target was the boundary after
the position the song had reached on its own (about 352 s). Going back across a track edge
(311001 is 136 s into track#2, 265001 also track#2, 361001 is 6.3 s into track#3) used the same
`SeekToPosition`, with no "previous track" call. The only calls in that test were
`SeekToPosition` and one `Playback` poll after each.

### (c) No early skip at a section boundary

After the start seek at 50 s on the capture clock and its one `Playback` poll, no call to
`api.mowl.com` went out until the owner's next action 173 s later (the proxy's unfiltered log,
which had no call to `api.spotify.com` either). A new interval block started in that window
(owner's marker at 138 s, about 88 s into the song). The owner confirmed the same song kept playing
through the block change. The screen showed about 19 minutes left in the segment about 2 minutes into a
25-minute, two-segment workout, so the boundary tested was **an interval boundary inside the first
segment**, not the boundary between the two segments.
A song that ended by itself during an untouched stretch was not isolated: the first song's handover
fell within seconds of the owner's first forward press, so the natural handover was **not**
observed separately.

### (b) What the downloaded data holds

- Playlist tracks (`GET /v1/Playlists/<id>/tracks`): per track `SpotifyTrackID`, `SpotifyUri`, `Artist`,
  `Title`, `BPM`, `Duration` (ms), `TrackID`, `WaveformSVGUri` (a MOWL media URL), `DateCreated`,
  `IndexStatus`, `IndexError`, `FileSize`. **No start, offset, position or end field.**
- Program list (`POST /v1/programs`): per program `SegmentCount` (2), `TotalDuration` (1500 s),
  `Segments: null`, `PlaylistID`, `PlaylistType: Spotify`, `SpotifyUrl`, and a `Playlist` object
  (name, `TrackCount`, `PlaylistDuration: 0`, `Tracks: null`). No per-segment or per-track music field.
- **Workout metadata** (the played workout, a public MOWL "Standard Camps" workout of 25:00 with two
  segments; read once afterwards with `get_workout` at the owner's request, a read only). Per segment:
  `name`, `kind` (main or cooldown), `start`, `sec` (segment one: 1262 s from 0:00; the cooldown 238 s from
  21:02). Per interval: `start` (m:ss), `sec`, `zone`, `position` (Seated or Standing), `cycle` (SB or ACC),
  `rpm` range. For music only: `type: Spotify`, the playlist `name` and `playlist_id`. There is no
  per-track or per-segment music field (no start, offset, position, end or duration for a song).
- **The seek positions are the interval start times.** The interval starts are 0:00, 1:23, 2:14, 2:58,
  3:33, 4:25, 5:11, 6:01, 6:46, 7:15, 7:35 (83, 134, 178, 213, 265, 311, 361, 406, 435, 455 s).
  All ten distinct targets seen in the forward/back presses and the first run (83000, 134001, 178001,
  213001, 265001, 311001, 361001, 406001, 435001, 455001 ms) equal an interval start in seconds (the
  1 ms is added by the app, except for 83000). The one exception is the first-run seek to 318394
  (see (a)), which is not an interval start and is not among the ten. So MOWL's seek
  position is simply the workout clock at the start of the interval jumped to. That is also the
  playlist timeline, because the music runs continuously from workout time 0 with each song's overlap
  taken off.

## What was not seen (limits)

- **The raw workout download.** The workout's intervals and segments were not downloaded over HTTP in
  either run, even after a full restart of the app. They may come from a local cache, from an earlier
  download, or over a channel the proxy cannot see (the `Play` request names an MQTT flow version, and a
  UDP listener on port 6454 was open; no other TCP connection was seen). The metadata above comes from
  the `mowl` tool, which shows it in its own shape, so a raw field it drops would not show. A fresh
  capture with a cleared local MOWL cache would close that, which is a change to the owner's app
  data and was not done.
- **The hop to Spotify.** The Spotify desktop app on this Mac was the playback device. If MOWL's
  servers command it with the Spotify Web API, that hop cannot be seen from here. Nothing here shows
  whether MOWL ever sends a position to Spotify directly.
- **No certificate pinning** was met for `api.mowl.com` and `media.mowl.com` (no TLS failures).
- **First run had no markers.** The first run's actions were matched only by content and order
  (the seeks above). The second run used time markers.
- The workout used was a two-block Spotify workout with three tracks in the played part; one
  mid-track block boundary was tested for (c); the natural handover of a song was not isolated.
- The segment boundary (between the two segments) was not tested, only an interval boundary inside segment 1.
- Redaction gaps found while reading (all kept out of this report): a creator display name in the
  `Play` answer, the machine's name in the devices list, the Spotify track id in `Playback` →
  `Data.item`, and the Spotify playlist id inside `open.spotify.com` URLs. MOWL's own numeric ids
  (program, track, playlist) were kept as plain values on purpose (they are not user ids).

## What this means for #580 (inference)

MOWL has a per-track `Duration` and one workout clock that is also the playlist timeline. An
interval's mid-track start is simply where the clock falls inside a song, and a jump is one
`SeekToPosition` to the interval's start time. A section end triggers nothing, and neither the workout
nor the track list holds a start or end for a song. Nothing seen shows a way to play a trimmed track:
a song always plays from its start and to its end, and a "start mid-track" only comes from where the
workout clock lands. (Inference. The open point is the raw workout download, above.)
