# ADR 0038: The composer flow: describe, shape it together, approve and save

Status: Accepted (owner decision and mockup approval, 2026-11-17; roadmap Amendment 29; issue #1184).
Implementation: #1185, #1186, #1188, #1189, #1190, #1191. Behind the `composer_flow` flag until the owner's
live check.

Amends:
- [ADR 0008](0008-song-picking.md) §4 (phase 1 ends with the song list; phase 2 designs) for composer
  classes: phase 1 also designs the class, the approval covers the list and the draft, and phase 2 fits
  instead of designing (§5, §7 below). §5 (Ask for changes) is replaced by §6 below for composer classes.
  §6 (Resume) gets the rules of §9. §7 (waiting, expiry, cancel) is unchanged except for §6 and §12 below.
- [ADR 0007](0007-classes-revisions-and-saving.md) §5: a save normally approves one revision by the user's
  click on Save. Here the click on Approve covers the save of the **refit** of the approved draft, which has
  a different hash (§7).
- [ADR 0029](0029-ai-first-class-design.md) §5: the hand-off can start a sketch (§4), and more web-side
  hand-offs follow the same pattern (§5, §7, §8).

Keeps: ADR 0007 saving as an operation, ADR 0008's approval before every external write, ADR 0029 §4
(explicit instructions vs assumptions), roadmap Amendments 25 to 27.

Not part of this design: SwedeBeat. See §3 (attached music) and §11.

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
- `CLASSBEAT_COMPOSER_FLOW` stays unset in `compose.prod.yaml` and in `scripts/check-prod-compose.sh`, as
  the other switches that wait for the owner's live check do.

### 3. The reading (`interpret_request`)

- **Input `music_sources`**: at most 5 entries `{kind: "spotify_playlist", id}`.
  - **Only Spotify playlists can be attached.** A SwedeBeat mix has no readable track list: ADR 0016 §1 item
    4 says it cannot be read outside the SwedeBeat app, and the only existing path
    (`design/swedebeat_music.py`) writes a playlist into MOWL, downloads up to 250 MB and spends one of the
    user's daily analysis slots. A SwedeBeat playlist needs a gateway path that ADR 0036 §8 does not allow.
    Neither is part of this design (§11).
  - **Ownership.** `interpret_request`'s admit reads only the database, and Spotify playlists have no stored
    listing. So admit checks only the id's shape (Spotify's base-62 id) and the count of 5. The real check is
    that every read in `fill` uses the user's own Spotify connection and token: a playlist that token cannot
    read is `source_unreadable` (§5). A followed playlist the user can read therefore works, and is only
    read, never written.
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
- **A revision that is not ready is never written anywhere (one shared rule).**
  - Readiness is carried by the revision's `origin`, which is fixed when the row is inserted. **Revisions
    stay immutable** (ADR 0007 §2, migration 0004's `revisions_immutable`): nothing here updates one.
  - A revision is *not ready* when its origin is **`sketch`** or **`composer_preview`** (§5). A preview is
    never ready, for good: not while its job waits, and not after the job was approved, replaced, cancelled
    or expired. Only phase 2's refit or re-design (`origin = design_with_songs`) is ready.
  - One server-side helper, `revision_ready`, refuses a not-ready revision with the closed reason code
    `not_ready`. Every route and job that takes a revision id calls it:
    - `save_workout` and `update_workout`;
    - Send to player;
    - duplicate;
    - `POST /api/classes/{id}/current`;
    - `refit_draft`, `revise_songs` and `studio_revise` on it;
    - `revise_draft` on a `composer_preview` (a `sketch` is revised by `revise_draft`; a preview is changed
      through §6, never by a plain revise).
  - A guard test lists every route and job kind that takes a revision id and fails when one does not call
    `revision_ready` or is not on a short reviewed allowlist.
  - Every hard-coded list of revision origins gets `sketch` and `composer_preview`, or a test showing it
    doesn't need them. If the origin is constrained in the database, a migration adds the two values (an
    insert-time change only).
- **Chat changes on a sketch.** `revise_draft` accepts a `sketch` base and produces a `sketch`. It follows
  the soundless revise rules, takes no credit, and counts toward the class's revise limit (§9).
- **A soundless class ("No music")** has no sketch: its first design is the class, as today, and its
  approve-and-save is the save alone.

### 5. Songs into the sketch (`design_with_songs`, mode `fill`)

