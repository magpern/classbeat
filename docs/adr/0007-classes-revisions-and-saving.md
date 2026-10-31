# ADR 0007: Classes, revisions and saving to MOWL

Status: Accepted (#152, 2026-10-29)

## Context

M3a (ADR 0005, ADR 0006) ends with a `design_draft` job that stores an immutable `draft` revision
and leaves the user a read-only summary. M3b is the rest of roadmap §6 and §8's class: the class
page, revisions by chat, **Save** and **Update saved** to the user's MOWL account, and usage and
cost (roadmap M3b row, §2, §4, §5, §6, §9). It also owes issue #71 its design: what Amendment 1's
"nothing Spotify-derived is kept indefinitely" means for the data M3b stores.

What exists and is relied on, unchanged:

- Tables (migration 0004): `classes` (`id`, `owner_id`, `title`), immutable `class_revisions`
  (`number`, `kind`, `content`, `content_hash`, `music`, `provenance`, `job_id`, `parent_id`),
  `jobs` (one open job per class, `credit_state`, `approval_*` columns), `job_events`,
  `external_operations`, `mowl_playlist_links`, `agent_runs` (tokens and `cost_micros` per stage
  run, written fenced after every turn).
- The protocol for a write to a user's account: ADR 0005 §7 (a `pending` row committed before the
  call, the call outside any transaction, a fenced outcome, `failed` only when the request provably
  had no effect, `uncertain` otherwise, one automatic reconciliation, never retried automatically,
  the user's **Check again** and **Do it anyway**), with `perform` in `classbeat.operations.protocol`
  and the connection `hold` that keeps a call from going out with another account's credentials.
- `mowl.workout.save(spec, creds, playlist_id, program_id=None)` (`mowl-workouts` 0.5.0) is **not
  one request**: `PUT v1/programs` (a new private program, or with `ProgramID` an update in place),
  then per segment `PUT v1/segments`, `DELETE v1/segments/{id}/intervals`, `PUT .../intervals/multiple`,
  then `PUT v1/programs/{id}/segments`. A re-save writes **new segment copies** and replaces the
  program's list, so a failure part-way leaves a program that exists but is incomplete (create) or
  still shows its old segments with new header fields (update). It sets `IsPublic: false`. It raises
  `MowlError` if an update is answered with another program id (MOWL made a copy). The reads
  available are `api.list_workouts` (My Workouts, `creationDateDesc`, paged) and
  `api.get_workout`; `workout.summarize` is the compact view. MOWL has no idempotency key and no
  delete or search by name used by this project.
- `ADR 0010`: the beta circle's risk acceptance; saving and updating in the user's own MOWL account
  is allowed; Spotify-derived data is kept only while a class refers to it.

M3b's "done when" (roadmap): an E2E run with fakes of design, revise, save and update; the
uncertain-write path; token usage and cost recorded per job (real cost per class is measured at
live activation).

## Decision

Sections 1 to 4 are the class, §5 to §9 the writes to MOWL, §10 usage, §11 retention (#71), §12
and §13 the surface and the tests, §14 the issues.

### 1. Tables and columns

Every new table is **owned** (ADR 0002 §2): `owner_id` referencing `users(id) ON DELETE
CASCADE`, `UNIQUE (owner_id, id)`, owner-inclusive foreign keys, `enable_owner_rls`, application-
supplied `uuid4` ids, one permissive policy `owner_isolation`. Every read in the web process goes
through `UserContext` and a `user_session` (`classbeat.classes`, `classbeat.usage` join the
allowlist of `tests/session_scan.py`); every worker write through `fenced_session`. One migration,
`0005_classes` (issue C1). Nothing here touches the queue functions' columns, so `classbeat-queue
v<N>` does not change and the startup check stays as is.

- **`classes`** gains `current_revision_id uuid NULL` (owner-inclusive FK to `class_revisions`,
  `ON DELETE SET NULL (current_revision_id)`), `brief jsonb NULL` (the validated `Brief`, set once
  by the class's first design, ≤ 4096 bytes) and `music_source jsonb NULL`
  (`{source: "mowl_spotify_playlist", spotify_playlist_id}`, the same value as the first job's
  input). A revision needs both again and revisions deliberately hold no Spotify id (ADR 0006 §6).
- **`class_messages`** (immutable like `job_events`: restrictive `UPDATE` and `DELETE` policies
  `USING (false)`; cascades still delete): `id`, `owner_id`, `class_id` → `classes`
  (`ON DELETE CASCADE`), `seq int` (`UNIQUE (owner_id, class_id, seq)`), `role` check `IN ('user',
  'assistant')`, `body text` (user: the request, 1 to 2000 characters; assistant: the optional
  note, 0 to 300), `job_id uuid NULL` (→ `jobs`, `SET NULL (job_id)`), `base_revision_id` and
  `result_revision_id uuid NULL` (→ `class_revisions`, `SET NULL (<col>)`), `change jsonb NULL`
  (the change summary of §3, ≤ 1024 bytes), `created_at`.
- **`saved_workouts`**: one row per MOWL program classbeat created for a class. `id`, `owner_id`,
  `class_id` → `classes` (`CASCADE`), `program_id bigint` (> 0), `playlist_id bigint NULL`,
  `revision_id uuid NULL` (the revision last written; → `class_revisions`, `SET NULL
  (revision_id)`), `revision_number int`, `content_hash bytea` (that revision's), `state_hash
  bytea NULL` (the normalized MOWL state read back after the last write, §7; NULL means not
  verified), `name text` (≤ 100), `operation_id uuid NULL` (the creating operation, `SET NULL`),
  `detached_at timestamptz NULL` (§9), `created_at`, `updated_at`. `UNIQUE (owner_id, id)` and a
  partial unique index `(owner_id, program_id) WHERE detached_at IS NULL`. The row holds no
  secret and no MOWL URL.
- **`external_operations`** gains `class_id uuid NULL` (owner-inclusive FK to `classes`, `ON DELETE
  CASCADE`; NULL for the attach), `request_context jsonb` (default `'{}'`, ≤ 2048 bytes,
  non-secret: ids of the user's own rows, hashes, MOWL program ids, §8), `pending_at` and
  `call_ended_at timestamptz NULL` (§8). **Every transition into `pending`, the insert and the
  take-over of a `failed` row alike, sets `pending_at = now()`, `call_ended_at = NULL` and the new
  attempt's `request_context`** (baseline, `before_hash`, `bound_seconds`), so nothing of the
  earlier attempt survives. "The call returned" means `call_ended_at >= pending_at`. Its `kind` check gains `mowl_save_workout` and
  `mowl_update_workout`. `result` and `external_id` keep their meaning and limits.
- **`agent_runs`** becomes the durable usage record (§10): `job_id` nullable with `ON DELETE SET
  NULL (job_id)`, new `job_kind text` and `price_version text`, an index `(owner_id,
  started_at)`, and two restrictive policies: `agent_runs_finished_immutable` (`AS RESTRICTIVE FOR
  UPDATE USING (outcome IS NULL) WITH CHECK (true)`) and `agent_runs_no_delete` (`AS RESTRICTIVE FOR
  DELETE USING (false)`). The `WITH CHECK (true)` is required: without it PostgreSQL applies the
  `USING` expression to the *new* row as well, and the fenced update that sets `outcome` and
  `finished_at` would be refused (for `classbeat_app`, and for the owner too under forced row
  security). The `USING` part still applies to the old row, so a finished row can't be changed. The
  migration **backfills `job_kind`** (and `price_version`, `'unknown'`) on the existing finished
  rows from their jobs **before** it creates the policy, or the backfill itself would be refused.
  Deleting a class therefore keeps its usage; deleting the user still cascades (FK actions run
  without row security). The columns are added by C1 (#141, the only migration); US1 (#145) only
  writes them.
- **Error categories** (the three checks that share the list: `jobs.error_category`,
  `external_operations.error_category`, `agent_runs.outcome`) gain `program_changed`,
  `program_missing` and `music_unlinked` (§5, §7).
- **Job kinds** `revise_draft`, `save_workout`, `update_workout` and **operation kinds**
  `mowl_save_workout`, `mowl_update_workout`. **Event kinds** do not change: a revision is
  announced by `revision_created`, a write by `external_operation {kind, state}`, the end by
  `completed {revision_id}`; the payload models allow the two new operation kinds.

### 2. Revisions and the current revision

**A revision is immutable and identified by its content.** `class_revisions` rows are created only
by a job's final fenced transaction (never by a browser request), cannot be updated by any role
(`revisions_immutable`), and are deleted only with their class or account. `content` is a validated
`WorkoutDraft` fitted to the music, `content_hash` the SHA-256 of its canonical JSON
(`classbeat.design.revision.canonical`), `music` the reference of ADR 0006 §6 (`link_id`,
`fingerprint`, `unknown_bpm`). Two additions to the unchanged column set:

- **`parent_id`** is now always set except for the class's first revision: the revision a revise
  job started from. The revisions of a class form a tree; `number` is the creation order and never
  reused.
- **Provenance** (`classbeat.agent.records.Provenance`, still `extra="forbid"`) gains `origin`
  (`design_draft` or `revise_draft`), `base_revision_id` (the parent, `null` for a design) and
  `request_message_id` (the user's `class_messages` row that asked for it, `null` for a design),
  next to the existing provider, model, effort, `prompt_version`, versions, `agent_run_ids`, usage
  and cost. Old revisions validate with the new fields absent.

**Which revision is current.** `classes.current_revision_id` is the revision the page shows, a
revise job starts from, and Save and Update default to. It is set:

1. by the job that creates a revision, in the same fenced transaction, **by compare-and-set**: to
   the new revision if the column is NULL (the first design) or equals the job's `base_revision_id`
   (a revise), otherwise unchanged (the new revision is kept but not current). A new revision is
   thereby accepted by default; the user can go back at any time;
2. by the user: `POST /api/classes/{id}/current {revision_id, content_hash}` (CSRF, no recent
   authentication, it is the user's own work). One transaction: `SELECT … FOR UPDATE` on the class,
   refuse with 409 `class_busy` while the class has an open job (so a pick can't race a store),
   409 `hash_mismatch` if the hash isn't the revision's, 404 if the revision isn't one of the
   class's own (looked up with `owner_id` and `class_id`), otherwise set the column. Picking the
   current revision again is a 200 and writes nothing. A pick creates **no** revision: the next
   revise job's `parent_id` simply points at the picked one.

Revisions are never edited in place and never "restored" by copying; the tree plus the pointer is
the whole model. Only `kind = 'draft'` revisions can be current, revised or saved (`song_list`
revisions are M4's and approved, not saved).

### 3. Revising by chat

**Kind `revise_draft`** (issue R1; `classbeat.design.revise`, registered with the other kinds in
both processes). Input: `{class_id, base_revision_id, request, mowl_connection_id}` (`JobInput`
with `CONNECTIONS = ("mowl",)`), `request` 1 to 2000 characters (the brief text's limit, control
characters stripped). It uses the existing `POST /api/jobs` with its `Idempotency-Key`, per-user
limit and one-open-job-per-class rule; two kind seams are added to `classbeat.jobs` (issue K1):

- `JobKind.consumes_credit` (default true): the three new kinds are false, so they create the job
  with `credit_state = 'none'`, never call the ledger and are ignored by settle. A class is one
  credit however often it is revised or saved (roadmap §9 meters classes; revisions are limited
  per class, §4).
- `JobKind.admit(db, ctx, input)`, run inside the create transaction after the owner lock and the
  idempotency lookup. It may add server-resolved fields to the stored input and refuse with a
  reason (409 `{reason}`); it creates nothing when it refuses.

`revise_draft.admit` refuses `stale_base` unless `base_revision_id` is the class's current revision
(the browser sends what it shows; the server never guesses), `revision_limit` and `chat_full`
(§4), `invalid_input` for a base that isn't a `draft` of the class, and otherwise stores
`base_revision_hash` and inserts the user's `class_messages` row (`seq` next, `base_revision_id`,
`job_id` null until the job id exists, set in the same transaction) with the job.

**Pipeline** (stages `music`, `revise`):

1. **Music**: the class's `music_source` through the existing D1 step (the link, or the attach as
   its external operation, then the read-only analysis). If the music's fingerprint differs from
   the base revision's (the playlist changed, or the user reconnected MOWL and the link was
   re-made), the job continues and tells the model so as a fact; the new revision records the new
   fingerprint and the page says the music changed.
2. **Stage `revise`** (own system prompt, so its own `prompt_version`; tools `get_music`,
   `preview_draft`, `submit_draft`, no write tool, ADR 0006 §3). `submit_draft` gains an optional
   `note` (≤ 300 characters, control characters stripped) in which the model says what it changed.
   **Model input** (`classbeat.agent.model_input`, the one allowlisted builder, ADR 0006 §7): the
   class's `brief`, the music summary, the **base draft** (`content`), and **the user's requests
   along the base revision's `parent_id` chain**, oldest first, at most
   `CLASSBEAT_CHAT_CONTEXT_REQUESTS` (default 10), then the new request. Nothing else: **no earlier
   model text, note or transcript ever goes back to the model.** The planted-value test of ADR 0006
   §7 is extended to every new field.
3. **Store**, in the final fenced transaction (`Complete`): the connection guard once more
   (`MusicStep.still_current`); the new revision (`parent_id` = base, `origin = revise_draft`,
   content re-validated, same hash rules as a design; one whose `content_hash` equals the base's is
   refused as `invalid_output`: a revision that changes nothing is not stored); the assistant
   `class_messages` row; the compare-and-set of §2; `revision_created`; `completed`. A failed,
   cancelled or expired job stores no revision and no assistant message; its user message stays,
   and the page shows it with the job's failure.

**The stored conversation** is `class_messages`: the user's requests, and per answer the **change
summary** and the optional note.

- The **change summary** is computed by the backend from the two drafts (`classbeat.design.diff`):
  segments added and removed, intervals changed, added and removed (compared by position and
  fields), duration and TSS before and after (`workout.tss`, never a model number). It is data, not
  text. The page renders it in its own words.
- The **note** is the model's one sentence, shown to the user as plain text (never as HTML or
  markdown) and **never read back**: it is untrusted output (T8, T56) and storing it is for the
  user's benefit only. Model text that was prompt-injected through a song title therefore can't
  persist into a later request.
- Limits and retention: ≤ `CLASSBEAT_CHAT_MAX_REQUESTS` user requests per class (§4), each ≤ 2000
  characters, notes ≤ 300; the rows are deleted with the class or account (§11) and are never in
  events, logs or SSE (ADR 0005 §6). The conversation lives in `class_messages` and the revision
  tree, not in a transcript: tool calls, tool results and model turns are still not stored
  (ADR 0006 §6).

Cancel, retry, lease loss, shutdown and the cost ceiling are ADR 0005 §3 to §4 and ADR 0006 §5
unchanged; a retried attempt starts the stage from its beginning and the final store is the only
write, so a retry never leaves a half revision.

### 4. Limits and settings

| Setting | Default | Meaning |
|---|---|---|
| `CLASSBEAT_REVISIONS_PER_CLASS` | 25 (1 to 200) | the most **chat revisions** (`origin = revise_draft`) a class may have; the first design is not counted |
| `CLASSBEAT_CHAT_MAX_REQUESTS` | 50 (1 to 200) | the most user requests stored per class |
| `CLASSBEAT_CHAT_CONTEXT_REQUESTS` | 10 (1 to 30) | requests along the parent chain sent to the model |

**The per-tier limit** is a seam, as the credit seam of ADR 0005 §5: `classbeat.design.limits.RevisionPolicy`
(`limit_for(db, ctx, class_id) -> int`). M3b ships `SettingRevisionPolicy` returning
`CLASSBEAT_REVISIONS_PER_CLASS` for everyone, which also bounds storage (25 × 64 KB per class).
Roadmap §9's tier figures (Free 3, Instructor 10) are **M5's**: it replaces the policy with one that
reads the user's entitlements and the admin-editable tier table, and nothing in M3b changes. `admit`
counts revisions inside the create transaction under the owner lock, so two racing requests can't
both pass the last slot. The refusal is 409 `revision_limit` with the limit and the count in the
body so the page can say "3 of 3".

### 5. Approvals and revalidation (roadmap §2)

Model output is a proposal and the model has no write tool; the writes below are the roadmap's
**user write** ("save or update the MOWL workout: backend, only after the user's approval").

- **The approval is the user's click on Save or Update for one revision**, carried as
  `{class_id, revision_id, content_hash}` (and, for Update, `saved_workout_id` and `overwrite`) in
  `POST /api/jobs` with `kind = save_workout` or `update_workout`. It needs a signed-in session, a
  CSRF token and an `Idempotency-Key`; a second tab, a replay or a double click returns the first
  job. Recent authentication is not required (it is the user's own work, as for ADR 0005's Approve).
  The browser never sends a workout body, a program id, a playlist id or a name: it names rows by
  id and hash, and everything written comes from the stored revision.
- **Revalidation happens at three points**, each against the authenticated user and that exact
  revision, and each failing closed:
  1. **At creation** (`admit`, inside the create transaction): the class and the revision are the
     user's own (looked up with `owner_id`; a foreign or unknown id is a plain `invalid_input`/404,
     never a different message); the revision belongs to the class, is a `draft`, and its stored
     `content_hash` equals the sent one (`hash_mismatch`) and equals the SHA-256 of its canonical
     content recomputed now; `workout.validate(WorkoutDraft(...).to_spec())` passes; the class has
     no open job (409 `class_busy`, from the unique index); `mowl_connection_id` equals
     `ctx.connection("mowl")` and `classbeat.connections` reports it `connected`
     (`not_connected`); the revision's `music.link_id` names a live `mowl_playlist_links` row of the
     user (`music_unlinked`: the account changed since the revision was designed, §9); for Update the
     `saved_workout_id` is the user's live (`detached_at IS NULL`) row of the same class
     (`invalid_input`). Nothing is stored on a refusal.
  2. **In the worker** (stage `prepare`, a fenced transaction): the same checks again from the
     database, because time has passed (the class may have been deleted, the connection
     reconnected, the saved row detached). The spec is rebuilt from the stored `content` and
     validated again; the job's stored input is the approval record and is read only after the
     fence. Deletion of the class cascades the job away, so the fence fails on the next write
     (T53).
  3. **In the operation's decide transaction** (ADR 0005 §7 step 1): the guard re-reads the link
     row and the saved row under the share lock `classbeat.connections.hold` takes on the MOWL
     connection row, and compares the playlist id and program id to the ones the call will use. A
     reconnect or disconnect in between fails the job `not_connected` before `pending` is written
     (ADR 0006 §3, #117 item 1, which M3b's saves honour as that note asks).
- **What is written is only what was approved.** `workout.save` takes the spec, the playlist and
  program ids as arguments built in the worker from the revision and the rows above. The request
  hash of the operation (§6, §7) covers the content hash and the target, so a retry that would
  write something else is a hash mismatch and `internal`, never a write.
- Both kinds: `consumes_credit = False`, no model call, no `agent_runs`, no cost.

### 6. Save: a new private workout

**Kind `save_workout`** (issue SV1), stages `prepare`, `save`, `verify`.

1. **`prepare`**: the revalidation of §5.2, which also includes the **unresolved-write rule**: a Save
   (and an Update, §7) is refused with 409 `write_unresolved` while any `mowl_save_workout` or
   `mowl_update_workout` row of the same class that belongs to **another job** is `pending` or
   `uncertain` (at creation in `admit`, and again here); the user resolves it with Check again or
   Do it anyway (§8), after which it is `succeeded` or `failed` and the rule no longer applies. Without
   the rule a save that may have happened would be followed by a second one with no warning. The
   playlist id is read from the revision's link row;
   `hold` is taken on the opened connection in that transaction. Then a **preflight read**, outside
   any operation: `api.list_workouts(creds, page=0)` through `call_opened`, whose result (the
   ProgramIDs of the first page, at most 50) is the **baseline**. A preflight that fails is an
   ordinary failure with the usual retry classes (`upstream_unavailable`, `rate_limited`,
   `needs_reconnect`): no `external_operations` row exists yet, nothing was written, and a MOWL
   that is down is found here rather than inside the write. This is why a transient MOWL outage
   rarely becomes an uncertain write.
2. **`save`**: `perform` with kind `mowl_save_workout`:
   - **key** `<job_id>:save` (ADR 0005 §7: a job's own step); **request hash** over `{kind,
     content_hash (hex), playlist_id}`; **`class_id`** set; **`context`** `{revision_id,
     content_hash, playlist_id, baseline: [program ids], bound_seconds}`;
   - **the call**: `workout.save(spec, creds, playlist_id)` in a thread through `call_opened` with
     **`single_request = False`**. The library makes many requests, so after a first request
     `ConnectionFailure.not_applied` is not trusted for it (only the `NEVER_SENT` categories,
     `key_unavailable`, `decrypt_failed` and `not_connected`, still say nothing was sent, and for
     those `failed` is correct: the call never started); an allowlist refusal after the first
     request is `uncertain` (`OperationRequest.single_request`), and a MowlError after a first
     request is `uncertain` too. **Every failure of the write itself
     is `uncertain`; `failed` comes only from a reconciliation that proves no effect (§8).** An
     answer without an integer `program_id` is `UncertainAnswer`, also `uncertain`;
   - **the lease**: the `pending` transaction extends it by `bound_seconds = 30 s × (3 + 3 ×
     segments)` capped at 30 minutes; the heartbeat
     keeps extending it; a cancel never interrupts the call between `pending` and its outcome
     (ADR 0005 §4). **The bound assumes a total deadline of 30 s per request.** The library's
     `urlopen(timeout=30)` is only a socket timeout per blocking operation, so a slow-drip response
     could outlast it. SV1 therefore enforces the total deadline in the request function the call
     goes through (the connection's egress-checked transport), tested with a slow-drip fake. A
     per-request socket timeout is not a bound, so if SV1 cannot enforce the total deadline it is
     blocked rather than given a larger figure. `call_may_be_running` (§8) rests on this;
   - **the effect**: MOWL's `ProgramID` becomes the operation's `external_id` and `result`
     `{segments: n}`. In the same transaction as the `succeeded` row, `on_succeeded` inserts the
     `saved_workouts` row (`ON CONFLICT DO NOTHING` on the live-program index), so whoever marks the
     write `succeeded` (the worker, the reconciliation, the user's **Check again**) also records the
     save, exactly as the attach records its link.
3. **`verify`** (best effort, after `succeeded`): `api.get_workout` for the new program, normalized
   (§7) and compared with the revision. Equal: `saved_workouts.state_hash` is set. A mismatch, or a
   read that fails, leaves `state_hash` NULL (the page says "saved, not checked") and the job still
   ends `completed`: the write is final, `succeeded` rows are never undone.

Saving is **not idempotent across jobs by design**: each Save is a new workout, as roadmap §6 says,
so a user who wants a variation can save two revisions of one class. The page shows the existing
saved rows and offers Update first; a Save with an existing row needs no extra confirmation but says
"a new workout will be added". A browser replay is the same job (`Idempotency-Key`); a retried or
reaped job reuses its operation by key and never saves twice.

### 7. Update saved: only the user's own program, read first

**Kind `update_workout`** (issue SV2), stages `prepare`, `read`, `save`, `verify`.

- **Which program.** The input names `saved_workout_id`, never a program id. `prepare` and the
  decide guard read the program id from the user's own live `saved_workouts` row (same class, same
  owner, `detached_at IS NULL`) inside the fenced transaction. A program id from the browser, from
  a model or from MOWL's own listing is never written to. The operation key is `<job_id>:update`,
  the hash covers `{kind, program_id, content_hash, playlist_id}` (the update writes the playlist
  id, as Save's hash does), and `on_succeeded` updates only a live
  `saved_workouts` row with that program id.
- **Baseline** (in `prepare`, as for Save, §6): `api.list_workouts(creds, page=0)` through
  `call_opened`, outside any operation; its ProgramIDs are the **baseline** stored in the context. An
  update is not one request either and can create a program (below), so it needs the same listing
  evidence as Save. A failing preflight is an ordinary failure with no operation row.
- **Read first** (`read`, outside any operation): `api.get_workout(program_id)`. It must exist
  (`program_missing` otherwise: deleted in MOWL, or the id is not served to this account), and its
  **normalized state** must match what classbeat last wrote:
  - `normalized(program)` (one function, `classbeat.mowl_state`) is the canonical JSON of the
    fields the library writes and `workout.summarize` returns: name, description, per segment its
    kind, name and total, per interval `sec`, `zone`, position, cycle, rpm range, game and FTP
    test, the playlist id, and `public`. Ids, TSS rounding and timestamps are not in it. It is the
    **same function** the reconciler uses (§8), and `matches(spec, program)` compares an intended
    spec to a read program through it.
  - If `public` is true, or `sha256(normalized)` differs from `saved_workouts.state_hash` (or the
    hash is NULL), the user changed the workout in MOWL after classbeat wrote it, or classbeat could
    not check it: the job ends `program_changed`, **nothing is written**, and the page offers
    "Update anyway (replaces your changes in MOWL)". That is a new Update request with
    `overwrite = true`, which skips only this comparison; every other check stays. The library
    writes `IsPublic: false`, so a public program is always "changed". `overwrite` is sent only by
    the page's confirmation, never inferred by the server, and **Do it anyway** on an uncertain
    update leaves it to the page to ask (§8).
  - The `normalized` hash before the write is the operation's `before_hash` (context), next to the
    `baseline` and `bound_seconds`.
- **The write**: `workout.save(spec, creds, playlist_id, program_id=program_id)`, again
  `single_request = False` and every failure `uncertain`. A MowlError saying MOWL answered with
  another program id is `uncertain` as any other: MOWL then made a **new, empty program** (the
  library stops before writing segments into it) while the original stays untouched. The original
  therefore still equals `before_hash`, and a read of the target alone can **not** show that the
  request had no effect. That is why the reconciler (§8) also lists My Workouts against the
  baseline and treats **any new program as inconclusive**: such a row is `uncertain`, is never
  retried automatically, and so cannot pile up stray workouts, one per attempt. The page's uncertain
  text for updates mentions that a stray workout of that name may exist in My Workouts; it is the
  user's own private workout, nothing in classbeat points at it, and the user deletes it in MOWL.
- **`verify`** as in §6: `state_hash` set from the read-back, or NULL.

### 8. Uncertain saves and updates: what a read proves

A multi-request write that fails or whose outcome is not recorded is `uncertain`, and **no job
sends it again** (ADR 0005 §7 step 4; T51). The one automatic reconciliation runs the kind's
`Reconciler` in the worker; the same code runs in the web process for the user's **Check again**.
It is read-only, with the user's credentials opened through `classbeat.connections`, and bounded
(one listing and at most three program reads). **The listing is covered** when it has fewer than 50
programs or contains at least one program of the baseline (it is newest first); a full page of only
new programs, or a listing that fails, is not covered. Only a covered listing can support `failed`;
a target that would sit off page 0 is therefore inconclusive. It needs the data the write left behind, which is
why `request_context` is stored at the `pending` insert and handed to the reconciler and to
`on_succeeded` (the shared protocol change of issue X2: `OperationRequest.context`,
`Operation.context`).

**`mowl_save_workout`.** From the listing now (`list_workouts`, page 0) and the context's
`baseline`:

| What the read shows | Outcome |
|---|---|
| **exactly one** program that is both **not in the baseline** and private and whose `matches(spec, program)` holds (the revision's content, rebuilt from the stored revision). Only *new* programs count, and other new programs that do **not** match (something the user made in the MOWL app meanwhile) neither add to nor cancel this single match | `succeeded`, `external_id` = its ProgramID, `saved_workouts` written |
| no match, but one or more new programs that don't match (a **partial** save: the program exists, segments missing or different) | inconclusive: the effect may have happened, a retry would duplicate |
| two or more new programs that match | inconclusive (a duplicate already exists; the user decides) |
| no new program, listing covered | inconclusive, **unless** `CLASSBEAT_MOWL_LISTING_LAG_SECONDS` is set and `call_ended_at` is set and `now() ≥ call_ended_at + lag`, then `failed` |
| listing not covered (more than 50 new programs, or it failed) | inconclusive |

Absence is not proof: MOWL's read-after-write delay is unknown, and a wrongly-`failed` row leads to
a duplicate. The lag setting is **unset by default**; the owner measures how soon a new workout
appears in the listing at live activation (one authorized save on the owner's own account, see "Live
activation" below) and then sets it. Until then a save that may not have happened waits for the user,
which is the roadmap's rule ("not retried automatically while a duplicate can't be ruled out").
Two assumptions here are **not yet checked against real MOWL** and are listed in "Live activation"
below: that a new workout appears on page 0 of `creationDateDesc`, and that `get_workout` shows the
fields `matches` compares.

**`mowl_update_workout`.** From `get_workout(program_id)`, the listing (`list_workouts`, page 0) and
the context's `before_hash` and `baseline`. The target read alone never proves "no effect", because
the request can create a *different* resource (§7), so `failed` also needs a covered listing with
**no new program** since the baseline:

| What the read shows | Outcome |
|---|---|
| `matches(spec, program)` holds | `succeeded` (a new program, if any, is a stray of the user's own; the page mentions it) |
| the target is unchanged (`sha256(normalized(program)) = before_hash`) **and** the listing is covered **and** has no program that is not in the baseline | `failed`: provably unchanged and nothing created (orphan segment copies the failed attempt made are not part of the program) |
| the target is unchanged but **any new program** appeared (or the listing is not covered or failed) | inconclusive: MOWL may have made a copy; a retry could make another, so the row stays `uncertain` and is never retried automatically |
| anything else (partial) | inconclusive |
| the program is gone | inconclusive (`program_missing` is shown) |

An update that is `failed` by proof is retried by the job as the protocol already does
(`NO_EFFECT_CATEGORY`); the retry's own `prepare` takes a fresh baseline and its `read` step
compares again, so it can't overwrite an edit the user made meanwhile. Every attempt of
`save_workout` and `update_workout` starts at `prepare`, never at `save` (as `design/kind.py`'s
`run` restarts from the top), so the read-first check (T59) runs on each retry. A retry therefore only
follows a proof that nothing, neither a change nor a new program, resulted from the last attempt.

**The user's two actions** (`classbeat.operations.user`, kind-agnostic, CSRF) behave as ADR 0005
§7, plus a rule for the multi-request kinds. **Check again** runs the reconciler once more.
**Do it anyway** marks the row `failed`, `resolved_by = 'user'`, and the page then offers the same
action as a new job (a new key, a new operation row), after a warning that depends on the kind:
for a Save "this may add a second workout to your MOWL account" (the user deletes extras in the
MOWL app); for an Update, "your MOWL copy may be partly updated; the update replaces it" and it
sends `overwrite = true`, since a partial state can't equal `state_hash`. Neither action applies to
a `succeeded` row (409 `final`).

**A stale call may still be running.** A `pending` row left by a lost lease (the process is alive
but fenced out, or slow) is marked `uncertain` by the next job without `call_ended_at`; its thread
can still be sending requests for up to `bound_seconds`. For such rows both actions are refused
with 409 `call_may_be_running` until `pending_at + bound_seconds + 30 s` (`actionable_at` in the
view), so a repair can't interleave with the old call's late requests. A row whose call returned
(`call_ended_at >= pending_at`) is actionable at once. A row left by a crashed worker process is
treated the same way: the bound is the only evidence that the call is over. **The automatic
reconciliation waits for `actionable_at` too** (ADR 0005 §7 step 4): when the next attempt finds a
stale `pending` row of a multi-request kind without `call_ended_at` and `now() < actionable_at`, it
marks it `uncertain`, does not reconcile and does not call, and the job ends `uncertain_write`; the
user's Check again after `actionable_at` does the reconciliation. An update therefore can't be
retried while the old thread may still write.

**An orphaned `pending` row.** Operation keys are per job, so the only job that can meet a `pending`
row is the same job's next attempt. When `queue_reap` ends the job instead (`failed` /
`lease_lost` once the attempts are spent, or `cancelled` when a cancel was requested), or the job
expires or is deleted, nobody would ever resolve the row: both user actions answer 409
`not_uncertain`, and a later Save would add a second workout (#117 item 7 notes the hidden-pending
gap). It is closed in two parts, without touching the queue functions:

1. **Who resolves it.** `classbeat.operations.orphans.resolve(db, ctx)` (a new module, added to
   the session-guard allowlist; on the worker path it writes through the job's fenced session) marks every `pending`
   `mowl_save_workout` / `mowl_update_workout` row of the user whose job is terminal (`failed`,
   `cancelled`, `expired`, `completed`) or gone `uncertain`, under the same conditional update
   (`WHERE state = 'pending'`) and the job's error category when it has one. It runs under the
   user's own row security at the start of every route that reads or acts on a class's operations
   (the class and operation views, Save and Update admit, class delete, Check again, Do it anyway)
   and in a worker's `prepare` for the owner. The user then sees the row as an ordinary uncertain
   write, with `actionable_at` still applying (it has no `call_ended_at`). A late outcome write from
   the fenced-out holder fails on the lease, so it can't overwrite the marking.
2. **Save and Update refuse meanwhile** (the unresolved-write rule of §6): a Save or an Update is
   refused with 409 `write_unresolved` while any `mowl_save_workout` or `mowl_update_workout` row
   of the same class that belongs to **another job** is `pending` or `uncertain`. The user
   resolves the row first, and only then can another write be started.

Tests: crash on the last attempt (the job ends `failed` `lease_lost`, the row becomes `uncertain`,
Save is refused, Check again after `actionable_at` resolves it); cancel requested, then lease
expiry (the job `cancelled`, the same); a Save during the window before `resolve` ran is refused as
`write_unresolved` too.

**The tested forms in the end-to-end suite (T2, `e2e/tests/class-save.spec.ts`).** Issue #151
described two of the uncertain cases more literally than the fakes can show them; these forms are
accepted as the tests of those cases:

- *A save whose answer is lost* (and *a second workout only when the user decides*): the fake MOWL
  applies every write but drops the answer to the **last** write (`save-arm?fail=<n>:drop`), and
  `save-listing?hidden=1` hides the new program from My Workouts, so the reconciler sees no new
  program and cannot prove "no effect". Check again, once the listing shows the program, finds it.
- *A partly done update*: the revision between the two versions changes only the **description**
  (the `revise_description` script), and the drop hits the write after the program header and the
  first segment (`5:drop`), so the program holds the new description but the old segment list.
  Check again cannot tell it from an untouched one and the row stays `uncertain`; Do it anyway
  leads to an Update with `overwrite`.

### 9. Reconnecting MOWL, and another account

`mowl_save_workout` and `mowl_update_workout` operations and `saved_workouts` rows are classbeat's
local data about **that MOWL account**, like the playlist links and attach operations (ADR 0006 §3).
The connection row records no account identity, so a disconnect, a reconnect in place, `keys wipe`
and an account switch are indistinguishable. In the same transaction as today's link deletion
(`_forget_mowl_account`, issue X2):

- every save and update operation of the user is **deleted** (any state; an undecided one is gone,
  as an undecided attach is, and the dialogs for reconnect and disconnect say so); and
- every `saved_workouts` row is **detached**: `detached_at = now()`. The row stays as history (the
  page shows "saved earlier to a previous MOWL connection"), can't be updated, and keeps no live
  program id in the unique index. A later reconnect never re-attaches it automatically: classbeat
  can't tell whether the account is the same one.

Consequences, each tested: an Update of a detached row is refused (`invalid_input`); a Save or
revise needing the music link finds none and says so (`music_unlinked` for Save; a revise re-attaches
through `music_source` and makes a new link and a new revision, which can then be saved into the new
account); a job whose call was in flight at the reconnect ends `not_connected` when its outcome
can't be recorded (the operation row is gone), writes no saved row and is not retried, and the call
already made went to the account whose token it opened. Save never writes a program into an account
other than the one whose link it read: the link, the `hold` and the credentials come from one
`wrapped_dek` generation, and a reconnect deletes the link. **Save does not re-attach music or
re-analyse**; saving an old revision after an account change is refused rather than writing timings
fitted to music the new account may not have.

**Amendment (2026-10-31, ADR 0009 §5.4, accepted in #198):** "one `wrapped_dek` generation" now
reads one `generation`: the link, the `hold` and the credentials come from one value of
`connections.generation`, compared together with `id` (a rewrap no longer changes it).

If a later MOWL read exposes a stable account identity, comparing it would let a same-account
reconnect keep its rows; that is a possible improvement, not assumed.

### 10. Usage and cost

**Stored durably.** `agent_runs` (ADR 0006 §6) already has one row per stage run with tokens by
kind and `cost_micros`, updated after every turn (a crashed run keeps what it recorded). M3b makes
it the durable usage record instead of adding a copy:

- `job_id` is nullable and `SET NULL`s when the job goes with its class; the restrictive policies of
  §1 stop any role from changing a finished row or deleting a row (only the account's deletion
  cascades). Usage therefore survives deleting a class, as a monthly view and M5's accounting need.
- New columns: `job_kind` (the job's kind, so a deleted job is still attributable) and
  `price_version` (a dated constant `classbeat.agent.prices.PRICES_VERSION`; editing the price table
  bumps it, and a stored row keeps the version it was priced with). No history is re-priced.
- **Per run** is the row itself; **per job** is the sum over `job_id`. `GET /api/jobs/{id}` gains
  `usage` (tokens and `cost_micros`, summed over its runs and attempts). A job that never called a
  model (a save) has none.

**The monthly per-user view.** `GET /api/usage?month=YYYY-MM` (signed in; default the current UTC
month; 404 for a month before the account): sums by `started_at` month in UTC, for the user's own
rows only, per job kind and per model: runs, distinct jobs, the four token counts, `cost_micros`;
plus classes designed (distinct completed `design_draft` jobs, which M5 turns into the quota
figure), revisions made, saves and updates completed. The account page ("plan and usage", roadmap
§8) renders it as an **estimate in US dollars at list prices**.

**What the figure leaves out is stated, in the body and on the page**, as `notes` codes the page
words: `estimate_at_list_prices` (not the provider's invoice); `sdk_retries` (the SDK's silent
retries inside a turn, ADR 0006 §6: at most two uncounted attempts per turn, three for the turn that
ends a failed attempt, each at most the conversation's input plus `max_tokens`; only the attempt
that answers reports usage); `lost_lease_turn` (a turn that finished after its worker lost the lease
can't be recorded, at most one per lost lease); `failed_stream_turn` (a request that failed after
the provider began billing it returns no usage). The bound is a ceiling per event, not an estimate;
the page says "may be slightly lower than your provider's bill". `max_retries=2` stays (ADR 0006 §2).

**Real cost per class is measured at live activation.** M3b records what each job costs on fakes
and at the price table; it claims nothing about real cost. At the owner's authorized live run
(`docs/status.md`, "Live activation log") the owner designs and revises one class on their own
account, then compares the sum of `agent_runs.cost_micros` for those jobs with the provider
console's usage for the same window; the ratio of our sum to the console's and the cost per
design and per revise are recorded there, and ADR 0006 §5's defaults and `PRICES_VERSION` are set from them
before any other tester. The same run measures the MOWL listing lag of §8 and checks `matches`
against a real program (one save and one update on the owner's account, authorized for that run).
The first live smoke ($0.08 to $0.30 per draft, $2.59 for 15 cases) is the only real-cost figure
today. The admin cost view (all users) is M5's, through `classbeat_maint` with the audit log.

### 11. Retention (#71)

Amendment 1 and ADR 0010: a user's own Spotify-derived track lists and timing go with their class or
account, and a shared analysis goes once no class of any user refers to it; nothing Spotify-derived
is kept indefinitely. #71 asks what that means for M3b, in four parts.

**Where things stand.** classbeat stores **no shared music analysis** today (ADR 0006 §2: the
analysis lives in memory for the run), and **M3b adds none** (§3's music step analyses again for
every revise). So M3b has no cross-user reference count to make and builds no purge job; the
roadmap's "a periodic purge, M3b" is moved to the first milestone that stores a shared analysis
([roadmap Amendment 3](../roadmap.md)). Every
Spotify-derived byte classbeat keeps is in an owned row that a class or the account owns. What M3b
stores, and how each goes:

| Data | Where (after M3b) | Deleted with |
|---|---|---|
| Track titles of tracks MOWL had no BPM for | `class_revisions.music.unknown_bpm` | the class (cascade) or the account |
| Fingerprint (SHA-256 of track ids and lengths) | `class_revisions.music.fingerprint` | the class |
| Song titles the model or user wrote into segment names, descriptions, cues | `class_revisions.content`, `class_messages.body` and notes, `saved_workouts.name` | the class |
| The user's brief, the Spotify playlist id | `classes.brief`, `classes.music_source`, `jobs.input` | the class |
| Counts, enums, own ids (no titles, no model text: ADR 0005 §6) | `job_events` | the job (cascade) |
| MOWL program/playlist ids, ProgramIDs of the listing, hashes | `external_operations` (`external_id`, `request_context`) | the class for save/update kinds (`class_id` cascade); the account; MOWL disconnect (§9); attach rows as ADR 0006 §3 |
| Spotify id to MOWL playlist id | `mowl_playlist_links` (ids only, no track data) | the MOWL disconnect or reconnect, the account |
| Token counts, cost, model | `agent_runs` (no provider content) | the account only (kept past a class on purpose, §10) |
| Nothing from the model's turns | not stored (ADR 0006 §6) | n/a |

`mowl_playlist_links` is **not** purged when no class refers to it: it holds two identifiers and no
metadata of the music, it is a cache of MOWL's own state for the user's own account, and it goes
with the connection. This is the decision #71 and ADR 0005 §1 left open; revisited at M6.

**The deletion paths are executable.** `DELETE /api/classes/{id}` (issue C2) removes the class and,
by cascade, everything in the first rows above; the account deletion (ADR 0003) removes all. A
**retention registry** (guard 10, issue C1: `classbeat.db.RETENTION`, one entry per table with a
provider-derived or user-content column and its deletion path) is checked by a test that fails when
a table or column is added without an entry, and a database test plants a row in every registered
table, deletes the class and then the user, and asserts nothing is left (except `agent_runs`, then
gone with the user). This makes "every new Spotify-derived column has a deletion path" a CI gate
rather than a reviewer's memory. Logs hold no titles (ADR 0004 §7, ADR 0005 §6).

**Cross-user reference count: when a shared analysis store is introduced** (M4 at the earliest, if
song picking caches pool analyses across users; roadmap §6), the store must come with, in its own
ADR and before its PR:

- the analysis table as a **system table** (no `owner_id`, `SYSTEM_TABLES`, ADR 0002 §3), keyed by
  source, fingerprint and `MAP_VERSION`, readable only through a narrow `SECURITY DEFINER` function
  that checks the requester's access first (roadmap §6);
- every owned row that uses an analysis records its id (`class_revisions.music.analysis_id`; a job
  in `jobs.analysis_id` for the time it runs), nothing else about the analysis;
- a purge function in the same style as the queue's (ADR 0005 §3): owned by a `NOLOGIN` role with
  `BYPASSRLS`, `EXECUTE` for `classbeat_app` only, `search_path = pg_catalog, pg_temp`, and **column**
  privileges only: `SELECT (music)` on `class_revisions`, `SELECT (analysis_id, state)` on `jobs`,
  `DELETE` on the analysis table. It deletes an analysis only when **no revision of any user and no
  open job** (`queued`, `running`, `waiting_for_approval`) refers to it and it is older than a grace
  period of at least the approval TTL plus an hour, `FOR UPDATE SKIP LOCKED`, in batches of at most
  100, and writes one audit row of counts (no ids). Under ADR 0002 §3 this is the narrow function the
  issue asks for; a broad bypass or `classbeat_maint` read of owned tables is not used;
- **in-flight work is protected twice**: the open-job reference and the grace period, so a job that
  has loaded an analysis but not yet stored its revision, or a list waiting seven days for approval,
  keeps it. A revision created after the check but referring to an analysis the purge is about to
  delete is prevented by the grace period and by the purge re-checking under the row lock on the
  analysis;
- the worker's maintenance loop runs it every 30 s with the other sweeps, one transaction per batch;
- **per-user beat-one corrections** (roadmap §6; not built in M3b) are rows of a class-owned table
  (`class_id` cascade) registered in guard 10, never shared;
- the shared store never keeps an analysis whose BPM is incomplete (`spotify.unknown_bpm`, ADR 0006
  §3), as already stated.

**Backups.** Deleted rows live on in backups. M6 sets the retention; this ADR fixes the requirement:
backups of the database are kept for a bounded, stated time (at most 35 days is the proposal); the
privacy text says so; a restore re-applies the deletions made since the backup (the class and
account deletions leave a log of ids only, kept as long as the backups) before the stack is opened
to users. Until M6 there are no backups of the owner's stack beyond the owner's own.

**#71's acceptance** ("ADR covering the above, before the M3b PR that stores shared analyses") is
met by this section: no M3b PR stores a shared analysis; the first one that does must follow the
last list above.

### 12. API, categories, events

Web (all signed in; CSRF on writes; 404 for foreign or unknown ids; no MOWL or Spotify id, key or
lease data in any body):

| Route | Does |
|---|---|
| `GET /api/classes`, `GET /api/classes/{id}` | my classes (`limit`, 1 to 500, default 500, and `cursor`; the body's `next_cursor` is the next page's `cursor`, `null` on the last; a bad cursor is 422 `invalid_cursor`); one class with its current revision number, saved summary, open job |
| `GET /api/classes/{id}/revisions`, `/revisions/{id}` | the tree (number, parent number, origin, created, summary numbers, cost) and one revision (content, music warnings, provenance without run ids) |
| `GET /api/classes/{id}/messages` | the conversation |
| `POST /api/classes/{id}/current` | §2 |
| `POST /api/jobs` with `revise_draft`, `save_workout`, `update_workout` | §3, §5 to §7 |
| `DELETE /api/classes/{id}` | §11; 409 `class_busy` with an open job; 409 `call_may_be_running` while a save or update row of the class is `pending` or still inside its bound (`now() < actionable_at`), because the cascade would drop the only record of a write that may still land; 409 `uncertain_write` unless `?confirm_uncertain=true`; says the MOWL workouts stay |
| `GET /api/usage`, `usage` in the job view | §10 |
| `/api/operations/...` | unchanged shape; `OperationView` gains `class_id`, `actionable_at` |

Reasons on 409 at creation: `stale_base`, `revision_limit`, `chat_full`, `hash_mismatch`,
`class_busy`, `write_unresolved` (§6, §8), `not_connected`, `music_unlinked`, `invalid_input`. New failure categories, with
the plain sentence the page shows: `program_changed` ("this workout was changed in MOWL after
classbeat saved it"), `program_missing` ("this workout is no longer in your MOWL account"),
`music_unlinked` ("the music of this version is not in your connected MOWL account; change it and
save the new version"). The three are not retryable. Backoff and retry classes are otherwise
ADR 0005 §4's.

### 13. Threat model and tests

New threats T58 to T65 go into [the threat model](../security/threat-model.md) with this ADR.

**Tests** (database tests against Postgres in CI, in-process fake MOWL, e2e fake MOWL; every one
named in an issue's criteria):

- **Isolation**: two-user RLS on each new table; 404 on every class, revision, message, usage and
  operation route for the other user's ids; a revise request or a pick naming another user's
  revision or class is refused; guard 1, 2 and 10.
- **Approval**: every refusal of §5.1 and §5.2 sends nothing to the fake (counted); a saved row of
  another user, a detached one, another class's, or an input carrying a program id can't be written
  to; a hash mismatch at the decide step is `internal` and writes nothing.
- **Write protocol**: failure injection at each request number of a save and an update: refuse
  before send and drop after send both `uncertain`, none `failed`; crash mid-call then the next job
  never calls; two jobs on one class never both call; the reconciliation tables of §8, each row;
  `call_may_be_running`; the lag setting off and on; an update whose copy attempt left a stray
  program ends `uncertain` and is never sent again (no second stray); an orphaned `pending` row
  (last attempt, cancel then lease expiry) becomes `uncertain` and blocks Save and class delete
  until resolved; a take-over of a `failed` row resets `call_ended_at`, `pending_at` and the
  context; a save with unrelated new programs still reconciles `succeeded`; more than 50 new
  programs is inconclusive; a slow-drip response does not outlast `bound_seconds`.
- **Account switch**: reconnect mid-call, between creation and run, and after saving: no stale
  link, `external_id`, operation or saved row is used; Update of a detached row refused; Save of an
  old revision `music_unlinked`; a revise re-attaches and the new revision saves into the new account.
- **Conversation**: the notes and model text never appear in model input (planted values, injected
  title in a note); the context follows the parent chain; limits; an unchanged draft isn't stored.
- **Usage**: the monthly view equals the sum of `agent_runs` for fake designs and revises across
  attempts and a crashed run; survives class deletion; excluded after account deletion; the price
  version is kept per run; finished rows can't be changed or deleted by `classbeat_app`.
- **Retention**: the registry test and the plant-and-delete test of §11.

## Alternatives

- **A `current` flag on revisions, or a mutable "working copy".** Rejected: revisions are immutable
  (ADR 0005 §1) and a flag would need updates; the pointer on the class is one compare-and-set and
  keeps the tree.
- **Feeding the full transcript (model text included) to the next revision.** Rejected: it makes
  prompt injection through a song title persistent and grows cost every turn. Requests along the
  parent chain plus the base draft are enough, and cheaper.
- **Storing the model's explanation as the assistant message and showing it as the answer.**
  Replaced by the deterministic change summary plus an optional, never-read-back note.
- **Saves outside the job system** (a web route calling MOWL). Rejected: the protocol (fence,
  `pending` before the call, uncertain handling, cancel and shutdown rules) exists for jobs; the web
  route would duplicate it without the lease.
- **One `save_workout` kind with a mode.** Two kinds keep each input, stage list and reconciler
  small and let the operation kinds have separate keys and tests.
- **A per-class operation key** (`class:<id>:save`, so a second Save finds the first). Rejected:
  Save is meant to create a new workout each time (roadmap §6), and a per-class key would turn the
  second Save into a reuse of the first result.
- **Reconciling a save by name only.** Rejected: a same-named program already in My Workouts would
  be mistaken for the effect. The baseline and the full content match avoid it.
- **Treating "nothing found" as proof of no effect.** Rejected until the listing lag is measured
  (§8); a duplicate in the user's MOWL account is worse than one extra click.
- **Adopting a partial program** (finding it and updating it in place instead of creating another).
  It would avoid the duplicate after a partial create. Deferred: it makes classbeat write to a
  program no row of ours points at, which contradicts §7's rule; the user can delete the incomplete
  one in the MOWL app. A change in `mowl-workouts` to report which step failed and the new program
  id would make it safe later.
- **A separate `usage_records` table.** `agent_runs` already has every column and is written in the
  fenced transactions; a copy would need its own sync and its own bugs. Making it durable and
  immutable with two policies and one nullable column is smaller.
- **Counting revisions against the class credit.** Rejected by roadmap §9: classes are metered,
  revisions limited per class.
- **A purge job in M3b.** Nothing shared is stored, so there is nothing for it to purge; the design
  for when something is is §11.
- **Re-attaching the music inside Save.** Rejected (§9): it would write fitted timings against
  music nobody re-analysed.

## Consequences

- M3b is eleven issues (§14); five need an Opus Developer (migration and RLS, job-kind seams,
  operation protocol, the two MOWL write kinds), every PR gets a fresh Reviewer (Opus except the
  frontend and E2E).
- The M3a protocol changes only additively (`context`, `class_id`, two timestamps, an `OnSucceeded`
  and `Reconciler` input that carries the operation); its tests for the attach must pass unchanged.
- `external_operations` rows of the two kinds go with their class, so deleting a class while a
  write is undecided drops the user's record of it; the delete route refuses while a write is
  `pending` or inside its bound and asks for confirmation when one is `uncertain`.
- A save or update that fails after MOWL received a request is **always** shown to the user until a
  read proves its fate; the user will see this rarely (the preflight removes the common transient
  failures) and the page must make the two actions understandable (issue FE2).
- Update's change detection depends on `workout.summarize` and `get_workout` returning what the
  library wrote; if MOWL normalizes differently, every update first reports `program_changed` until
  `matches` and `normalized` are fixed. The overwrite path keeps the feature usable meanwhile.
- Usage numbers are estimates with stated gaps; the first real figure per class comes from the
  owner's live run.
- The roadmap needs a dated amendment (by the Architect on acceptance): the purge moves from M3b to
  the first shared analysis store (§11).
- Threat model T58 to T65.

### Live activation (not an M3b task)

At the owner's authorization for that run (AGENTS.md safety envelope), on the owner's own MOWL
account, one design, one revise, one save and one update, and read-only checks: that a new workout
appears in page 0 of `creationDateDesc` and how fast (sets `CLASSBEAT_MOWL_LISTING_LAG_SECONDS`);
that `get_workout` returns the fields `matches` and `normalized` use and that a read-back equals the
intended spec; what MOWL does with an update PUT; the real cost per class (§10). Results go in
`docs/status.md`'s Live activation log.

## M3b issue breakdown

One issue per PR, milestone "M3b Class page and save". "Opus" marks a security-critical issue with an
Opus Developer (AGENTS.md: external writes, approvals, RLS and migrations); the others have a
Sonnet Developer. Each issue is not startable before this ADR is accepted. Fakes only.

| # | Issue | Owner | Depends on |
|---|---|---|---|
| C1 (#141) | Migration `0005_classes`: `classes` columns, `class_messages`, `saved_workouts`, `external_operations` columns and kinds, the three categories, durable immutable `agent_runs` (columns `job_kind`, `price_version`, the two restrictive policies with `WITH CHECK (true)`, `job_kind` backfilled before the policy), guard 10 (retention registry); RLS, cascade and immutability tests | **Opus** | none |
| K1 (#142) | Job-kind seams: `consumes_credit`, create-time `admit`, `design_draft` sets the class pointers, provenance fields | **Opus** | C1 |
| X2 (#143) | Operation protocol: `class_id`, `request_context`, `pending_at`, `call_ended_at` (reset on every return to `pending`), orphan resolution, the unresolved-write rule, `Operation`-carrying `Reconciler` and `OnSucceeded`, `call_may_be_running`, disconnect and reconnect deleting the write operations and detaching saved rows | **Opus** | C1 |
| C2 (#144) | Class and revision API, current-revision pick, class delete with the pending/bound refusal and the uncertain-write confirmation, 404 tests | Sonnet | C1 |
| US1 (#145) | Usage: writes `job_kind` and `price_version` (columns added by C1) when it inserts `agent_runs` rows, adds `classbeat.usage` to the session-guard allowlist, `GET /api/usage`, per-job usage in the job view, `notes` codes | Sonnet | C1 |
| R1 (#146) | `revise_draft`: admit with limits and stale base, `revise` stage, model input with the parent chain, change summary and note, store with compare-and-set, settings and `RevisionPolicy`, fake scripts and three smoke cases | Sonnet | C1, K1 |
| SV1 (#147) | `save_workout`: approval revalidation, preflight baseline, `mowl_save_workout` with reconciler and `saved_workouts`, verify, fake MOWL programs with failure injection | **Opus** | K1, X2 |
| SV2 (#148) | `update_workout`: saved-row-only target, preflight baseline, read first, `program_changed` and overwrite, `mowl_update_workout` with `before_hash` and baseline reconciler | **Opus** | SV1 |
| FE1 (#149) | Class page, revisions panel, Change it chat, My classes | Sonnet | C2, R1 |
| FE2 (#150) | Save and Update UI, uncertain panels, overwrite dialog, usage panel | Sonnet | SV2, US1, FE1 |
| T2 (#151) | E2E with fakes: design, revise, save, update; uncertain save and update; account switch; usage recorded per job (the uncertain cases in the forms recorded in §8) | Sonnet | FE2 |

Order: C1; then K1, X2, C2 and US1 in parallel; then R1 and SV1; SV2; FE1; FE2; T2 last. M3b is done
when T2 and the crash and failure-injection tests of SV1 and SV2 are green (roadmap M3b "done when":
design, revise, save, update with fakes; the uncertain-write path; token usage and cost recorded per
job).

## Amendments after implementation (2026-10-30)

- **Brief size (K1, #158):** a brief can be 2000 characters but the `classes.brief` column is
  limited to 4096 bytes, which multi-byte text can exceed. Job creation refuses such a brief
  with 422 (no echo of the text) instead of failing in the final store after the model has run.
- **Usage before the account existed (US1, #159):** `GET /api/usage?month=` answers zeros for a
  month before the account was created, not 404: the 404 would need `users.created_at`, which the
  auth-tables guard reserves for `classbeat.auth`. A malformed month is 422 `invalid_month`.
- **Classes designed and revisions** count only completed jobs; for deleted jobs a `submitted`
  run stands in, so a deleted job with several submitted attempts is over-counted (documented in
  `usage/service.py`).
- **The class API's saved summary (#167, hardening batch):** `saved` carries `count` (live rows),
  `latest_revision_number`, `latest_saved_at`, `latest_up_to_date`, `latest_saved_workout_id` (the
  classbeat id, never a MOWL id, of the newest live `saved_workouts` row, the one Update names),
  `detached_count` (rows detached by a MOWL disconnect, a reconnect or `keys wipe`, section 9) and
  `saved_state`: `none` (no live row), `ok`, or `inconsistent` (a live row exists but its creating
  operation belongs to another class, so no id is offered because Update would refuse it; the page
  says the saved state needs attention).
- **Bound clamp:** a `bound_seconds` read from an operation's `request_context` is clamped to 0 to
  1800 s wherever `actionable_at` is computed (the one SQL in `operations.requests`, used by the
  protocol, Check again, Do it anyway, the operation view and class delete) and in `bound_of`.
- **Usage, what the view leaves out:** `jobs` counts a live job once and each run of a deleted job
  as a job (its identity went with `job_id`; `agent_runs` holds nothing else that names the job),
  so it can be high after a class delete. Saves and updates completed are **not** in the monthly
  view: their only records (`jobs`, `external_operations`, `saved_workouts`) go with the class or
  the MOWL connection, so a count would shrink when a class is deleted, unlike the rest of the
  figures. If the account page needs them, a durable counter is its own change.
- **Listing-name filter:** the save reconciler's name comparison normalizes to NFKC, collapses
  whitespace, folds case and ignores a trailing `…` or `...` (a listing may cut a long name); it
  stays inclusive, so an unsure comparison leaves the program a candidate.
