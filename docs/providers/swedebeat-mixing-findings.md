# Swedebeat mixing: findings for #580

Date: 2026-11-13. Feeds the question list for Swedebeat in issue #580 (use part of a song). This is
research, not an implementation.

**Scope.** I used the owner's own signed-in account in the Swedebeat Android app (Swedebeat G2,
version 2.1 build 86, Samsung Galaxy SM-S936B, over ADB) and read Swedebeat's public pages and the
mowl-workouts repository. For network evidence I used only what the app writes to the phone's own
system log (Android logcat): its HTTP client and its AWS SDK log every request URL. I ran no proxy,
installed no certificate, did not decompile or patch the app, and did not download, save or analyse
any audio. Everything below is redacted: tokens, signatures, keys, account ids and emails are
replaced by placeholders.

**What I changed in the account** (all on one throwaway playlist; the owner's playlists and mixes
are untouched):

1. Duplicated the owner's playlist "Test McP" into **"ZZ Claude test - throwaway"**.
2. On the copy: trimmed track 1 (start 0:04.04), raised its tempo +3 %, set a 3 s crossfade between
   tracks 1 and 2. A mistap in the player also set track 1's live tempo to +1 %.
3. Opened **Share** on the copy. The app immediately uploaded the playlist as a zip, before any
   confirmation. I closed the dialog without pressing its final Share button; "Shared by me" stays
   empty.