- **Start.**
  - A new web hand-off, `POST /api/classes/{id}/fill-songs`, starts it automatically when a sketch is the
    class's current revision, it has no open job and no fill has been started for it. The user starts it by
    **Find songs again** after a failed, replaced or expired fill: then the current revision is a sketch or a
    preview whose job is no longer waiting.
  - The input's `base_revision_id` must be **the class's latest sketch**. The new draft is inserted with
    ADR 0007 §2's compare-and-set on the class's current revision, whatever that is. Anything else is
    `stale_base`.
  - **Idempotency key** = a hash of the class id, the sketch's **revision** id and an `attempt` number stored
    on the class. A new sketch (a pending shape change) gets a new key. A retry after a refusal or failure
    increments `attempt`.
  - **A refusal or failure** is stored on the class (the ADR 0029 §5 pattern) and shown with a **Find songs
    again** button that retries with the next `attempt`. No credit is used.
- **Credit.** `consumes_credit` is false in this mode, because the sketch took the class credit. The job
  counts toward the cost cap and the per-user job limit. It is **exempt from the class's revise limit**, so a
  user who used the limit on the sketch is never left with a spent credit and no way to get songs (§9).
- **`pool_search`.**
  - The brief is the sketch's segments (duration, intensity, role), the taste (#1133) and the pinned songs.
  - **Pool sources:**
    - The attached Spotify playlists' tracks, read with the user's own connection and marked `favoured`. Read
      only: the user's playlist is never written (Amendment 26). One that cannot be read is Not as asked
      `source_unreadable`, and the others still count.
    - The pinned songs, each verified by Spotify search (the `find_songs` rules). A song only counts if
      Spotify really has it.
    - Search fills the rest, as today (Amendment 25).
- **`pool_write` and `pool_analyse`.** Unchanged: the private working copy, its consent, and MOWL's
  analysis.
- **`choose`.**
  - Pinned songs are placed at their positions.
  - Favoured tracks are preferred wherever they fit the shape and the taste.
  - New Not as asked codes, neither of them a failure: `pinned_not_found` (a pinned song is not on Spotify)
    and `source_unreadable`.
- **`design` (new, before the wait).**
  - The design runs on the chosen songs' pool analysis, with the sketch as its base. It keeps the shape the
    user agreed to and re-times it to the songs.
  - **The draft is stored with `origin = "composer_preview"`.** It becomes the class's current revision (so
    the page shows it), but it is *not ready* (§4): nothing can save, update or send it, and nothing ever
    updates it. The pool analysis it was fitted to is stored in the draft's own `music` object, as every
    draft's is (§7 uses it).
- **`AwaitApproval`.** The wait, its expiry and cancel are as in ADR 0008 §7, with §12 below.
- **The approval's identity.**
  - `POST /api/jobs/{id}/approve` names the job, the `song_list` revision and the draft revision.
  - **`approval_hash`** = SHA-256 over the canonical bytes of `{song_list_content_hash, draft_content_hash}`,
    each as ADR 0008 §4 and ADR 0007 §5 define them. A different list or draft is `hash_mismatch`.
  - Phase 2's `prepare` revalidates, fenced, as ADR 0008 §6 does: the list is still the class's newest
    `song_list` revision, the draft is still this job's draft, and the hash is computed again.

### 6. Refining while it waits (swap and change)

- **One open job per class includes `waiting_for_approval`** (`jobs_one_open_per_class`, migration 0004).
  So a swap or a change cannot be a second job beside the waiting one. It **replaces** it:
  - the create transaction, under the owner lock, **cancels the waiting job first** (so the index admits
    the new one) and then creates the new job, with the cancel's credit settle (a no-op here, since a fill
    takes none);
  - **if the create is refused** (`revision_limit`, `quota` with detail `cost` for the cost cap,
    `pool_busy`), the transaction rolls back and the waiting job and its approvable list are kept; the
    refusal is shown on the chat message;
  - the replaced job's status is `cancelled`, so approving it is refused (`superseded`). Its preview is not
    touched and is never ready (§4).
- **A swap** of one song is replace mode, as `revise_songs` does today. It stores a new `song_list`
  revision, then runs the `design` stage.
