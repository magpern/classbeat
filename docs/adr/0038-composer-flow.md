# ADR 0038: The composer flow: describe, shape it together, approve and save

Status: Accepted (owner decision and mockup approval, 2026-11-17; roadmap Amendment 29; issue #1184).
Implementation: #1185, #1186, #1188, #1189, #1190, #1191. Behind the `composer_flow` flag until the owner's
live check.

Amends:
- [ADR 0008](0008-song-picking.md) §4 (phase 1 ends with the song list; phase 2 designs) for composer
  classes: phase 1 now also designs the class, the approval covers the list and the draft, and phase 2 fits
  instead of designing (§5, §7 below). §7 (waiting, expiry, cancel) is unchanged.
- [ADR 0029](0029-ai-first-class-design.md) §5: the hand-off can start a sketch (§4), and two more web-side
  hand-offs follow the same pattern (§5, §7).

Keeps: [ADR 0007](0007-classes-revisions-and-saving.md) saving, ADR 0008's approval before every external
write, ADR 0029 §4 (explicit instructions vs assumptions), roadmap Amendments 25 to 28.

## Context

The owner found the current flow bulky (2026-11-17). Today an instructor:

1. fills in a start screen with a text box, starter chips, service chips, a sport choice, a long playlist list,
   a destination sentence and an ideas box;
2. waits for the reading;
3. approves a song list without having seen the class it will be in;
4. waits for the final playlist, MOWL's analysis and the design;
5. saves to MOWL in a separate step, on a class page with about a dozen panels.

That is three decisions and two waits, and the song approval comes before the class exists.

The owner described the flow they want (issue #1184) and approved a three-screen mockup:

1. **Describe.** One text box, with music attached as tags and a "Surprise me" button.
2. **Shape it together.** A chat beside a rough sketch. classbeat says how it read the request, asks
   follow-up questions, and the class is refined in conversation. Songs can be named and placed ("this song
   is the last climb"). Attached playlists are favoured, but are not a limit.
3. **Approve and save.** The class with its songs in place, and one approval.

## Decision

### 1. The flow

| Step | What the user sees | Job | Credit |
|---|---|---|---|
| Describe | The composer (§10.1) | `interpret_request` (§3) | none |
| Sketch | The reading in the chat, the sketch on the right | `design_draft`, mode `sketch` (§4) | the class credit |
| Shape | Chat changes to the sketch | `revise_draft` on a sketch (§4) | none |
| Songs | "Finding songs for this shape…", then the class with songs | `design_with_songs`, mode `fill` (§5) | none |
| Refine | Swap a song, or a chat change | `design_with_songs`, modes `swap` and `change` (§6) | none |
| Approve and save | One button | phase 2 with `fit` (§7), then `save_workout` | none |

The songs start by themselves when the sketch is ready. The user can keep shaping meanwhile (§8).

### 2. The flag

- `composer_flow` follows the `running_enabled` pattern (ADR 0018 §5): an environment switch
  `CLASSBEAT_COMPOSER_FLOW` (off by default) and a stored flag. Only a stored `on` switches it on, and only
  under the switch. It is exposed to the web with the other per-user flags.
- With it off, every job and screen behaves as before.
- The old start screen and class page stay until a later cleanup issue, after the owner's live check.

### 3. The reading (`interpret_request`)

- **Input `music_sources`**: at most 5 entries of `{kind: "spotify_playlist" | "swedebeat_mix", id}`.
  - Each id must be one of the user's own (the playlists and mixes the start screen lists); any other id is
    `invalid_input`.
  - "No music" stays `music_override {source: "none"}`, and cannot be combined with `music_sources`.
  - The music order (ADR 0029 §4, rule 4) reads `music_sources` as "favour these", never as "use only these".
- **Pinned songs**: at most 10 items `{title, artist, at, quote}`, origin `explicit` only.
  - `at` is a position hint from the request ("warm-up", "climb 3", "last climb"): cleaned text, at most 40
    characters.
  - Pinned songs are model output and only a proposal. Each is verified by search before it is used (§5).
- **Follow-up questions**: at most 2 items `{path, question, assumed}`.
  - They are asked only for a brief field with no explicit value whose default changes the class's shape:
    length, intensity, warm-up.
  - **They never block.** The brief uses `assumed`, and the chat says so ("I used 5 minutes").
  - An answer is an ordinary chat change. A quick-answer button sends a prepared sentence.
- The interpret prompt and schema change, so `suite_version` is bumped and eval cases are added (ADR 0012).

### 4. The sketch

- With the flag on and a class with music, the ADR 0029 §5 hand-off enqueues `design_draft` in mode
  `sketch`.
  - The design runs with no songs: time-based segments, the soundless design's rules.
  - The revision has `origin = "sketch"`.
  - It takes the class credit. Two concurrent hand-offs admit one job and one credit, as today.
- **A sketch is never written anywhere.**
  - Save, update and Send to player refuse a `sketch` base server-side, with the closed reason code
    `not_ready`, before any MOWL or player call.
  - Every hard-coded list of revision origins gets `sketch`, or a test showing it doesn't need it.
- **Chat changes on a sketch.** `revise_draft` accepts a `sketch` base and produces a `sketch`. It follows
  the soundless revise rules, takes no credit, and counts toward the class's chat and revise limits.
- **A soundless class ("No music")** has no sketch: its first design is the class, as today, and its
  approve-and-save is the save alone.

### 5. Songs into the sketch (`design_with_songs`, mode `fill`)

- **Start.**
  - A new web hand-off, `POST /api/classes/{id}/fill-songs`, starts it when the sketch completes. Its
    idempotency key is derived from the sketch job's id. After a closed tab, the class page calls it again.
  - The input adds `base_revision_id`, which must be the class's current sketch. Anything else is
    `stale_base`.
- **Credit.** `consumes_credit` is false in this mode, because the sketch took the class credit. The job
  counts toward the cost cap, the per-user job limit and the class's revise limit.
- **`pool_search`.**
  - The brief is the sketch's segments (duration, intensity, role), the taste (#1133) and the pinned songs.
  - **Pool sources:**
    - The attached Spotify playlists' tracks, marked `favoured`. Read only; the user's playlist is never
      written (Amendment 26).
    - The attached SwedeBeat mixes' tracks, read through MOWL as today's mix source. Each is searched on
      Spotify by title and artist (the `find_songs` rules) and marked `favoured` when found. Nothing is
      sent to or written to SwedeBeat.
    - The pinned songs, each verified by the same search. A song only counts if Spotify really has it.
    - Search fills the rest, as today (Amendment 25).
- **`pool_write` and `pool_analyse`.** Unchanged: the private working copy, its consent, and MOWL's
  analysis.
- **`choose`.**
  - Pinned songs are placed at their positions.
  - Favoured tracks are preferred wherever they fit the shape and the taste.
  - New Not as asked codes, neither of them a failure: `pinned_not_found` (a pinned song is not on Spotify)
    and `source_unreadable` (an attached source could not be read).
- **`design` (new, before the wait).**
  - The design runs on the chosen songs' pool analysis, with the sketch as its base. It keeps the shape
    the user agreed to and re-times it to the songs.
  - It produces a `draft` revision.
- **`AwaitApproval`.**
  - The approval hash covers the song list **and** the draft revision's hash.
  - The wait, its expiry and cancel are as in ADR 0008 §7.

### 6. Refining while it waits

- A **Swap** of one song is replace mode, as `revise_songs` does today, followed by the `design` stage.
- A **chat change** is mode `change`: the design runs again and the songs stay. If the change asks for
  different music, it becomes a swap of the songs it names, or the taste's replace round.
- Each of these is a new `design_with_songs` job on the same list.
  - It supersedes the waiting job with the existing `superseded` semantics: approving the old one is
    refused.
  - It ends in `AwaitApproval` again.
  - It takes no credit, and counts toward the class's revise limit.

### 7. Approve and save, in one step

- **Approval.** The approval route takes `save: true`, valid only for a `fill`, `swap` or `change` job, and
  stores it with the approval. Anything else is `invalid_input`.
- **Phase 2** runs `prepare`, `create`, `fill`, `attach` and `music` as today: the final private playlist,
  verified by id, attached in MOWL and analysed.
- **`fit` replaces phase 2's `design` step.**
  - The final analysis is compared, track by track, with the pool analysis the approved draft was designed on.
  - **Same order, and every length within `CLASSBEAT_FIT_TOLERANCE_SECONDS` (default 2):** the approved draft
    is refitted with the refit rules (`refit_draft`), with no model and no change of shape. The class's
    current revision is that refit.
  - **Otherwise:** the design runs as today. The result is **not** saved, and the class says "the final
    songs differed; check the class and save".
- **Save.** The worker never enqueues (`test_session_guard.py`).
  - When phase 2 completes with `save: true` and the fit kept the approved draft, the class stores
    `save_pending`.
  - The browser calls the existing save route, with an idempotency key derived from the phase-2 job id.
    After a closed tab, the class page resumes it.
  - `save_workout` and its operation are unchanged: at most once, and reconcile before a retry.
- **What the click authorises.** One click approves both writes: the final Spotify playlist, and the MOWL
  workout of the class the user saw. The approve bar says this before the click (§10.2). Every later change
  is saved only with **Update workout**, as today.

### 8. Changes sent while a job runs

- There is still one open job per class.
- A chat message sent while a sketch, fill, swap, change or phase-2 job runs is stored as the next message,
  with status `pending`.
- When the job ends, the web submits it as the next change. The submit is idempotent by message id, and
  resumed after a closed tab.
- The chat shows the message as pending until then.

### 9. Credits and limits

| Job | Credit | Cost cap | Per-user job limit | Class revise limit |
|---|---|---|---|---|
| `interpret_request` | no | yes | yes | no |
| sketch | the class credit | yes | yes | no |
| `revise_draft` on a sketch | no | yes | yes | yes |
| `fill`, `swap`, `change` | no | yes | yes | yes |
| phase 2 (`fit`) | no | yes | yes | no |
| `save_workout` | as today | as today | as today | no |

A class abandoned after its sketch has used its credit, as an abandoned design does today.

### 10. The web

**10.1 The composer** (`/classes/new`, flag on):
- One card, with the text box and the attached-music tags.
- **Add music**: a dropdown with search, Spotify playlists and SwedeBeat mixes in groups, and "No music".
  Multi-select, at most 5. "No music" excludes the rest.
- A sport select, only when running is enabled.
- **Surprise me**: a random entry from a curated table (en, sv, da; within the plan's length), started at
  once.
- **Start**, and an **Advanced settings** link.
- Removed: the starter chips, the ideas box, the service chips, the destination sentence and the long
  playlist list.
- No scrolling at 1280×800, and no horizontal scroll at phone width.

**10.2 The workspace** (the class page of a composer class, flag on):
- **The chat, left:** the request with its tags, the reading with its questions and quick answers, the
  replies, and a message box with add-music and Send.
- **The class, right:**
  - a state chip: "Rough: songs not in yet", "Ready to approve" or "Saved";
  - the profile;
  - the segments, each with its song, artist and source ("Pinned by you", the list's name, "Found by
    classbeat"), and Swap before approval;
  - one status line while a job runs.
- **The approve bar:** "Approving creates a private Spotify playlist in this order and saves a private
  workout to MOWL." and **Approve and save**. After the save: **Update workout**.
- **A "More" menu** holds Beat One, Send to player, the SwedeBeat section, versions and delete.
- **Phone width:** the class first, and the message box sticky at the bottom.

### 11. Unchanged

- The Advanced form.
- Running (ADR 0018).
- The SwedeBeat studio (ADR 0036).
- Amendment 28. When the class's song source is SwedeBeat, the same flow runs with `/catalog/match` instead
  of Spotify search, and with no pool write or final playlist (#1179).
- Attaching a **SwedeBeat playlist** (as opposed to a mix) needs a gateway read path that ADR 0036 §8 does not
  allow. It is out of scope, and needs its own amendment.

## Alternatives

- **Songs and sketch together, in one job.** The first feedback comes later, and every change of shape
  re-picks songs, which costs model calls and pool writes. Rejected: the owner wants to shape first.
- **Questions that block until answered.** This adds a stop to every request that leaves out a length.
  Rejected: an assumed value the user can change costs nothing.
- **The worker enqueues the next job** (fill after the sketch, save after phase 2). This breaks the session
  guard of ADR 0029 §5. The web-side hand-off is already proven, with idempotent and resumable calls.
- **Keep the song approval and the save separate.** Rejected: this is the bulk the owner named. One click
  still precedes every external write, and it covers exactly what the user saw.
- **New job kinds instead of modes.** They would duplicate `design_with_songs`'s phases, its waiting and its
  approval checks. Modes keep one security-reviewed path.

## Consequences

- The approval now covers the design as well as the songs. A model call designs classes whose songs the
  user may reject: the cost of a fill that ends in a swap or a change is the model calls of its `design`
  stage.
- Phase 2 normally needs no model call (`fit`), so the time after Approve is shorter than today.
- A sketch can be abandoned with the class credit used.
- Two new web hand-offs (fill, save) and one pending message make the class page resumable in more states.
  Each is idempotent and tested with two concurrent callers.
- The old flow and the new one live side by side behind `composer_flow` until the cleanup issue.