4. Pressed **Merge & Publish** on the copy (with the owner's approval). It rendered and published
   one MP3 mix. The copy now shows "Published".

The copy and its published mix can be deleted by the owner in the app; I have not deleted anything.

Marking: **Observed** facts come from the screen or the log. Anything marked *(inferred)* is my
reading of those facts and was not confirmed.

## 1. Features

The app is free; the music comes from purchases or a subscription (§3). On the owner's account
(a gym-chain account) all features below were available. The app's own UI names no plan per feature.

| Feature | What the app lets a user do | Screenshot |
|---|---|---|
| My playlists | Purchased or downloaded mixes and the user's own playlists, each with BPM range, total time and track count | [01](swedebeat-mixing/01-my-playlists.png) |
| Purchased mix | Only **Duplicate** and **Favorites**: a purchased mix cannot be edited in place; a copy can | [02](swedebeat-mixing/02-purchased-mix-menu.png) |
| User playlist menu | **Add Downloaded Tracks**, **Duplicate**, **Rename**, **Share**, **Merge & Publish**, **Set BPM** | [03](swedebeat-mixing/03-user-playlist-menu.png) |
| Playlist editor | Track list with per-track BPM and length, a scissors (trim) button per track, a copy-track button, and a crossfade marker between every pair of tracks | [04](swedebeat-mixing/04-user-playlist-editor.png) |
| Choose tracks | From **My music** (downloaded single tracks, each with its own BPM and length), and **Add from Swedebeat**: the catalogue track by track, each with a download and an add-to-playlist button | [13](swedebeat-mixing/13-my-music-tracks.png), [14](swedebeat-mixing/14-add-single-tracks.png) |
| Order | Tracks are a list in order *(inferred: re-ordered by drag; I did not try it)* | [04](swedebeat-mixing/04-user-playlist-editor.png) |
| Part of a track ("Putsa låt", trim song) | Waveform, play, **Mark start** and **Mark end** at 1/100 s; Save or Discard. The saved trim shortens the track's shown length (6:16 → 6:12) | [05](swedebeat-mixing/05-trim-start-end.png) |
| Tempo per track | In the trim screen: a tempo slider in % with the resulting BPM (150 → 155 BPM at +3 %), **Reset BPM**, and a preview speed 1.0x–1.5x. The list then shows "150 - 155 BPM" | [06](swedebeat-mixing/06-trim-tempo.png) |
| Tempo for a whole playlist | **Set BPM**: one target BPM for the playlist, or **AUTO** | [09](swedebeat-mixing/09-set-playlist-bpm.png) |
| Live tempo in the player | Now playing shows "TEMPO +n %" and the resulting BPM; play mode "Kontinuerlig" (continuous) | [17](swedebeat-mixing/17-now-playing-tempo.png) |
| Crossfade per transition | Off by default. On: **length 1–30 s** in whole seconds (12 s preset), a fade-out curve for the outgoing track and a fade-in curve for the incoming one, each with a level ("0% db") and a time, edited under **Editera**. Total playlist time drops by the crossfade (33:41 → 33:38 for 3 s) | [07](swedebeat-mixing/07-crossfade-edit.png), [08](swedebeat-mixing/08-crossfade-length.png) |
| Share | Upload, then pick categories (activities, music style) and Share to other users. A catalogue "Delad/Mottagen mix" lists other users' shared playlists (Popular, Recently added, Shared by me) with a download button | [10](swedebeat-mixing/10-share-categories.png), [11](swedebeat-mixing/11-shared-catalogue.png) (names blurred) |
| Store mixes | "Swedebeat mix" catalogue of ready mixes to download | [12](swedebeat-mixing/12-store-mixes.png) |
| **Merge & Publish** | Renders the playlist, with its trims, tempos and crossfades, into **one MP3** and publishes it to the account ("It will take a few minutes", three steps, then "Published") | [15](swedebeat-mixing/15-merge-progress.png), [16](swedebeat-mixing/16-merge-published.png) |
| Match Spotify link | Paste a Spotify playlist URL; the app says the first 50 songs are matched against its music library. Not submitted | [18](swedebeat-mixing/18-add-playlist-sheet.png), [19](swedebeat-mixing/19-match-spotify-link.png) |
| Intervals | "My intervals" and "Shared intervals" (empty on this account). Not explored further | — |
| Settings | Backup, interval settings, support, audio (**Normalize sound** only), log out | — |

Plans (Swedebeat's Unlimited page, prices in SEK per month): **Unlimited Infinity** 249 (all MOVE
YA! mixes, "over 800+") and **My! Unlimited Free** 169 (the royalty-free subset, "over 235+"). Both
list the same app features: download in the app, pitch control, individual playlists, **Mix Tool**,
upload of your own songs. The app page says the app is free with no in-app purchases. Which plan the
owner's gym account has, and whether a smaller plan hides the Mix Tool, I could not see.

## 2. How it works

**Tracks are separate files.** My music lists single tracks, the catalogue has a download button
per track, and the merge reads one local MP3 per track (`app_merge_files/0.mp3` … `6.mp3`, MP3
44.1 kHz stereo, 320 kb/s). A playlist is a list of references to those files plus per-track and
per-transition settings.

**Playback mixes in the app.** The player ("CrossPlayer" in the log) plays the separate files and
applies tempo and crossfades live *(inferred from the log lines "no mixpoint" and the live tempo
control)*.

**Merge & Publish renders on the phone, not on the server.** Observed sequence:

1. **Render on the device** with ffmpeg (ffmpeg-kit, ffmpeg 6.0). Filter graph from the log:
   track 1 → `atrim` (the trim), every other track → `atempo` (tempo, also when unchanged), then
   `acrossfade`, encoded with `libmp3lame`. Output: one MP3, 320 kb/s, **33:20.87**, about 78 MB.
   Speed about 79× real time.
2. **Upload** the MP3 straight to Swedebeat's AWS S3 in a multipart upload (17 parts), signed
   by the app itself:
   `POST|PUT https://users-playlists.s3.eu-west-1.amazonaws.com/<email-without-@>/<Playlist_Name>-<n>.mp3`
3. **Register** the mix with Swedebeat:
   `POST https://www.swedebeat.se/downloader/mergemp3api.php?<redacted query>`, 1,226-byte body,
   `200 OK`. Body shapes were not logged.
4. The app's background workers end with `MergeAndPublishWorker` SUCCESS. The playlist shows
   "Published".

The parameters a mix carries, as the app exposes them:

| Parameter | Where | Unit / range seen |
|---|---|---|
| Track | per track | a Swedebeat catalogue track (own uploads too, per the plan page) |
| Order | playlist | list position |
| Start, end | per track ("Putsa låt") | m:ss.hh |
| Tempo | per track | % change, shown with the resulting BPM; whole-playlist target BPM via Set BPM |
| Crossfade | per transition | on/off, length 1–30 s, fade-out and fade-in curve (level, time) |
| Normalize | app setting | on/off |

*(Inferred)* The filter graph applies all of these at render time, so a server that ran the same
render could take the same list as input. Whether Swedebeat has such a server, I did not see.

**Other endpoints seen** (paths only, all query strings redacted):

| Call | When |
|---|---|
| `POST https://www.swedebeat.se/downloader/app-backup-v2/backup.php?…` (about 24 KB body) | After each playlist edit (background "backup" worker). *(Inferred: the playlist definitions, including trims and crossfades, are backed up here.)* |
| `POST https://shareplaylists.s3.eu-west-1.amazonaws.com/<account-id>/<timestamp>-<n>.zip` (multipart) | Opening **Share**: the whole playlist is uploaded as a zip before the user confirms. *(Inferred: the zip holds the track audio: 15 upload parts, against 17 for the 78 MB merged MP3.)* |
| `GET https://swebeat.s3.eu-west-1.amazonaws.com/…` | Cover images and catalogue files |
| `POST https://www.swedebeat.se/downloader/trackplayer.php` | When playback starts *(inferred: play reporting)* |
| `POST https://www.swedebeat.se/downloader/trackappaccess.php` | App start *(inferred: access check)* |
| `GET https://cloud.swedebeat.se/track/extendedsearch` | Catalogue search (already used by mowl-workouts) |

**The link to MOWL** (from mowl-workouts `docs/mowl-api.md` and `api.py`, not re-tested): MOWL lists
"the user's own Swedebeat mixes" with `POST v1/swedebeat/Playlists` → `{ID, Name, URL,
DurationInMilliseconds}`, wraps one in a MOWL playlist with `PUT v1/swedebeat/Playlist`, and
serves the audio through `GET v1/swedebeat/playlist/{id}/TemporaryLinks` (a signed link to the
MP3). *(Inferred)* Those "own mixes" are the Merge & Publish results in `users-playlists`; that
would also explain mowl-workouts' note that the listed length can be wrong: the app shows 33:38 for
the test playlist, while the rendered file is 33:20.87, because the app's total ignores the tempo
change. I could not check whether MOWL now lists "ZZ Claude test - throwaway" (§6).

**Side observation for Swedebeat.** The app writes its AWS SDK request-signing debug output and
every request URL to the Android system log, which any app-debugging tool on the phone can read.
The S3 key of a published mix contains the user's email address. I recorded neither.

## 3. Terms

From Swedebeat's public pages (short quotes, retrieved 2026-11-13):

- **Commercial use** (Terms, §8a): "You have the right to use Swedebeat products for commercial
  use."
- **Modification and derivative works** (Terms, §8c): the customer agrees not to "modify, lend,
  rent, lease, sell, distribute or create derivative works" of the service. The terms say nothing
  about the app's own trim, tempo and crossfade tools.
- **Reproduction** (Terms, §8c): no part of the service may be reproduced "except as expressly
  permitted in these terms."
- **Credentials** (Terms, §3.2): only the customer may use the customer's credentials.
- **Partners**: the terms mean webshop sellers by "partners". There are **no API, developer or
  partner-access terms** on any page I found.
- **Public performance**: the Terms point to collecting societies (STIM, PRS, SACEM and others)
  for rights and limits. The Unlimited page says for the royalty-free plan: "No public performance
  fee has to be paid." For Unlimited Infinity it says nothing.
- **Licence page**: "All products marketed on our website are intended for professional use within
  the fitness industry." It invites licensing questions by contact, and says Swedebeat is "happy
  to assist with licensing of external productions."
- **App page**: the app markets the cutting itself: users can make their own mixes "with the
  included trim and cut features", control crossfades "like a DJ", and change BPM keeping pitch.

Not covered anywhere: a third party building or editing a mix for the user, an API, a partner
programme, server-side rendering, or which plan carries which editing right.

## 4. Fit for classbeat

What exists today:

- Swedebeat already has every parameter #580 needs: track choice, order, start and end per track,
  tempo, and crossfade length per transition. A Merge & Publish result is one MP3 in the user's
  account, which is what MOWL attaches today *(inferred, §2)*.
- The render happens **in the app on the user's phone**. There is no observed server endpoint that
  takes a mix description and returns audio. The only server step after rendering is a registration
  call (`mergemp3api.php`) for a file the app has already uploaded.
- The app holds its own storage credentials and signs uploads itself. classbeat must never use
  those (AGENTS.md, ADR 0016), so classbeat cannot imitate the upload or the registration.

So, **with Swedebeat's agreement**, the realistic routes are, in order of fit:

1. **Server-side render API.** classbeat sends `{tracks: [{track_id, start, end, tempo_pct}],
   transitions: [{crossfade_s, fade curves}], name}` for the user (with the user's Swedebeat
   authorization), Swedebeat renders it with the same graph as the app and publishes it to the
   user's mixes, and MOWL attaches it as it does now. Does not exist today.
2. **Draft playlist API.** classbeat creates an unrendered playlist (tracks, cuts, tempo,
   crossfades) in the user's account; the instructor opens it in the app and presses Merge &
   Publish. The rendering stays in Swedebeat's app. Does not exist today; the backup endpoint
   suggests playlists are already stored server-side *(inferred)*.
3. **Manual hand-off** (what ADR 0016 does today, extended): classbeat shows the instructor the
   track list with cut points, tempos and crossfade lengths, and the instructor builds it in the
   app. Needs no agreement, but costs the instructor real time per class.

Either API route also needs Swedebeat's licensing answer on a third party designing the cuts, and
both fit MOWL only if MOWL keeps listing published mixes as it does today.

## 5. Questions for Swedebeat

1. Is there, or could there be, a partner API for a third-party app (classbeat) acting for a
   signed-in Swedebeat user? Which authorization would you want (OAuth or similar), and which
   terms?
2. Could a partner submit a mix description (track ids, order, start and end per track, tempo per
   track or target BPM, crossfade length and curve per transition, name) and have Swedebeat render
   and publish it to the user's mixes, as Merge & Publish does in the app?
3. If not rendering: could a partner create an unrendered playlist (draft) in the user's account
   that the user then opens, checks and publishes with Merge & Publish?
4. Is a mix published with Merge & Publish exactly what MOWL / Intelligent Cycling lists as the
   user's Swedebeat mixes (`v1/swedebeat/Playlists`)? Is there anything else MOWL needs?
5. Licensing: may cut, tempo-changed and crossfaded versions of your tracks, designed by a partner
   on the user's behalf, be played in classes under the user's or the gym's licence? Does it differ
   between Unlimited Infinity (public performance fees) and My! Unlimited Free (royalty-free)?
6. Which plans include the Mix Tool and Merge & Publish? Do gym (chain) accounts differ from
   personal ones?
7. Is there a stable catalogue id per track that a partner may store, and a catalogue search a
   partner may call (today the app uses `cloud.swedebeat.se/track/extendedsearch`)? Can a partner
   get track length and BPM per track?
8. Are there limits on a mix: number of tracks, total length, crossfade range (the app offers
   1–30 s), tempo range?
9. Could the Spotify-link matching in the app (first 50 songs) be offered to a partner, so a
   classbeat song list can be matched to your catalogue with your consent?
10. Do you have a partner or developer contact, and terms we should read before we build anything?

## 6. Unknowns

- **Request and response bodies.** The log gives methods, URLs and body sizes only, not bodies, for
  `mergemp3api.php`, `backup.php` and the share upload. Reading them would need a proxy with a
  trusted certificate on the phone or app changes, both out of scope. So the exact parameter names
  Swedebeat stores (for trims, tempo, crossfades) are unknown.
- **Server-side rendering.** Not seen. The only render observed runs in the app. Whether Swedebeat
  has a server renderer is unknown.
- **MOWL listing of the new mix.** The mowl MCP server failed to connect this session, and this
  machine has no mowl CLI or MOWL sign-in, so I could not check that "ZZ Claude test - throwaway"
  appears in MOWL's Swedebeat mixes. The owner can check in the MOWL app.
- **How every transition is joined.** The filter graph names `atrim`, `atempo` and one
  `acrossfade`. How transitions with crossfade off are joined (plain concatenation or a zero-length
  fade) is not visible.
- **Rendered length vs shown length.** The render is 17.1 s shorter than the app's total. The +3 %
  tempo on track 1 explains about 11 s *(inferred)*; the remaining ~6 s is unexplained.
- **Order editing.** I did not try re-ordering tracks.
- **What the Share zip contains.** Not inspected. It stays uploaded, unpublished, under the account.
- **Plans.** Which plan the owner's gym account has, and whether smaller plans hide the Mix Tool.
- **The website.** swedebeat.se is a webshop and help site. I found no mix editor there, so there
  was no browser traffic to observe *(not confirmed by signing in on the web)*.
- **The manual** (manual.swedebeatmediagroup.com) explains create, edit, crossfade and share only
  in videos, which I did not watch.

## Sources

- Swedebeat: [fitness music app](https://www.swedebeat.se/en/fitness-music-app),
  [Unlimited Music](https://www.swedebeat.se/en/unlimited-music.html),
  [Terms and Conditions](https://www.swedebeat.se/en/terms-and-conditions/),
  [License and copyright](https://www.swedebeat.se/en/music-licence/),
  [App manual](https://manual.swedebeatmediagroup.com/).
- mowl-workouts: `docs/mowl-api.md` (Swedebeat calls), `docs/music-sync.md`, `src/mowl/api.py`,
  `src/mowl/swedebeat.py`.
- classbeat: ADR 0016 (Swedebeat music), ADR 0021 (use part of a song), issue #580.