- **A change** keeps the songs and runs `design` again.
  - It **always stores a new `song_list` revision** with the same content, so ADR 0008 §5's test "the newest
    `song_list` revision" still tells an old job from a new one.
  - It runs `pool_write` and `pool_analyse` again on the kept songs (a new run: the working copy's consent
    stands, and it takes its own analysis slot under the daily limit), so no analysis needs to be stored
    beyond the draft's own `music` (§5).
  - **A change that does not fit the kept songs** is decided by the server, by number: when the re-designed
    class's length differs from the songs' total by more than 10%, the change is stored as a new `sketch`
    revision (the songs dropped), the waiting job is cancelled, and the fill hand-off (§5) starts again for
    the new sketch. The model does not decide this.
- Swap and change count toward the class's revise limit and take no credit (§9).

### 7. Approve and save, in one step

- **Approval.** The approval route takes `save: true`, valid only for a `fill`, `swap` or `change` job, and
  stores it on the approval receipt. Anything else is `invalid_input`.
- **Phase 2** runs `prepare`, `create`, `fill`, `attach` and `music` as today: the final playlist (asked for
  private; Amendment 27), verified by id, attached in MOWL and analysed.
- **`fit` replaces phase 2's `design` step.**
  - The final analysis is compared, like with like, with the pool analysis stored in the approved draft's
    `music` object. A draft's `music.songs[]` holds each song as heard in the class (`start`, `end`, `bpm`),
    so the same function builds `songs[]` from the final analysis, and:
    - **the same tracks in the same order** (the fingerprint here is of track ids only, so `refit_draft`'s
      `music_changed` guard does not trip on a different playlist id);
    - **every song's heard length, `end - start`, within `CLASSBEAT_FIT_TOLERANCE_SECONDS`** (default 2)
      **and the last song's `end` (the music's total length) within 5 seconds**. BPM and beat one are not
      compared: the same recording analyses the same way.
  - **Within both:** the approved draft is refitted with the refit rules (`refit_draft`), with no model and
    no change of shape. The refit is a new revision with `origin = design_with_songs` (ready) and becomes the
    class's current revision.
  - **Otherwise:** the design runs as today and stores a revision with `origin = design_with_songs`. It is
    **not** saved by the flow, and the class says "the final songs differed; check the class and save". The
    user's own Save is the only way it is written.
- **`save_pending`.** The worker never enqueues (`test_session_guard.py`). When phase 2 completes with
  `save: true` and the fit kept the draft, the class stores `save_pending = {revision_id, content_hash}` of
  the refit.
  - The browser calls the existing save route for **exactly that revision and hash**, never for "the current
    revision". The request carries `save_pending_job_id`, and the save admit checks the revision and hash
    against `save_pending` **only when that parameter is present**: a user's manual Save is unaffected.
  - The idempotency key is derived from the phase-2 job id. After a closed tab, the class page resumes it.
  - **`save_pending` is cleared** in the same transaction that makes any newer revision current, and when
    the save job reaches any terminal state. A failed save shows its own error and the ordinary **Save**
    button, and the driver (§8) does not try again by itself.
  - `save_workout` and its operation are unchanged: at most once, and reconcile before a retry.
- **A resume** (ADR 0008 §6) of a failed phase 2 of a composer approval keeps the approval's `save` flag, so
  it sets `save_pending` when it completes. It takes **no credit** (§9).
- **What the click authorises.** One click approves both writes: the final Spotify playlist, and the MOWL
  workout of the class the user saw (refitted by §7). The approve bar says this before the click (§10.2).
  Every later change is saved only with **Update workout**, as today.

### 8. Changes sent while a job runs

- There is still one open job per class.
- A chat message sent while a sketch, fill, swap, change or phase-2 job runs is stored with status `pending`
  (storage and the idempotent submit by message id are built in #1186; each later job kind uses them).
- **The web's one driver.** When the class has no **queued or running** job (a job waiting for approval does
  not count: a submit replaces it, §6), the class page does the first of these:
  1. a sketch is current with no fill: if a message is pending, submit it as a sketch change, else start the
     fill (§5). A shape change made while the sketch job ran is therefore applied **before** songs are
     chosen;
  2. `save_pending` is set: save exactly that revision (§7);
  3. a pending message exists: submit it. After a fill, swap or change it is a **change** that replaces the
     waiting job (§6). After a phase 2 it is an ordinary revise of the saved class.
- **Approve is disabled while a message is pending**, so the user never approves something older than their
  last instruction.
- **A refused submit** (`revision_limit`, `quota`, `chat_full`) marks the message `failed` with
  the reason and puts its text back in the message box. It never stays pending.

### 9. Credits and limits

