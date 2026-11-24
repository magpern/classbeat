# ADR 0038: The composer flow: describe, shape it together, approve and save

Status: Accepted (owner decision and mockup approval, 2026-11-17; roadmap Amendment 29; issue #1184). Amended 2026-11-24 (roadmap Amendment 36; the sketch gate, a ride follows its class, the music decision).
Implementation: #1185, #1186, #1188, #1189, #1190, #1191. Behind the `composer_flow` flag until the owner's
live check.

Amends:
- [ADR 0008](0008-song-picking.md) §4 and §5 for composer classes: phase 1 also designs the class, and the
  design is stored **inside the `song_list` revision** (content `v: 3`, §5), so the approval's hash covers
  the list and the design. Phase 2 fits instead of designing (§7). §5's Ask for changes is replaced by §6
  below. §6 (Resume) gets the rules of §7 and §9. §7 (waiting, expiry, cancel) is unchanged.
- [ADR 0007](0007-classes-revisions-and-saving.md) §5: a save normally approves one revision by the user's
  click on Save. Here the click on Approve covers the save of **the first draft**, stored in phase 2 from the approved design
  (§7). It has a different hash from anything the user saw, and its hash is checked against `save_pending`.
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
    length and intensity. The brief has no warm-up field yet, so a question about the warm-up needs a later
    brief change (a design decision of its own); the reading never asks one.
  - **They never block.** The brief uses `assumed`, and the chat says so ("I used 5 minutes").
  - An answer is an ordinary chat change. A quick-answer button sends a prepared sentence.
- The interpret prompt and schema change, so `suite_version` is bumped and eval cases are added (ADR 0012).

### 4. The sketch

- With the flag on and a class with music, the ADR 0029 §5 hand-off enqueues `design_draft` in mode
  `sketch`.
  - The design runs with no songs: time-based segments, the soundless design's rules.
  - The revision has `origin = "sketch"`.
  - It takes the class credit. Two concurrent hand-offs admit one job and one credit, as today.
- **The sketch stays the class's current revision until phase 2.** Nothing before the approval inserts a
  draft revision (§5), so there is no preview revision, no readiness state and no current-pointer question
  while songs are found, swapped or changed, replaced, expired or failed.
- **A sketch is never written anywhere.** One server-side helper, `revision_ready`, refuses an origin that
  is not ready with the closed reason code `not_ready`. The only not-ready origin is `sketch`.
  - Every route and job that takes a revision id calls it: `save_workout`, `update_workout`, Send to
    player, duplicate, `POST /api/classes/{id}/current`, `refit_draft`, `revise_songs`, `studio_revise`.
  - **A guard test** lists every route and job kind that takes a revision id, and enumerates every origin
    literal. It fails when a route does not call `revision_ready` and is not on a reviewed allowlist, or when
    an origin is neither classified ready nor not ready. The allowlist records why each entry is safe: the
    studio outputs, `spotify-sync` and `studio-songs` are unreachable for a non-studio class
    (`current_draft.py`); `swedebeat_match` is reachable for a composer song list and is safe because it
    writes nothing and sends only titles and artists through the owner's gateway, as for any song list
    today.
  - Every hard-coded list of revision origins gets `sketch`, or a test showing it doesn't need it. If the
    origin is constrained in the database, a migration adds the value (an insert-time change only).
  - Revisions stay immutable (ADR 0007 §2, `revisions_immutable`): nothing here updates one.
- **Chat changes on a sketch.** `revise_draft` accepts a `sketch` base (the sketch is current, so its base is
  never stale) and produces a `sketch`. It follows the soundless revise rules, takes no credit, and counts
  toward the class's revise limit (§9). `Provenance`'s lineage rule (`agent/records.py`, `_lineage`) is
  extended so that origin `sketch` may name a base and a request, as a revise does.
- **A soundless class ("No music")** has no sketch: its first design is the class, as today, and its
  approve-and-save is the save alone.

### 5. Songs into the sketch (`design_with_songs`, mode `fill`)

- **Start.**
  - A new web hand-off, `POST /api/classes/{id}/fill-songs`, starts it automatically when the class's
    current revision is a sketch with no open job and no fill started for it. The user starts it by **Find
    songs again** after a failed, replaced or expired fill.
  - The input's `base_revision_id` must be the class's current revision, which is a sketch; anything else
    is `stale_base`.
  - **Idempotency key** = a hash of the class id, the sketch's **revision** id and an `attempt` number stored
    on the class. A new sketch (a shape change made before the fill) gets a new key. A retry after a
    refusal or failure increments `attempt`.
  - **A refusal or failure** is stored on the class (the ADR 0029 §5 pattern) and shown with **Find songs
    again**. No credit is used.
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
  - **Its result is not a draft revision.** It is stored in the **`song_list` revision**, whose content
    becomes `v: 3` with a `preview`: `{content, music}`: the draft's content and a **small** `music`
    (only what the fit compares: the track-id fingerprint and each song's `start` and `end`, below), and a
    reference to the
    `design` stage's `agent_runs` row. The revision stays what ADR 0008 §5 says: immutable, never current,
    never revised or saved as a draft. **The whole canonical `content` stays at or under 64 KB**:
    migration 0004's single check on every revision's `content` (`class_revisions_content`,
    `MAX_CONTENT_BYTES`) is not raised. So `preview.music` is **not** the full pool analysis: it holds only
    what the fit compares (the track-id fingerprint and per-song `start` and `end`, §7), and it stays inside
    `content`, so the hash covers exactly what the fit compares against. The size is checked **before the
    insert**: an over-size result fails the job with the existing `invalid_output` category (no credit, and
    the user can **Find songs again**), never as a database error in the middle of the job. A maximum-size
    case is tested.
  - **Every reader of a `v: 3` list handles `preview`** (the songs view, the seed of a replace, `revise_songs`,
    `swedebeat_match`, `prepare`), and the hash is always computed over the full stored content.
  - So nothing before the approval can be saved, updated or sent: there is no revision to name.
- **`AwaitApproval`.** The wait, its expiry and cancel are as in ADR 0008 §7.
- **The approval's identity is ADR 0008 §5's, unchanged.** `POST /api/jobs/{id}/approve` names the job and
  the `song_list` revision, and `content_hash` covers the whole `v: 3` content, so the approval covers the
  songs **and** the design. A different list or design is `hash_mismatch`. Phase 2's `prepare` revalidates,
  fenced, as ADR 0008 §6 does: the list is still the class's newest `song_list` revision, and its hash is
  computed again.

### 6. Refining while it waits (swap and change)

- **One open job per class includes `waiting_for_approval`** (`jobs_one_open_per_class`, migration 0004).
  So a swap or a change cannot be a second job beside the waiting one. It **replaces** it:
  - the create transaction, under the owner lock, **cancels the waiting job first** (so the index admits
    the new one) and then creates the new job, with the cancel's credit settle (a no-op here, since a fill
    takes none);
  - **if the create is refused** (`revision_limit`, `quota` with detail `cost` for the cost cap,
    `pool_busy`), the transaction rolls back and the waiting job and its approvable list are kept; the
    refusal is shown on the chat message;
  - the replaced job's status is `cancelled`, so approving it is refused (`superseded`).
- **A swap** of one song is replace mode, as `revise_songs` does today. It stores a new `song_list` revision
  (with a new preview).
- **A change** keeps the songs and runs `design` again.
  - It **always stores a new `song_list` revision** with the same songs and a new preview, so ADR 0008 §5's
    test "the newest `song_list` revision" still tells an old job from a new one.
  - It runs `pool_write` and `pool_analyse` again on the kept songs (a new run: the working copy's consent
    stands, and it takes its own analysis slot under the daily limit), so no analysis is stored beyond the
    preview's own `music`.
  - **A change that does not fit the kept songs** is decided by the server, by number: when the re-designed
    class's length differs from the songs' total by more than 10%, the change produces a new `sketch`
    revision (the songs dropped) through the ordinary sketch revise, and the fill hand-off (§5) starts again
    for it.
- Swap and change count toward the class's revise limit and take no credit (§9). Their input carries the
  sketch as `base_revision_id` (§5), so every `design_with_songs` job names the sketch it started from.
- **What a change changes lives in the waiting song list's preview only.** If that list expires, is cancelled
  or its replacement fails, the sketch is back to the shape it had before the fill, and changes made since
  are not kept as a revision. They stay in the chat, and the user can send them again. This is accepted,
  rare (the wait is up to 7 days) and shown by the chat history.
- **A swap or change that fails while running** leaves no approvable list, because the waiting job was
  already cancelled. The class keeps its sketch, the page offers **Find songs again**, and the user's change
  stays in the chat. This is tested.

### 7. Approve and save, in one step

- **Approval.** The approval route takes `save: true`, valid only for a `fill`, `swap` or `change` job, and
  stores it, with the approved `song_list` revision id and `content_hash`, on the **approval receipt**.
  Anything else is `invalid_input`.
- **Phase 2** runs `prepare`, `create`, `fill`, `attach` and `music` as today: the final playlist (asked for
  private; Amendment 27), verified by id, attached in MOWL and analysed.
- **`fit` replaces phase 2's `design` step.** The worker calls the fit function directly. It does **not**
  enqueue a `refit_draft` job, so Beat One's origin and its `refit_limit` are not involved.
  - The final analysis is compared, like with like, with the track-id fingerprint and the per-song `start` and
    `end` in the approved list's `preview.music`: `songs[]` is built from the final analysis by the same
    function as the preview's, and
    - **the same tracks in the same order** (the fingerprint here is of track ids only, so `refit_draft`'s
      `music_changed` guard does not apply);
    - **every song's heard length, `end - start`, within `CLASSBEAT_FIT_TOLERANCE_SECONDS`** (default 2)
      **and the last song's `end` (the music's total length) within 5 seconds**. BPM and beat one are not
      compared: the same recording analyses the same way.
  - **Within both:** the preview is re-timed to the final analysis with `fit_to_music`, with no model and no
    change of shape.
  - **Otherwise:** the design stage runs with a model, with **the approved preview's content as its base**
    (not "as today" with no base), so the shape the user approved is kept.
  - **Either way the first real draft is stored in phase 2's final transaction** with
    `origin = design_with_songs` (as today's phase 2 design), `music.song_list_id` as today, and ADR 0007 §2's
    compare-and-set from the sketch, which is still the current revision. Its provenance names the model
    run that produced the design: the preview's `design` run on a fit, the new run otherwise. That is
    ≥ 1 run, as `Provenance` requires, and a non-revise origin names no base, as it does today. If
    `Provenance`'s validation cannot express "a run of this job's earlier stage", the Developer of #1189
    extends it for exactly this case.
  - **If the fit did not keep the preview** the class says "the final songs differed; check the class and
    save", and the flow does not save: only the user's own Save writes it.
- **`save_pending`.** The worker never enqueues (`test_session_guard.py`). When phase 2 completes with
  `save: true` and the fit kept the preview, the class stores `save_pending = {revision_id, content_hash}` of
  the draft just stored.
  - The browser calls the existing save route for **exactly that revision and hash**, never for "the current
    revision". The request carries `save_pending_job_id`, and the save admit checks the revision and hash
    against `save_pending` **only when that parameter is present**: a user's manual Save is unaffected.
  - The idempotency key is derived from the phase-2 job id. After a closed tab, the class page resumes it.
  - **`save_pending` is cleared** when a save or update job for the class is admitted (by either route), in
    the same transaction that makes any newer revision current, and when the save job reaches any terminal
    state. *As built (#1189):* the admit clears it; "a newer revision" is a **read rule**, not a write in
    every transaction that moves the pointer: `save_pending` counts only while it names the class's current
    **and newest** revision (`saving/save_pending.current`, the only reader: the save admit and the class
    answer), so the transaction that inserts any newer revision voids it, whether that revision becomes
    current or not, and a pointer moved back to the draft does not revive it. A later `song_list` revision
    (Change songs before the save) voids it too, and the user saves by hand. A trigger was not used: the
    queue check admits no user function other than the pinned guard triggers. The terminal-state clear
    holds by construction: the admit cleared it, and no phase 2 can set it again while the save job is the
    class's one open job. While it is set the page shows "Saving…" and not the Save button, so a manual Save and the driver
    can never both save. A failed save shows its own error and the ordinary **Save** button, and the driver
    (§8) does not try again by itself.
  - `save_workout` and its operation are unchanged: at most once, and reconcile before a retry.
- **A failed phase 2 and its resume** (ADR 0008 §6).
  - The page shows "The playlist was not created" with **Create the playlist again** (a resume), not "Songs
    expired".
  - **The resume reads the approved list, hash and `save` from the receipt**, never from the browser's
    request. `prepare` revalidates them and the sketch's currency, then runs `fit` as above and sets
    `save_pending` when the approval had `save: true`.
  - **The resume's base comes from the receipt**, not from the browser (ADR 0008 §6). After a revise of the
    sketch the sketch is no longer the approved list's base, so `prepare` refuses the resume `stale_base`,
    and the chip stops offering **Create the playlist again**.
  - It takes **no credit** (§9). The resume stays available until a newer song list exists, as in ADR 0008
    §6.
  - *As built (#1189):* the resume is the hand-off `POST /api/classes/{id}/resume-songs` (an
    `Idempotency-Key`, no body: whatever the browser sends is ignored), which builds the `resume` input from
    the receipt and creates it with no credit; `POST /api/jobs` mode `resume` for a composer list is
    `receipt_invalid`, since that kind's static credit would be reserved.
- **The class answer** (#1189): `save_pending {revision_id, content_hash, job_id}` (null while it does not
  count) and `phase2`: `{state: "failed"}` while the newest list's approval can be resumed and its sketch is
  current, `{state: "differed"}` while the current revision is a phase-2 draft whose final songs differed
  (provenance `fit: "redesigned"`, not saved by itself), else null. The draft's provenance records
  `fit: "kept" | "redesigned"`.
- **What the click authorises.** One click approves both writes: the final Spotify playlist, and the MOWL
  workout of the class the user saw (re-timed by §7). The approve bar says this before the click (§10.2).
  Every later change is saved only with **Update workout**, as today.

### 8. Changes sent while a job runs

- There is still one open job per class.
- A chat message sent while a sketch, fill, swap, change or phase-2 job runs is stored as the next message,
  with status `pending` (storage and the idempotent submit by message id are built in #1186; each later job
  kind uses them).
- **A pending message is submitted by the state of the class**, never by a guess:

  | State when the job ends | The message becomes |
  |---|---|
  | a song list is waiting for approval | a **change**, which replaces the waiting job (§6) |
  | the current revision is a sketch and no song list waits (expired, cancelled, failed or replaced) | an ordinary revise of the sketch (§4); the new sketch has no fill started for it, so driver rule 1 starts one. **Find songs again** is only for a fill that failed, was replaced or expired for the *same* sketch |
  | the current revision is a draft (after phase 2, or a soundless class) | an ordinary revise of the saved class |
  | anything else | `failed` with `not_changeable` |

- **The web's one driver.** When the class has no **queued or running** job (a job waiting for approval does
  not count: a submit replaces it, §6), the class page does the first of these:
  1. a sketch is current with no fill started for it: if a message is pending, submit it as a sketch revise,
     else start the fill (§5). A shape change made while the sketch job ran is therefore applied **before**
     songs are chosen;
  2. `save_pending` is set: save exactly that revision (§7);
  3. a pending message exists: submit it as the table says.
- **Approve is disabled while a message is pending**, so the user never approves something older than their
  last instruction.
- **A refused submit** (`revision_limit`, `quota`, `chat_full`) marks the message `failed` with the reason
  and puts its text back in the message box. It never stays pending.

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
  - a state chip, derived from the current revision and the songs' state: "Rough: songs not in yet",
    "Ready to approve", "Songs expired" (**Find songs again**), "The playlist was not created" (**Create the
    playlist again**), "Saving…" or "Saved";
  - the profile;
  - the segments, each with its song, artist and source ("Pinned by you", the playlist's name, "Found by
    classbeat"), and Swap before approval;
  - one status line while a job runs.
- **The approve bar:** "Approving saves a private workout to MOWL and creates a playlist in your Spotify in
  this order (we ask Spotify for private)." and **Approve and save**. After the save: **Update workout**.
- **A "More" menu** holds Beat One, Send to player, the SwedeBeat section, versions and delete.
- **Phone width:** the class first, and the message box sticky at the bottom.
- *As built (2026-11-18, #1206): the reading window.* The workspace shows from the first second after
  Start, not when the reading lands. Until the class has a stored interpretation, `composer` in the class
  answer (`classes/composer_view.py`) is true only while the class's `interpret_request` job is **queued or
  running**, its input says `composer: true` (the server field set at admit, #1185) with a music choice other
  than "No music", **and** the `composer_flow` gate is on now. The rule reads the job through the owner-scoped
  class read (`owner_id` and `class_id`, the job's own columns; no change to the jobs table, the queue or the
  lease code). A class whose job input has no marker, every older class, a class with the flag off, a "No
  music" class and a reading that ended without a stored interpretation (failed, cancelled, expired) answer
  `composer: false` and keep the page they had; once the interpretation is stored the answer follows its job
  input as before. In the window the workspace shows the request, the job status line ("Reading your
  request…") and a "No sketch yet" chip (state `reading`, no version yet) in place of "Rough: songs not in yet"; the
  chat box says there is no version to change yet, and the page's one hand-off (`useHandoff`) starts the
  sketch when the reading is stored, in the same mounted workspace.

### 11. Unchanged, and out of scope

- The Advanced form, running (ADR 0018) and the SwedeBeat studio (ADR 0036).
- **Spotify stays required for a class with songs** (Amendment 25). A SwedeBeat-only song source was planned
  (Amendment 28) and **parked** by the owner on 2026-11-17: its design step needs a per-track map of sections,
  drops and breakdowns that a SwedeBeat match does not give (`docs/ideas.md` §4).
- Attaching SwedeBeat mixes or playlists as favoured music is out of scope (§3) and needs its own amendment.

### 12. Expiry

- When the waiting job expires (`queue_expire`, ADR 0008 §7), nothing about a revision changes: revisions
  are immutable, `queue_expire` moves no pointer, and the sketch was current all along.
  - The page derives "Songs expired" from the songs' state. **Find songs again** starts a new fill (no
    credit, §5, with the next `attempt`) from the current sketch.
  - Chat still works: the sketch is current, so a message is an ordinary sketch revise (§8).
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
- **Store the pre-approval design as a draft revision** (an earlier version of this ADR). It needed a
  not-ready state or origin for the draft, a current pointer that moved before the approval, and special
  cases for expiry, replacement and failure, and two review rounds found gaps in each. Storing it inside the
  immutable `song_list` revision needs none of them.
- **Store the pool analysis separately** (ADR 0008 §4 stores none). Not needed: the preview's own `music`
  holds the little the fit compares (the fingerprint and each song's `start` and `end`), and a change
  analyses the kept songs again.

## Consequences

- The approval now covers the design as well as the songs. A model call designs classes whose songs the
  user may reject: the cost of a fill that ends in a swap or a change is the model calls of its `design`
  stage.
- Phase 2 normally needs no model call (`fit`), so the time after Approve is shorter than today.
- A sketch can be abandoned with the class credit used.
- The `song_list` revision grows by the design (`v: 3`), within a stated bound. Everything before the
  approval is inside it, so nothing can be saved early and the sketch stays current until phase 2.
- The shared `revision_ready` rule and its guard test add a small cost to every route that takes a revision
  id, and keep the sketch from being written.
- Three hand-offs (fill, pending submit, save) make the class page resumable in more states. Each is
  idempotent, runs in a fixed order (§8) and is tested with two concurrent callers.
- The old flow and the new one live side by side behind `composer_flow` until the cleanup issue.
- Only Spotify playlists can be attached as favoured music. SwedeBeat music stays on its own studio path.

## Amendment of 2026-11-24: the sketch gate, a ride follows its class, and the music decision (#1491)

Status: Accepted (Architect, under the owner's delegation of 2026-11-24; roadmap Amendment 36).
Implementation: #1484, #1492, #1495, #1496, #1497, #1498.

### Context

A free-text cycling request with music was read and then refused `feature_off` at the hand-off
(#1484). The sketch is a `design_draft` with `NoMusicChoice` in mode `sketch` (§4), so every sketch
path went through the gate of a *no-music class*, `music_kind.admit_soundless`: the environment's
`CLASSBEAT_SOUNDLESS_ENABLED`, the job's plan's coverage and every plan's coverage. That switch exists
for one external write that is not verified live: a MOWL save or update with `PlaylistID: -1` (ADR
0007, amendment of 2026-11-03, owner steps 1 and 2). A sketch is never written anywhere (§4,
`revision_ready`). Separately, the flow could not ask about music: a request that left music open was
read as "pick for me", the song search started as soon as the sketch existed, and an instructor with
no Spotify connection could not start at all (Amendment 25).

### Decision

1. **One sketch gate** (amends §4). Every sketch path checks only that the job's **own plan's**
   `design` route covers the no-songs prompt (`soundless_not_evaluated` otherwise), and in the worker
   `require_covered(model)`. No sketch path reads `CLASSBEAT_SOUNDLESS_ENABLED`: the first sketch's
   admit and run, a sketch edit's admit and run, whether editing a sketch is offered (`can_change`),
   and a change of shape after songs (which runs the sketch revise). A guard test lists these call
   sites. A no-music class (`music_source = none`), its revise, duplicate, Save and Update keep ADR
   0007's gate unchanged, including the switch.
2. **Availability per plan** (amends §2). The composer is available to a user when the operator's
   pair is on (`CLASSBEAT_COMPOSER_FLOW` and a stored `on`, unchanged) **and** the user's plan has the
   sketch's coverage. The interpret admit, the hand-off and `GET /api/connections` (`composer_flow`,
   now per user) use it. When it is not available at hand-off time, the class takes the older route
   (`design_with_songs` or `design_draft`) and is recorded as such (`interpretation.route`), so it is an
   ordinary class from then on.
3. **A ride in progress follows its class.** A class that took the sketch route opens in the composer
   workspace whatever the flag says now. If its plan's coverage lapses, sketch edits and changes of
   shape are refused `soundless_not_evaluated` and the waiting version is kept, while the song search,
   swap, change with songs, Approve and Save still work (none of them runs the no-songs prompt). If the
   operator switches the composer off, the composer's endpoints answer `feature_off` and the workspace
   says the ride is paused; nothing is lost.
4. **`CLASSBEAT_COMPOSER_FLOW` is settable in production** (amends §2's "stays unset"): default off,
   the stored `on` still needed, so the owner can run the live check on a pre-production server
   (#1498).
5. **The music decision** (amends §3 and §5). The reading stores `music_decision` on the class:
   `decided` (`request`) when the request attached playlists, chose a source, named a playlist or gave
   a music taste; otherwise `undecided`. While undecided the workspace asks once, beside the sketch:
   choose for me (`pick`), describe the sound (`describe`, the instructor's own words, at most 200
   characters, given to the song search as data), share up to 5 Spotify playlists (`playlists`), or
   decide later. The sketch does not wait; `fill-songs` refuses `music_undecided` until the decision is
   made, and takes its sources from the decision. The answer can change until a fill was started for
   the current sketch (`music_fixed` after). A row stored before has no decision and is read as
   `decided` (`request`), today's behaviour.
6. **Starting without Spotify** (amends Amendment 25 for composer classes). A request admitted with the
   composer available is not refused `needs_spotify` or `song_picking_unavailable`; the sketch needs no
   Spotify. The Spotify connection and the working-playlist consent are checked by the song search, as
   today (`not_connected`, `consent_missing`), before any write. The older flow keeps the early refusal.
7. **Consent at the moment it is needed.** The song search writes candidates into the instructor's
   public working playlist and attaches it in MOWL (ADR 0008 §2, §4); the workspace explains that and
   asks for the consent before the first search. Only Approve and save creates the final playlist and
   the workout (§7).
8. **Refusals say what happened** (amends §10). The hand-off panel words each stored reason as one of
   three kinds: about the request, about the account (with the action), or about the site ("not
   available here right now; nothing was charged; your request is kept", with no retry that cannot
   succeed).

### Alternatives

- **Fall back to the older flow whenever the soundless switch is off** (#1484's first proposal).
  Rejected as the rule: it makes the composer depend on a switch unrelated to it and hides the cause from
  the operator. Kept only for the real dependency, missing coverage (decision 2).
- **Proceed without asking when only one music option can finish.** Rejected: a missing connection says
  what is possible now, not what the instructor wants.
- **Ask about music before the sketch.** Rejected: the sketch needs no songs, so asking first only adds a
  wait.

### Consequences

- An installation with the soundless switch off can use the composer once its plans' routes cover the
  no-songs prompt (or, outside production, the fake covers).
- Two users on plans with different coverage can see different flows for a new request.
- The older flow stays as the fallback until the owner decides otherwise; it is not retired.
- The sketch still takes the class credit; an instructor who never connects Spotify has used it on a
  sketch they can shape but not fill.