| Job | Credit | Cost cap | Per-user job limit | Class revise limit |
|---|---|---|---|---|
| `interpret_request` | no | yes | yes | no |
| sketch | the class credit | yes | yes | no |
| `revise_draft` on a sketch | no | yes | yes | yes |
| `fill` | no | yes | yes | **no** (exempt) |
| `swap`, `change` | no | yes | yes | yes |
| phase 2 (`fit`) | no | yes | yes | no |
| resume of a composer phase 2 | **no** | yes | yes | no |
| `save_workout` | as today | as today | as today | no |

A class abandoned after its sketch has used its credit, as an abandoned design does today. ADR 0008 §6's
"the new job reserves credit again" applies only to a non-composer resume: here the sketch committed the
class credit.

### 10. The web

**10.1 The composer** (`/classes/new`, flag on):
- One card, with the text box and the attached-music tags.
- **Add music**: a dropdown with search listing the user's Spotify playlists, and "No music". Multi-select,
  at most 5. "No music" excludes the rest. (No SwedeBeat entries: §3.)
- A sport select, only when running is enabled.
- **Surprise me**: a random entry from a curated table (en, sv, da; within the plan's length), started at
  once.
- **Start**, and an **Advanced settings** link.
- Removed: the starter chips, the ideas box, the service chips, the destination sentence and the long
  playlist list.
- No scrolling at 1280×800, and no horizontal scroll at phone width.

**10.2 The workspace** (the class page of a composer class, flag on):
- **The chat, left:** the request with its tags, the reading with its questions and quick answers, the
  replies, and a message box with add-music and Send. Pending and failed messages show their state.
- **The class, right:**
  - a state chip: "Rough: songs not in yet", "Ready to approve", "Songs expired" or "Saved";
  - the profile;
  - the segments, each with its song, artist and source ("Pinned by you", the playlist's name, "Found by
    classbeat"), and Swap before approval;
  - one status line while a job runs.
- **The approve bar:** "Approving saves a private workout to MOWL and creates a playlist in your Spotify in
  this order (we ask Spotify for private)." and **Approve and save**. After the save: **Update workout**.
- **A "More" menu** holds Beat One, Send to player, the SwedeBeat section, versions and delete.
- **Phone width:** the class first, and the message box sticky at the bottom.

### 11. Unchanged, and out of scope

- The Advanced form, running (ADR 0018) and the SwedeBeat studio (ADR 0036).
- **Spotify stays required for a class with songs** (Amendment 25). A SwedeBeat-only song source was planned
  (Amendment 28) and **parked** by the owner on 2026-11-17: its design step needs a per-track map of sections,
  drops and breakdowns that a SwedeBeat match does not give (`docs/ideas.md` §4).
- Attaching SwedeBeat mixes or playlists as favoured music is out of scope (§3) and needs its own amendment.

### 12. Expiry

- When the waiting job expires (`queue_expire`, ADR 0008 §7), nothing about a revision changes: revisions
  are immutable and `queue_expire` moves no pointer.
  - The class's current revision stays the preview, which is never ready (§4). The page derives the state
    from the job: a current `composer_preview` whose job is expired, cancelled or replaced shows the chip
    "Songs expired".
  - **Find songs again** starts a new fill (no credit, §5, with the next `attempt`) from the class's latest
    sketch.
  - A swap or a change cannot start from an expired job: ADR 0008 §5's seed rule applies to a waiting list
    only.

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
- **Store the pool analysis separately** (ADR 0008 §4 stores none). Not needed: the draft's own `music`
  holds the analysis it was fitted to, and a change analyses the kept songs again.

## Consequences

- The approval now covers the design as well as the songs. A model call designs classes whose songs the
  user may reject: the cost of a fill that ends in a swap or a change is the model calls of its `design`
  stage.
- Phase 2 normally needs no model call (`fit`), so the time after Approve is shorter than today.
- A sketch can be abandoned with the class credit used.
- The shared `revision_ready` rule and its guard test add a small cost to every route that takes a revision
  id, and close the path by which a draft made before the approval could be saved. Readiness is carried by
  the origin, so revisions stay immutable and no migration of their state is needed.
- Three hand-offs (fill, pending submit, save) make the class page resumable in more states. Each is
  idempotent, runs in a fixed order (§8) and is tested with two concurrent callers.
- The old flow and the new one live side by side behind `composer_flow` until the cleanup issue.
- Only Spotify playlists can be attached as favoured music. SwedeBeat music stays on its own studio path.
