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
  - **Sport (issue #485, amendment below):** before that comparison, and also with `overwrite`, the
    program's own `ActivityTypeID` (0 cycling, 1 running) must be the class's sport; missing,
    unknown or another sport ends the job `program_other_sport` and nothing is written.
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
save the new version"). The three are not retryable. Issue #485 adds `program_other_sport` ("this
workout in MOWL is for a different sport than this class"), not retryable either. Backoff and retry classes are otherwise
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
- **Update never changes a program's sport (issue #485, 2026-11-03):** MOWL programs carry their own
  `ActivityTypeID` (0 cycling, 1 running; ADR 0018), and the library's re-save writes the spec's
  sport and bodies over whatever the id names. The read first therefore also compares
  `mowl_state.program_sport(program)` (the program's own `ActivityTypeID`, an exact integer that
  MOWL lists, else none) with `mowl_state.spec_sport` of the stored revision's spec (cycling unless
  it says `"sport": "running"`; every class is cycling until R1, #463, adds the sport to the brief).
  A mismatch, or a program whose sport cannot be told, ends the job `program_other_sport` (migration
  0024, `jobs.error_category` only), nothing written; `overwrite` skips only the "changed" comparison,
  never this check, and the page offers no "Update anyway" for it. The sport is not part of
  `normalized_program`, so stored `state_hash` values stay valid.
- **Running classes are saved and updated (M8 R1b, #490, 2026-11-03; ADR 0018 Amendment 2):** the
  class's sport is its stored brief's (`classes.brief ->> 'sport'`: absent or `cycling` is
  cycling, `running` is running, anything else `invalid_input`), never the content's, a model's or
  the program's. It replaces the `spec_sport` seam above: the Update sport check compares the
  program's own `ActivityTypeID` with `approval.Revision.sport`, both directions, `overwrite`
  included. The approval reads the content with the sport's schema only (`RunningDraft` or
  `WorkoutDraft`) and refuses a running revision on a Swedebeat mix or picked songs
  (`invalid_input`); Save then calls the same `workout.save(spec, creds, playlist_id)` (0.9.0 sends
  the program's `ActivityTypeID` 1, segments 0, running interval bodies with a float `Gradient`,
  on the existing playlist of the class's link; it never attaches music) and Update the same call
  with `program_id` (the library reads the target once more and refuses anything but a running
  program; the bound counts that read: `bound_seconds(segments, reads=1)`). Operation kinds, keys,
  hashes, contexts, guards and reconcilers are unchanged. `running_enabled` off is `feature_off`
  for Save and Update of a running class, at creation and in `prepare` (before the preflight); the
  reconcilers do not read it. The normal form gains a running variant (`"sport": "running"`, and
  per interval only `sec`, `zone` and `gradient`); a cycling spec and a program whose
  `ActivityTypeID` is 0 or missing keep the old form, so no stored `state_hash` changes; any other
  `ActivityTypeID` is not normalizable. Running update is supported by the library (0.9.0, owner
  live test of #482) but has not been run end to end by classbeat against live MOWL.

## Amendment of 2026-11-03: soundless classes, a "No music" source (issue #526)

Status: Proposed (Architect scoping for #526; accepted when its docs PR merges). Owner decision
(2026-11-03, chat): an instructor may want a class with no music (a soundless class, or no Spotify
Premium). Today every design names music: `design_draft`'s `MusicChoice` is a Spotify playlist or a
Swedebeat mix, and everything after it assumes a MOWL playlist and an analysis.

### Scoping: what assumes music today

| Area | Module(s) | What it assumes |
|---|---|---|
| Job input | `design/kind.py` (`MusicChoice`, `DesignDraftInput`, `ClassMusicSource`), `jobs/inputs.py` (an optional `class_id`) | `source` is `mowl_spotify_playlist` or `mowl_swedebeat_mix`; running needs a Spotify playlist; nothing compares a job's music with an existing class's |
| Admit | `design/kind.py` `admit`, `design/mixadmit.py`, `design/sport.py` | a mix is checked against the listing; a playlist is admitted as is |
| Flags | `flags/core.py`, `flags/effective.py`, `admin/settings.py`, `plans/tables.py` (`SETTING_KEYS`), migration `0007_tiers.py` (the `app_settings.key` CHECK, widened by `0016` and `0025`), `connections/routes.py`, `config.py`, `compose.yaml`, `web.py`, `worker/__main__.py` | the keys `swedebeat_enabled` and `running_enabled` are the only feature switches; a missing stored value is "on" when the environment allows it (the defect of #509) |
| Music stage | `design/music.py` (`MusicStep`, `load_music`), `design/swedebeat_music.py` | the attach or link, the analysis, the connection guard (`still_current`), `fingerprint`, `unknown_bpm` |
| Design stage | `agent/design.py` (`DesignContext`, `check`, `get_music`), `agent/mix_input.py`, `agent/running.py`, `agent/schemas.py` (`WorkoutDraft`: `"rpm": "beat"` is described as "the beat of the song under it") | every draft is fitted with `sync.fit_to_music` to a map; the timeline names tracks; music warnings |
| Model input | `agent/model_input.py` (`first_message`, `REVISE_MESSAGE`, `MUSIC_CHANGED`, `SONGS_CHANGED`; in the suite's `RULE_SOURCES`), the system prompts | a `music` block (playlist name, tracks, BPM, drops) and prompt text about the playlist |
| Revision | `design/revision.py` (`RevisionMusic`, `build`, `adopt_first`), `agent/records.py` (`Provenance.map_version`, `crossfade_sec`) | `music` has a `link_id` and a `fingerprint`; provenance has a map version; `adopt_first` writes `music_source` only while the class has none |
| Chat revise | `design/revise.py`, `design/chat.py`, `agent/revise.py` | the music step again, `music_changed` from the fingerprint, the `songs_requested` tool |
| Save and Update | `saving/approval.py` (`Revision.link_id`, `linked_playlist`), `saving/kind.py` (the link recheck under `hold`), `saving/operation.py` (`save_hash`, `save_context`, `_write_saved`), `saving/update.py`, `saving/update_operation.py` (`_whole`, `_known`, `_write_updated`), `mowl_state.py` | a positive playlist id from the user's link row (`music_unlinked` otherwise); `_whole` refuses anything but a positive id; `saved_workouts.playlist_id` has `CHECK (playlist_id > 0)` (`0005_classes.py`), so inserting `-1` would fail after the MOWL write |
| Song picking | `songs/admit.py`, `songs/phase2.py`, `songs/revise.py` | a class with picked songs has a Spotify source with `origin: "song_pick"`; `revise_songs` rewrites `music_source` |
| Swedebeat match and hand-off | `swedebeat_match/kind.py`, `classes/handoff.py`, `swedebeat_handoff/kind.py` (`swedebeat_verify_mix`), `swedebeat_handoff/adopt.py` | the class has a `song_list`; the hand-off replaces a Spotify source with a mix |
| Class API | `classes/schemas.py`, `classes/routes.py` | the page infers the music from songs and revisions; no field says "no music" |
| Web | `components/songs/MusicChoice.tsx`, `pages/NewClassPage.tsx`, `pages/ClassPage.tsx`, `components/classes/SideColumn.tsx`, `components/classes/ChatPanel.tsx` (Change songs, `use_change_songs`, "The music changed"), `components/classes/SavePanel.tsx`, `pages/JobPage.tsx`, `api/jobMessages.ts` (`designSteps`), `api/jobs.ts`, `api/connections.ts` | New class always picks music; the class page offers Change songs and the Swedebeat section; the job page lists a music step |
| Evals | `evals/suite.py` (`RULE_SOURCES`, digest), `evals/suite/manifest.json` (`suite_version` 10), `evals/smoke/`, `models/versions.py`, `models/routes.py` | every case has a playlist; the design group's prompt versions are the four stages of ADR 0012's amendment of 2026-11-03, and a results header records six stages |
| Fakes and e2e | `tests/mowlfake.py`, `tests/savetools.py`, `e2e/fake-mowl/server.py`, `compose.e2e.yaml` | every saved program has a positive `PlaylistID` |

Storage: `classes.music_source` is `jsonb` with a 1024-byte size check, `class_revisions.music` is
`jsonb` with no constraint (migration 0004), and `saved_workouts.playlist_id` is nullable but, when
set, positive. The music shapes need no migration. **The flag does** (item 2).

### Decision

1. **The source, and that it never changes.** A third `MusicChoice`, `{source: "none"}`
   (`NoMusicChoice`, `extra="forbid"`), for a cycling class only; a running brief with it is refused
   at input (`invalid_input`, 422), as running v1 designs on an existing MOWL playlist only (ADR 0018
   §2). The first design stores `classes.music_source = {"source": "none"}`. A class's music
   **kind** (`none` or not `none`) never changes once the class has a stored `music_source` or a
   revision, enforced like the sport (ADR 0018 §1) by a new `design.music_kind` module:
   - `admit_music` (in `design_draft`'s `admit`, when `class_id` is given): `none` on a class whose
     source or current revision is not `none`, or any other source on a class whose source is
     `none`, is `invalid_input`, after the generic checks of `jobs.user.create` (idempotency,
     `class_not_found`, `class_busy`, `not_connected`) and before any credit is reserved;
   - `check_stored` (in the final fenced store, after `adopt_first`): the class's `music_source`
     must have the kind of the revision just stored, or the transaction rolls back and the job ends
     `invalid_input`, so two queued jobs of different kinds cannot both land;
   - the song kinds' and the hand-off's stores refuse to write `music_source` on a `none` class
     (item 9), so nothing turns a soundless class into a music class or back.
   Save and Update key on **both**: the approval refuses `invalid_input` unless the revision's music
   is `none` exactly when the class's `music_source` is `none`; after that check, the `-1` and the
   `feature_off` of item 2 follow the revision's music. The refusals of item 9 and
   `ClassOut.soundless` read `music_source`; the checks above keep the two in agreement.
2. **The flag, which cannot be switched on around the gate.** `CLASSBEAT_SOUNDLESS_ENABLED`
   (compose `SOUNDLESS_ENABLED`, default false, web and worker) is the kill switch only. The admin
   flag `soundless_enabled` (`off`/`on`) is a new `app_settings` key: SL1 adds a migration that
   widens the `app_settings.key` CHECK (as `0016` and `0025` did; its downgrade deletes the row
   first, as `0025` does) and the key to `plans.tables.SETTING_KEYS`, `flags.core` and the admin
   settings. Unlike `running_enabled` today (#509), **soundless is on only when all three hold**:
   the environment is on, the stored value is exactly `on` (a missing, `null` or invalid value is
   `off` for this key, so `DELETE` turns it off), and every plan's `route_key` has a `design` route
   that covers soundless (item 6), checked when the effective flag is computed
   (`flags.effective`, what `GET /api/connections` returns as `soundless_enabled` and the form
   reads), not only when the admin stores `on`. A plan added later or a route cleared later
   therefore turns it off without anyone storing anything. The admin still refuses to store `on`
   (422 `soundless_not_evaluated`, a `refused` audit row) without coverage. Off: a `none` design or
   a revise of a soundless class is refused 409 `feature_off` at creation; with the flag on but the
   job's own plan's route not covering soundless, 409 `soundless_not_evaluated`. Both refusals run in
   `admit`, before the job row and before any credit is reserved (`jobs.user.create` calls `admit`
   first), in this order (SL1): the switch (environment and stored value, `feature_off`), then the
   job's own plan's route (`soundless_not_evaluated`), then every plan's route (`feature_off`, the
   effective flag). The own plan comes before every plan, or `soundless_not_evaluated` could never
   be answered. In the web, `fake` covers only when the web's own `CLASSBEAT_MODEL_PROVIDER` is
   `fake` and `CLASSBEAT_ENVIRONMENT` is not `prod` (item 6), and `compose.yaml` passes no
   `CLASSBEAT_MODEL_PROVIDER` to web, so a plain dev stack cannot switch soundless on. A queued job
   ends `feature_off` or `model_config` in the worker before any call (the backstop for a race
   between the admin check and a route change: at worst one job fails `model_config`, nothing is
   sent). Save and Update of a soundless class are `feature_off` when the
   environment switch or the stored value is off, at creation and in `prepare` (the reconcilers do
   not read it); they make no model call, so they do not check coverage.
   **Relation to #509:** this design does not depend on #509 and does not fix running. It adds the
   coverage helper `models.routes.covers(db, route_key, variant)` for `variant = "soundless"`; #509
   can reuse it for `"running"` and should make a missing `running_enabled` mean `off` the same way.
   It stays off for real users until the owner's live checks below.
3. **No music step.** A soundless job enters no `music` stage and makes **no MOWL or Spotify call**
   while designing or revising: no attach, no link, no listing, no analysis, no connection opened.
   A no-music step replaces `MusicStep` on this path; its `still_current` and `close` do nothing
   (the revision names no MOWL object; Save holds the connection itself). The job still names the
   user's current MOWL connection (the generic checks are unchanged), because the class is made to
   be saved there. Credits, quotas, the plan's length cap and gates apply as to any design.
4. **The soundless design and revise stages** (`agent`, ADR 0006 §3). Their own system prompts (an
   indoor-cycling class without music: no playlist, no beat, no drops), tools `preview_draft` and
   `submit_draft` only (no `get_music`; the revise variant has no `songs_requested`), and their own
   draft schema `SoundlessDraft` (as `RunningDraft` has its own): `WorkoutDraft` with the cadence a
   range only, so `"beat"` is never offered and the schema refuses it; the check also refuses it
   (`invalid_draft`) as a second line. The check is `workout.validate` of the draft as written, no
   `fit_to_music`; the timeline is the spec's own starts with no track; no quality report and no
   music warnings; TSS, duration, `tss_approximate` and `within_plan` as today. The stored content
   validates as a `WorkoutDraft`, so Save and the class page read it unchanged. `DesignResult` and
   `Provenance` take `map_version` and `crossfade_sec` as `None` for a soundless draft (every older
   revision has both).
5. **Model input.** The soundless first and revise messages live in a new module,
   `agent/soundless_input.py` (as running's in `agent/running_input.py`); `agent/model_input.py` is
   **not edited** by SL1 or SL2. The first message is the brief block (the cycling allowlist,
   reused, not copied) plus one fixed sentence that the class has no music, and **no `music` key at
   all**; the revise message has no playlist sentence and no `MUSIC_CHANGED`/`SONGS_CHANGED`. A
   model-input test pins that no playlist, track, BPM, drop or section text, and no `music` key,
   reaches the model. The soundless stages get **no rules section** (and no rule hits) until SL5:
   `design/guidance.py` (a rule source) tells the model to deviate "when the brief or the music asks
   for it", and rewording it would change the digest (SL1).
6. **Prompt versions, the suite and coverage** (ADR 0012 §5 and its amendment of 2026-11-03). The
   stages are named `design` and `revise`; `models.versions` keys them `soundless_design` (SL1) and
   `soundless_revise` (SL2). The `design` group then has six prompt versions and a results header
   eight stages (`all_prompt_versions`). **The suite's digest does not change in SL1 or SL2**:
   `agent/soundless_input.py` stays out of `RULE_SOURCES` and nothing listed there is edited, so
   `suite_version` stays 10 and `tests/test_evals_suite.py` passes; SL5 adds the module to
   `RULE_SOURCES`, adds the `soundless` category and bumps `suite_version` to 11 in one PR.
   **Coverage rule:** a `design` route covers soundless when its result is not stale for any reason
   (suite version, manifest digest, every design-group prompt version including both soundless
   keys, no newer failure) **and** its `suite_version` is at least `SOUNDLESS_SUITE_VERSION`, a
   constant in `models.routes` that SL1 sets to `None` (nothing covers soundless) and SL5 sets to
   11. A result recorded between SL1 and SL5 therefore never covers soundless, even with all eight
   prompt versions. The bootstrap choice never covers it; `fake` does (dev and CI only). The web
   process has no provider: there `fake` counts only when its own `CLASSBEAT_MODEL_PROVIDER` is
   `fake` and `CLASSBEAT_ENVIRONMENT` is not `prod` (`compose.e2e.yaml` passes the setting to web),
   so a production web treats nothing as covered without a recorded route. The
   worker checks coverage per attempt (`model_config` before any call), `admit` per job (item 2),
   and the effective flag per request (item 2).
7. **The revision.** `class_revisions.music = {"source": "none"}` (`NoRevisionMusic`, strict,
   `extra="forbid"`): no `link_id`, no fingerprint, no `unknown_bpm`. Provenance as today (item 4),
   `origin` `design_draft` or `revise_draft`. Its `content_hash` rules are unchanged. A revise of a
   soundless class never reports "the music changed".
8. **Save and Update** (sections 5 to 8). For a revision whose music is `none` (and a class whose
   `music_source` is `none`, item 1):
   - no link is read, at creation or under `hold` in `saving/kind.py`, and `music_unlinked` cannot
     happen;
   - Save calls `workout.save(spec, creds, -1)` (the library's "no music", `PlaylistID: -1`) and
     Update the same call with `program_id`;
   - `save_hash`, `save_context` and the update context carry `-1` (the request hashes and contexts
     of a playlist class are unchanged); `update_operation._known` accepts `-1` as the playlist and
     only there (`_whole` keeps refusing it for program ids and baselines);
   - `operation._write_saved` and `update_operation._write_updated` write `-1` as
     `saved_workouts.playlist_id = NULL` (the column's `CHECK (playlist_id > 0)` stays), so the row
     is written after a successful MOWL write instead of failing;
   - the normal form (`mowl_state`) writes "no music" as `playlist_id: null`, and reads `PlaylistID`
     `-1`, `null` or absent as `null`; any other value (a positive id, `0`) does not match, so the
     verify read leaves the write uncertain and a reconciler treats it as inconclusive, never as
     proof. Existing programs were read with a positive id, so no stored `state_hash` changes;
   - **unverified:** that a cycling Update with `PlaylistID: -1` clears a playlist someone attached
     to the program in the MOWL app. The library's docstring says it does; it has never been
     observed live (ADR 0018 Amendment 3 saw only a positive id kept on a running update). If MOWL
     keeps the playlist, the uncertain-write rules decide it unchanged (amended 2026-11-04 after
     the SL3 review, #586; the earlier text said such an update "is `uncertain`", which those
     rules do not give):
     - **the write's answer arrives:** the operation is `succeeded` and the job ends `completed`
       (the effect of the write is known: MOWL took it). The read after the write (`verify`)
       shows a playlist, so it does not match: the saved row's `state_hash` stays NULL and is
       never shown as verified, and the next Update's read first reports `program_changed`
       (unless "Update anyway"). Making it `uncertain` would not help: the update reconciler
       could never settle it (the read never matches and no longer equals `before_hash`), and
       Do it anyway would send the same write again;
     - **the write's answer is lost:** the operation is `uncertain`, and the reconciler finds the
       target neither matching nor equal to `before_hash`, so it stays inconclusive (Check
       again, Do it anyway), as for any other partial or unmatched update.
     The read first already sees an attached playlist as `program_changed`.
   - **Known gap, owned by SL6 (#532):** the Save and Update panel's "completed" sentence
     ("Updated. Your MOWL workout now matches this version.") and its state line come from the
     job and the saved row's `content_hash`, not from whether the last write was verified. For an
     unverified soundless Update (the case above) it would claim a match the read-back
     disproved. SL6 must expose whether the last write was verified (for example
     `state_hash IS NOT NULL` on the class's saved summary) and, when it was not, say that the
     write went through but classbeat could not confirm the workout matches (check it in the
     MOWL app), never "now matches". SL3 changes no UI.
   - Operation kinds, keys, the bound, the fence and the uncertain-write rules are unchanged. The
     approval refuses `invalid_input` for a `none` revision of a running class (item 1).
9. **Song picking, the Swedebeat match and the hand-off** (ADR 0008, ADR 0016). A soundless class has
   no `song_list` and no mix. `design_with_songs` (every mode) and `revise_songs` on it, a
   `swedebeat_match` of it, a hand-off for it and a `swedebeat_verify_mix` of it are refused
   `invalid_input` at creation by an explicit check of `classes.music_source`, not only by the
   absence of a list or a stored match; their final stores (`songs.phase2`, `songs.revise`,
   `swedebeat_handoff.adopt`) refuse to write `music_source` over a `none` source. The class page
   shows neither Change songs nor the Swedebeat section for it.
10. **The class API and the web.** `ClassOut` gains `soundless: bool` (true only when
    `music_source.source` is `none`; false for every older class). New class offers "No music" next
    to the playlists and mixes when `soundless_enabled` is on and the sport is cycling; the class
    page shows a "No music" chip; the chat panel offers no Change songs and never says the music
    changed; the job page shows a soundless job without a music step (`designSteps`); the Save and
    Update panel says the workout is saved without music. Export and deletion are unchanged (guard
    12: no new table and no new user column; the new `app_settings` key is not user data).

### Alternatives

- **An empty or silent "playlist"** (a fake map of one silent track) so the existing stages run
  unchanged: the model would be told about music that does not exist, `fit_to_music` would snap to
  invented bars, and Save would still need a MOWL playlist. Rejected.
- **Copy `running_enabled`'s gate as it is.** Rejected: a missing stored value or an environment-only
  `on` skips the coverage check (#509).
- **Land the stage keys, the gate and the suite bump in one PR.** Rejected for a separate module kept
  out of the digest plus a minimum suite version: SL1 and SL2 stay reviewable, and no result
  recorded in between can cover soundless.
- **Soundless running in the same change.** Deferred: it needs a third and fourth prompt variant, and
  ADR 0018 keeps running on an existing playlist in v1.

### Consequences

- Seven implementation PRs (#527 to #533 below) and one migration (the flag key, SL1).
- The design group's prompt versions grow to six and a results header to eight stages, so any
  design-group result recorded before SL1 and SL2 is stale (`prompt_versions`): also for running
  coverage, until a new live run. Nothing has been recorded live, so nothing changes in practice.
- **Owner steps before the flag goes on** (live, never an agent's, each separately authorized):
  1. one cycling save with `PlaylistID: -1` on a new private program, read back once, to confirm
     the read-back form item 8 assumes;
  2. one cycling Update of that same program, still with `PlaylistID: -1`, read back once (and, if
     the owner wants the clearing confirmed, after attaching a playlist to it in the MOWL app);
  3. a recorded `suite --live` pass on suite version 11 or later that covers the soundless stages,
     then `models enable` for each plan key.
  Until all three, `SOUNDLESS_ENABLED` stays false outside the e2e stack, and the effective flag
  stays off anyway without coverage.

### Issues (one PR each, in dependency order)

| Issue | Scope | Depends on | Developer / Review |
|---|---|---|---|
| SL1 #527 | `none` source and the music-kind checks, the flag (migration, effective flag with coverage, admin refusal, `admit` refusals), no-music step, soundless design stage and schema, `agent/soundless_input.py`, revision music, provenance, `soundless_design` key, coverage rule with `SOUNDLESS_SUITE_VERSION = None` | this amendment | Opus / Opus |
| SL2 #528 | `revise_draft` of a soundless class (flag, coverage, soundless revise stage, no `songs_requested`, chat), `soundless_revise` key | #527 | Sonnet / Opus |
| SL3 #529 | Save and Update with `PlaylistID: -1`: approval agreement, no link recheck, contexts, `_known`, `NULL` in `saved_workouts`, normal form, reconcilers, fakes | #527 | Opus / Opus |
| SL4 #530 | `ClassOut.soundless`; song picking, match, hand-off and verify refuse a soundless class; their stores keep `music_source` | #527 | Opus / Opus |
| SL5 #531 | Evals: `agent/soundless_input.py` in `RULE_SOURCES`, `soundless` category, `suite_version` 11, `SOUNDLESS_SUITE_VERSION = 11`, smoke cases and fake scripts | #527, #528 | Sonnet / Opus (it sets the coverage constant) |
| SL6 #532 | Web: "No music" in New class, class page, chat panel, job page, Save panel | #527, #528, #529, #530 | Sonnet / Sonnet |
| SL7 #533 | E2E done-when on the fakes: design, revise, save, update; no playlist call | #527 to #532 | Sonnet / Sonnet |

## Amendment: beat-one corrections (2026-11-03, issue #535)

Roadmap §6 asks for "beat-one corrections per user on top of an analysis" and a per-song control on
the class page. §11 above said they are rows of a class-owned table registered in guard 10, never
shared. The owner asked for them on 2026-11-03 (#535). This amendment is the design; the issues are
listed at the end. It follows the soundless amendment above and keeps its rules.

1. **The library.** The vendored `mowl-workouts` (0.10.0 since #556; the functions below are the same
   as in 0.9.0) has `library.correct_beat1(creds, store, mix, song_no, beats)`, for a Swedebeat mix
   only. It calls `api.find_swedebeat` (a MOWL request) and adds `beats` to the `beat1_shifts` of the
   user's link in a `MapStore`, which every later workout of theirs on that mix reads. classbeat
   **never calls it**: it would make a request from the web process, and a correction stored with
   the link would reach every class of that user on the music. classbeat uses the pure function under
   it, `library.shift_beat1(m, song_no, beats)`. That function moves a song's `first_downbeat`,
   `first_phrase` and events by whole beats, in place, and raises `ValueError` for a song the map
   does not have. classbeat runs it on its **own copy** of the map, once per correction, with the
   stored total. No wheel change is needed. `beats` keeps the library's meaning: -3 to 3, never 0;
   positive when the changes in that song come early, so beat 1 moves later. A shift is counted in
   beats, so it follows the song's tempo.
2. **The table** (the next free migration when BC1 merges; issue BC1). `beat_one_corrections` is
   owned and class-owned (§1's rules). Columns:
   - `id`, `owner_id`;
   - `class_id`: an owner-inclusive foreign key to `classes`, `ON DELETE CASCADE`;
   - `fingerprint text` (`^[0-9a-f]{64}$`);
   - `song int` (1 to 500);
   - `beats smallint` (`CHECK (beats BETWEEN -3 AND 3 AND beats <> 0)`; clearing deletes the row);
   - `bpm real` (above 0, at most 1000; the song's BPM when the row was set, item 3);
   - `created_at`, `updated_at`.

   Constraints and limits:
   - `UNIQUE (owner_id, class_id, fingerprint, song)`, RLS `owner_isolation`.
   - **At most 200 rows for the class's current fingerprint** (409 `correction_limit`). Rows for
     other fingerprints are **kept and not counted**: Make current can bring them back to life
     (item 8). They are bounded anyway. A user writes only rows for the current fingerprint, at
     most 200 per fingerprint, and a class gets a new fingerprint only through a job: a design, a
     revise, Change songs, or a mix analysed again (a refit keeps the fingerprint). Chat revisions
     are bounded by the revision limits, but a new design of an existing class is not. The real
     bound is therefore about (the credits the user spends on the class + the revisions) × 200
     rows. That is harmless, so no per-class bound on fingerprints is added.
   - No title, no Spotify or MOWL id, no map.

   Guard 10: deleted with the class and the account. Guard 12: exported (`class_id`, `song`,
   `beats`, `bpm`, the times). `fingerprint` is left out because it is bookkeeping: the same value
   is already in the exported revision's `music`. Nothing in the table is shared. A correction never
   writes `swedebeat_mix_maps` or any analysis.
3. **Which song.**
   - On a Swedebeat mix a song is the map's 1-based `song` (a song found in the audio).
   - On a Spotify playlist (the user's own or a class playlist of picked songs) it is the 1-based
     track in play order. The library's Spotify map holds one entry per section, so a correction of
     track `n` is applied to every entry with `track == n`. Spotify's beat 1 is the section start
     the library derives (`beat1_source: "spotify section"`), so a whole-beat shift is meaningful
     there too.

   A correction is bound to the **fingerprint** of the music it was set on: the current revision's
   `music.fingerprint`, computed before any correction. For Spotify that is the track ids and
   lengths; for a mix, the map's hash. It is applied only while the music step's fingerprint is the
   same, so an edited playlist, Change songs, or a mix analysed again never moves a different song.

   - **At set time** the song must be one of the current revision's `music.songs` (422
     `invalid_input` otherwise). Those are taken from the map, so the map has the song.
   - **At apply time** a row for a song the map lacks is skipped and logged as a warning (a count
     only, no ids). It cannot happen for the fingerprint the row was set on, but the job never fails
     on it.
   - **BPM arriving later.** The Spotify fingerprint ignores BPM. A track MOWL had no BPM for is
     fitted at the library's guess of 120. When MOWL later learns the BPM, the fingerprint stays the
     same and the stored shift is kept in beats, so it moves by the same number of beats at the new
     tempo. The row records the BPM it was set at. When a song's current BPM differs from it, the
     class detail says `bpm_changed` for that song and the page asks the user to listen again.
4. **Applying** (issue BC2; the pure functions in `classbeat.agent.beat_one`, BC1). A job that
   produces a draft reads the class's corrections for the music step's fingerprint in a fenced
   transaction (`class_id` from the fenced job row, the owner from the lease, never the input). It
   carries them with the music data: `mix_input.MixData` gains `beat_one`, and **`mix_input.SpotifyData`
   is a new subclass** of `model_input.MusicData` for a Spotify run. `model_input` stays as it is,
   because it is one of the eval suite's `RULE_SOURCES`, so the suite's digest and every recorded
   route stay valid. `mix_input.music_map` applies the corrections to its copy after the crossfade
   conversion, as the library's `_entry` does. So the fit (`sync.fit_to_music`), the timeline, the
   quality report, the warnings and the model's music summary all see the corrected map. A corrected
   mix song is no longer "an estimate" (the library's `beat1_shift` rule).

   This holds for `design_draft` on an existing class, `revise_draft`, `design_with_songs`,
   `revise_songs` and `refit_draft`. **A soundless design or revise** (the amendment above: no music
   step) reads and applies no corrections. The model sees numbers only (times, BPM, song indices).
   A correction never reaches it as text, and the model has no tool that sets one.
5. **What a revision records.** `class_revisions.music` (both the Spotify and the Swedebeat form)
   gains two optional fields. They are absent on every earlier revision, which therefore validate and
   hash exactly as before.
   - `beat_one`: the corrections applied, `[{song, beats}]` sorted by song (absent when none).
   - `songs`: the songs the class plays (those starting before the fitted workout ends), each
     `{song, start, end, bpm, estimate}` in seconds of the class as heard, `start`, `end` and `bpm`
     rounded to one decimal (`agent/beat_one.py`), plus `title` for a Spotify track. A song whose
     values fail the schema (a BPM MOWL reported outside 0 to 1000) is left out of `songs`, with a
     logged count, and never fails the job. The title is cleaned, Spotify-derived like `unknown_bpm`, and gone with the
     class.

   **Size: the new fields only.** `songs` holds at most 200 entries (a class is at most 180
   minutes), each with a title of at most 100 characters. A class whose music plays more than 200
   songs keeps the first 200 in `songs`: the later songs get no control, a correction for one of
   them is refused (422 `invalid_input`, it is not in `songs`), and the fit still uses the whole
   map. `beat_one` holds at most 200 entries (the
   cap of item 2). A `songs` entry is at most about 490 bytes of canonical JSON (about 90 bytes of
   numbers and keys, plus a title of at most 100 characters at up to 4 bytes each). A `beat_one`
   entry is about 25 bytes. The two lists together are therefore at most about 103 KB on any valid
   map. `revision.build` checks the canonical size of `songs` plus `beat_one` against 128 KB, as a
   defence that valid input cannot reach. The check never covers the whole `music` object or the existing `unknown_bpm` (up to 500
   titles of 200 characters, about 110 KB, as today), so nothing that is valid today is refused.
   Stored revisions, Swedebeat match revisions (`swedebeat_match/revision.py`) and `song_list`
   inserts do not go through `build`, so they are unaffected and no migration is needed.

   **One source for "estimate":** `songs[].estimate` is computed from the corrected map. For a mix
   song that means a beat-1 margin below the library's 1.3 and no correction; for a Spotify track,
   one MOWL had no BPM for. The warnings use the same map. `unsure_beat_one` of a mix revision keeps
   its meaning (the analysis's own list, uncorrected) and is read only for revisions without
   `songs`.

   A revision with `songs` is one with beat data, and the control is shown only for it. A revision
   from before this amendment and a soundless class (`music: {source: "none"}`) have none.
6. **Kind `refit_draft`** (issue BC3; no model, no credit, no cost, no `agent_runs`).
   - **Input:** `{class_id, base_revision_id, mowl_connection_id}`.
   - **`admit`** runs in the create transaction with the class row locked. Before anything is
     reserved it refuses:
     - a class whose `music_source` is `{"source": "none"}`: `invalid_input`, through
       `design.music_kind.is_none` on the locked class's `music_source`. It **never** goes through
       `music_kind.admit_class` or `admit_music`. Neither answers the question a refit asks:
       `admit_class` passes a music class and sends a soundless one through the soundless gate
       (`feature_off` when it is closed, an admit when it is open, so a refit would then run a music
       step on a class with no music), and `admit_music` takes the design job's own music kind and
       answers `invalid_input` only when that kind differs from the class's known one (its stored
       source, else its newest revision's music), so it never refuses a soundless class for a
       soundless request;
     - a class with no `music_source` (a duplicate of a class on picked songs, #550's item 5): 409
       `not_changeable`, as #550 specifies for `revise_draft`;
     - a base that is not a `draft` of the class (`invalid_input`), or not its current revision
       (`stale_base`);
     - the running flag (`sport.admit_class`), and a mix's flag and analysis limit
       (`mixadmit.admit_class_mix`), as for a revise;
     - more than 50 refits per class (409 `refit_limit`);
     - nothing to apply: the corrections for the base's fingerprint already equal its
       `music.beat_one` (409 `nothing_pending`), so a click cannot store an identical revision and
       use up the limit.

     The one-open-job-per-class index (`class_busy`) and the plan's concurrent jobs apply as for
     every kind. `admit` stores the base's hash.
   - **Run** (stages `music`, `fit`):
     1. the class's music step (`kind.music_step`, unchanged checks);
     2. a fingerprint other than the base's ends the job `music_changed` (a new job category, in
        BC1's migration), and nothing is stored;
     3. the corrections for that fingerprint are applied;
     4. the base's stored content is fitted again (`agent.design.check`, or
        `agent.running.check_running` for a running class). That keeps the total and moves each cut
        to the corrected grid inside the fit's 6-second tolerance (a shift of at most 3 beats is at
        most 3 s at 60 BPM);
     5. the final fenced transaction runs the connection guard, stores the revision (`parent_id` =
        base; provenance `{origin: "beat_one", base_revision_id, classbeat_version, mowl_version,
        map_version, crossfade_sec, sport?}`, no model fields) and sets `current_revision_id` by the
        revise compare-and-set. The revision is built by `revision.build_refit`, which shares
        `revision.build`'s content and music code, and so its validation and the size check of item
        5. It writes the provenance above itself, because `records.provenance` and its `origin`
        literal describe model runs; `beat_one` is the refit's origin.

     The revision is stored even when the content is unchanged, because its `music.beat_one`
     records the applied set. Saved then stays "up to date", since that compares content hashes
     (amendment of 2026-10-30). Refits are not chat revisions and do not count toward
     `CLASSBEAT_REVISIONS_PER_CLASS`.
7. **API** (issue BC4, `classbeat.classes`; CSRF and signed in; no recent authentication, because it
   is the user's own work). Each call is one transaction with the class row locked. A foreign or
   unknown class is 404, and so is a revision that is not one of the class's own, as Make current
   does.
   - **`PUT /api/classes/{id}/beat-one/{song}`** with `{beats, revision_id}` sets one song's total
     for the music of `revision_id`:
     - the class's `music_source` is checked first, explicitly: `none` (`music_kind.is_none`) is
       409 `no_beat_data`, and no `music_source` (a #550 copy of a class on picked songs, which
       `refit_draft` refuses) is 409 `not_changeable`. The class detail then has `available: false`,
       so the page shows neither the panel nor **Apply**;
     - `revision_id` must be the current revision (409 `stale_base`);
     - its `music` must have `songs` (409 `no_beat_data`) containing `song` (422 `invalid_input`);
     - `beats` must be a JSON integer from -3 to 3, not 0 (422);
     - the cap of item 2 applies (409 `correction_limit`).

     The fingerprint and the song's BPM are read from that revision, never sent by the browser.
   - **`DELETE /api/classes/{id}/beat-one/{song}?revision_id=`** clears the current music's row for
     `song` (idempotent). The same checks as PUT: a revision of another class is 404, and one that
     is not the current revision is 409 `stale_base`, and nothing is deleted.
   - **`DELETE /api/classes/{id}/beat-one/rows/{row_id}`** clears **any** row of the class by its
     id: a stale one, or one for a song the current revision no longer reaches. It is keyed by
     `owner_id`, `class_id` and `id`. A row id of another class of the same user deletes nothing and
     answers 404; another user's row is hidden by RLS and gets the same 404.
   - **`DELETE /api/classes/{id}/beat-one`** clears every row of the class.

   An open job does not block a change: the job applies what it read, and its revision's `beat_one`
   says which.

   `GET /api/classes/{id}` gains `beat_one: {available, revision_id, songs, pending, stale}`:
   - `songs`: the current revision's `music.songs` (`song`, `start`, `end`, `bpm`, `estimate` and,
     for a Spotify track, `title`), each with the class's `beats` (0 when it has none), the `beats`
     the revision `applied` (0 when none) and `bpm_changed`. `bpm_changed` compares the row's BPM
     with the song's after rounding both to one decimal, because the column is single-precision
     (128.3 reads back as 128.30000305) and `songs` holds one decimal;
   - `pending`: true when the corrections for the current fingerprint differ from the revision's
     `music.beat_one`;
   - `stale`: the rows for other fingerprints and for songs the revision does not play, as
     `{id, song, beats}`, so they can be cleared one by one or all at once.
8. **Make current and Duplicate.**
   - **Make current** (`POST /api/classes/{id}/current`, §2) brings an older revision's fingerprint
     back. Its rows are live again, and `pending` and `stale` are recomputed against it. This is why
     stale rows are kept.
   - **Duplicate** (#550): the copy's revision keeps `music.beat_one`, and the same transaction
     copies the source class's rows for that revision's fingerprint, re-keyed to the copy's class
     (same fingerprint, song, beats and BPM). Otherwise the copy would show `pending` and **Apply**
     would strip the corrections. **This text is the text of record.** #550's amendment and its
     implementation (DU1) add the row copy to the duplicate transaction, and make sure its insert
     guard allows that insert, whichever lands second.
9. **When the music changes under the corrections.** A mix's fingerprint is the hash of its map.
   Analysing it again (after the 30-day cache, an eviction at 20 maps per user, or a new
   `music.MAP_VERSION` in a wheel upgrade) gives a new fingerprint, and the class's corrections go
   inert. The same happens to a Spotify class whose playlist changed. Nothing is deleted. The panel
   lists these rows (`stale`, which also holds rows for songs the version does not play) and says:
   "These timing fixes are for music this version does not play: it was analysed again or changed,
   or the song is not in this version. They are kept and do not count towards the limit, so making
   an earlier version current can use them again. Clear them if you no longer need them." (the
   sentence as shipped in `BeatOnePanel.tsx`). The user can clear them one by one or all at once.
10. **Save and Update** are unchanged: they write the approved revision's content, which a refit or
    a revise has fitted with the corrections. A changed correction therefore reaches MOWL only
    through a new revision, and Saved shows "out of date" when its content differs. While
    corrections are pending, the page says the current version does not include them yet and offers
    **Apply timing fixes** (a `refit_draft` job) next to Save.
11. **UI** (issue BC5). On the class page, when `beat_one.available`, a "Beat 1" panel shows:
    - the songs the class plays: number or title, start, BPM, and an "estimate" or "tempo changed"
      note;
    - for each song, **Earlier** and **Later** (44 px targets, labelled with the song) and
      **Reset**;
    - the value in words ("beat 1 two beats later") in a live region;
    - the plain-language effect ("If the changes in this song land early, move beat 1 later");
    - the notice of item 9 with **Clear** per row and **Clear all**;
    - the pending notice with **Apply**.

    It uses Night Studio tokens and `src/components/ui` only, and has no flag. A soundless class
    shows no panel.
12. **Tests and threat model** (issue BC6, and each issue's own tests):
    - unit tests of `beat_one.apply`: mix and Spotify maps, the event re-snap, a song the map lacks
      skipped;
    - RLS and cascade database tests, and the guard 10 and 12 entries;
    - job tests on the in-process fakes: a correction shifts the fitted intervals; it survives a
      chat revision; another class and another user are unchanged; a changed fingerprint is inert,
      and a refit then ends `music_changed`; a soundless class is refused;
    - API tests: the cap counts the current fingerprint only, rows are cleared by id and all at
      once, and the soundless refusal holds;
    - Vitest with the page;
    - a Playwright spec on the e2e fakes, with an axe audit of the panel: a fake mix and a fake
      Spotify playlist, set, apply, revise, Save and Update, export and delete.

    New threat-model row **T123** (added by BC6): one user's correction reaching another user or
    class, a correction moving a different song after the music changed, or a correction written
    into a shared analysis. Controls: owned rows under RLS keyed by class and fingerprint, the
    fingerprint read server-side from the current revision, corrections applied to a per-job copy
    only, and no library call that writes a link.

| Issue | Scope | Developer / review | Depends on |
|---|---|---|---|
| BC0 (#538) | this amendment (and the ADR 0016 §1 note) | docs / Opus | none |
| BC1 (#539) | the next free migration (the table, RLS, `music_changed`), guards 10 and 12, `classbeat.agent.beat_one` (pure apply, songs) | Opus / Opus | BC0 |
| BC2 (#540) | apply in every draft kind (not soundless), revision `music.beat_one` and `songs` with the size cap | Opus / Opus | BC1 |
| BC3 (#541) | kind `refit_draft` (refusing a soundless class through `music_kind.is_none`, a class with no music source, and nothing pending; built by `build_refit`) | Opus / Opus | BC2 |
| BC4 (#542) | the API (set, clear, clear by row, clear all; the current-fingerprint cap; the soundless and no-source refusals) and `beat_one` on the class detail | Opus / Opus | BC1, BC2 |
| BC5 (#543) | the class page panel, Vitest | Sonnet / Sonnet | BC3, BC4 |
| BC6 (#544) | Playwright on the fakes, axe, T123 | Sonnet / Sonnet | BC5 |

## Amendment (2026-11-03): class revision history and duplicate a class (#536)

Owner (2026-11-03): "Class history" is the revision history of one class plus a **Duplicate**
action. Proposed in the docs PR, to be confirmed by the Architect. The quota and gate choices are
recorded in ADR 0011's amendment of the same date.

**What exists (scoping).** `classbeat.classes.service` already serves the revision tree
(`GET /api/classes/{id}/revisions`: number, parent number, origin, created, segments, intervals,
`total_sec`, `cost_micros`, `is_current`) and one revision; `POST /api/classes/{id}/current` is §2's
pick, checked against the current plan since #496 (`check_stored_draft`, 409 `quota`), with no model
call and no credit; `classbeat.design.diff.summarize` computes the change summary of §3 from two
stored drafts. The page's `RevisionsPanel` lists revisions and offers Make current. A class is
created only by `classbeat.jobs.user.create` (a design job), and a revision only in a job's final
fenced transaction. Nothing here changes the queue, the write protocol or any table. Duplicate is
`POST /api/classes/{id}/duplicate` with an `Idempotency-Key`: 201 for a new copy and 200 for a replay of
the same key and request (item 7 of D).

### H. Revision history (no migration, no new table)

1. **Timeline.** A new read-only route `GET /api/classes/{id}/history` (its own module,
   `classbeat.classes.history`, so the existing revisions route keeps its shape) lists every `draft`
   revision with the revisions route's fields plus its `content_hash` (hex, for Make current) and,
   computed in the web process from rows the user already owns: `tss` (`workout.tss` of the stored
   content under the class's sport schema, the same function the change summary uses; `null` when it
   cannot be computed, never a model number), `request` (the user's own `class_messages.body` of
   `provenance.request_message_id`, `null` for a design or a duplicate), `saved` (`live`: a live
   `saved_workouts` row has this `revision_id`; `detached`: only a detached one; `none`),
   `from_songs` (the draft's `music.song_list_id` is set: designed on approved songs) and, for a
   duplicate, `duplicated_from` `{class_id, number, class_title}` (title `null` when that class is
   gone or not the user's); the body also says `can_change`: a revise would be admitted now as far
   as the class's music goes, that is the class has a stored brief and a music source **and**
   `design.music_kind.admit_class` admits it. For a soundless class it follows the soundless gate
   (SL2, #528, has merged), because `can_change` calls the same function the revise's `admit`
   calls. At most 200 revisions are read, oldest first (a fixed
   `LIMIT`, the revisions route's bound): design revisions from Swedebeat hand-offs are not chat
   revisions, so the revision limit does not cap them, and the `LIMIT` is what bounds the per-row
   TSS work. Compare reads two contents of at most 64 KB and makes no external call, so it needs no
   rate limit. Revisions stay immutable; nothing is written by a read.
2. **Compare.** `GET /api/classes/{id}/history/compare?from=<revision id>&to=<revision id>` (signed in, read
   only): both ids must be `draft` revisions of the user's own class (looked up with `owner_id` and
   `class_id`; anything else is the same 404 `not_found`). The body is `diff.summarize(from, to,
   sport)` unchanged (segments added and removed, intervals changed, added and removed, duration and
   TSS before and after, `text_changed`), plus `warnings` `{from, to}` (the count of tracks MOWL had
   no BPM for and of songs whose beat 1 is an estimate, per revision) and `music` `{same_source,
   same_fingerprint}` (booleans only: no playlist id, mix id or title). A content that no longer
   validates is 500 `internal` with the reason only. The page words it ("2 intervals changed, 3 min
   longer, TSS 64 to 71, the music changed").
3. **Make current** is §2 and #496 unchanged; the timeline offers it on every non-current draft.
4. **Plan gate:** none. ADR 0011 section 8 listed "history" as a possible per-plan gate and Q7 keeps
   every gate off in the beta; storing it would need a `plans` column, the admin edit and guard 11
   for a gate nobody sets. History is on for every plan (ADR 0011 amendment of 2026-11-03).

### D. Duplicate a class

`POST /api/classes/{id}/duplicate {revision_id, content_hash, title}` with an `Idempotency-Key`
header (signed in, CSRF, no recent authentication: it is the user's own work; `title` 1 to 100 characters after trimming and removing
control characters; the page proposes "<title> (copy)" cut to 100). It creates a **new class** whose
revision 1 is a copy of the chosen revision. One `user_session` transaction:

1. **The source** is the user's own class (`owner_id`; a foreign, unknown or malformed id is 404
   `not_found`, so another user cannot duplicate or probe a class), with a stored `classes.brief`
   (a class still in its first design has none: 409 `not_ready`); the revision is a `draft` of that
   class (404 otherwise), its stored `content_hash` equals the sent one (409 `hash_mismatch`) and the
   SHA-256 of its canonical content recomputed now; the content validates under the class's sport
   schema (`stored_draft`, then `workout.validate` / `validate_running`), else 500 `internal`. An
   open job on the source does not block it: revisions are immutable and the source is only read.
2. **Flags (fail closed, the stricter rule on purpose):** 409 `feature_off`, nothing created, for
   a running class while `running_enabled` is off; for a Swedebeat mix while `swedebeat_enabled` is
   off when **either** the class's current `music_source` **or** the chosen revision's `music` is a
   mix (the copy takes the class's source, item 5, but its revision 1 still names the mix's link);
   and for a soundless class (the class's current `music_source` **or** the chosen revision's `music`
   is `{source: "none"}`, #526, as for a mix; `classes.duplicate` checks both) unless the full soundless
   gate admits it for this user: `design.music_kind.admit_soundless`, the same three conditions a
   soundless design meets (the switch, then the user's own plan's `design` route covering soundless,
   answered 409 `soundless_not_evaluated`, then every plan's route, `feature_off`). No model runs
   for a duplicate, but the copy is a soundless class the user could not have designed otherwise,
   so it is admitted exactly as a design would be.
3. **Plan, current plan only** (ADR 0011 amendment): the brief through `quota.gates.check_brief` and
   the copied draft through `check_content` (409 `quota` `length` or `feature`, the usual sentence),
   then one class credit (409 `quota` `classes`, `MeteredLedger.take_class`, ADR 0011). The whole
   transaction runs under the owner advisory lock (`jobs.user.lock_owner`), taken first, before the
   period lock, as job creation does. A refusal creates nothing.
4. **What is created:** `classes` (`id` new, `title`, `brief` copied byte for byte, so the same
   sport; `music_source` per item 5; `current_revision_id` the new revision; `dismissed_rules`
   empty) and one `class_revisions` row: `number` 1, `kind` `draft`, `parent_id` NULL, `job_id`
   NULL, `content` and `content_hash` copied, `music` copied **without** `song_list_id` (it names a
   song list of the source class), and a provenance with `origin: "duplicate"` and
   `duplicated_from: {class_id, revision_id, number}`: the descriptive fields (provider, model,
   effort, versions, `map_version`, `crossfade_sec`, `tss_approximate`, `rules`, `sport`) are the
   source revision's, because they describe how the content was made; `agent_run_ids` is empty,
   `usage` zero and `cost_micros` 0, so no cost is copied. `Provenance` allows an empty
   `agent_run_ids` only for `origin: "duplicate"`, which names neither a base nor a request.
   **§2's rule "rows are created only by a job's final fenced transaction" gains this one exception:
   the duplicate route inserts revision 1 of the class it creates in the same transaction**, from a
   revision of the same user it has just re-validated; the browser sends ids and a hash, never
   content.
5. **Music, never trusted from the copy.** The copy's `music_source` comes from the source
   class's **current** `classes.music_source`, never rebuilt from the chosen revision (a revision's
   `music` holds no Spotify playlist id). The two can differ: after a Swedebeat hand-off
   (`swedebeat_handoff/adopt.py` replaces the source with a mix) or a Change songs, an older
   revision was fitted to other music. This is what Make current already allows, and a revise of
   the copy handles it as any revise does (the music step's `music_changed` fact, §3). It is copied
   only as the user's own choice: `{source: "mowl_spotify_playlist", spotify_playlist_id}` (a class
   on the user's playlist), `{source: "mowl_swedebeat_mix", mix_id}`, or a soundless class's
   `{source: "none"}` (#526, gated as item 2 says). A soundless copy keeps `classes.music_source =
   {source: "none"}` and its revision 1's `music` `{source: "none"}`, so the soundless amendment's
   item 1 ("the kind never changes", `music_kind.check_stored`) holds for the copy; a revise of the
   copy goes through `music_kind.admit_class` like any revise (the soundless gate, SL2, #528). A class on picked songs
   (the rule keys on the class's `music_source.origin` = `song_pick`, which names the source
   class's `class_playlists` row) gets `music_source` NULL: its working and final playlists stay the
   source class's. Ownership is re-checked at use by the existing paths: a revise attaches through
   the user's own MOWL connection (§3), a mix must be in the user's own fresh listing
   (`mixadmit`), Save needs a live link of the user (`music_unlinked`).

   **A class without a music source is refused by the server at every entry point, not only by the
   page.** Before this amendment a class with a stored brief always had a `music_source`
   (`adopt_first` sets both); a copy of a class on picked songs is the first with a brief and none.
   - `revise_draft`'s `admit` (`classbeat.design.revise`) refuses it with 409 `not_changeable`
     right after the `stale_base` check and **before anything is written**: no `class_messages`
     row, no job, no job slot. The worker's existing `invalid_input` (no `music_source` at run
     time) stays as the second line. The page words the reason ("This class has no music of its
     own, so it cannot be changed by chat") and hides Change it when the timeline's `can_change`
     is false.
   - `revise_songs` refuses already with `not_song_picked`: the copy has no song list (`song_list_id`
     is dropped from its revision's `music`, and no `song_list` revision is copied).
   - The Swedebeat match and hand-off need a `song_list` of the class to match; the copy has none,
     so they refuse as for any class without one.
   - Save and Update use the revision's `music.link_id` and do not read `music_source`, so the copy
     can be saved (the link is the user's own and is checked live, §5).

6. **Not copied:** `saved_workouts` (no MOWL program link, no Save/Update state), the song lists and
   `approved_swedebeat_match_id`, `class_playlists`, Swedebeat hand-offs, jobs, events, external
   operations, `class_messages`, `agent_runs`, the dismissed rules. No MOWL, Spotify, Swedebeat or
   model request is made.

7. **A retried request costs one credit.** The `Idempotency-Key` header follows the jobs API's
   convention (16 to 64 of `A-Za-z0-9_-`, made by the page per user action; 400
   `idempotency_key_missing` or `idempotency_key_invalid`). The key and the SHA-256 of the
   canonical request (`class_id`, `revision_id`, `content_hash`, cleaned `title`) are stored in the
   copy's `provenance.duplicated_from` (`idempotency_key`, `request_hash`; no migration). Under the
   owner lock, before anything else is checked, the user's own duplicate revisions are looked up by
   that key. A new copy answers 201 with `{class_id, revision_id}`; the same key and request answer
   200 with the class made before (no second credit, no
   second class); the same key with another request is 409 `idempotency_mismatch`. A key whose copy
   was deleted since is free again (the user deleted the class it made). The page also disables its
   button while the request is in flight.

8. **Beat-one corrections** (the beat-one amendment, #549, item 8, is the text of record). The
   copy's revision 1 keeps `music.beat_one` (item 4 copies `music` except `song_list_id`), and the
   same duplicate transaction copies the source class's `beat_one_corrections` rows for that
   revision's `music.fingerprint`, re-keyed to the copy's class: same `fingerprint`, `song`,
   `beats` and `bpm`, new `id`, `owner_id` the user's, under that table's cap (at most 200 rows for
   the class's current fingerprint) and its unique key. Otherwise the copy would show the
   corrections as `pending` and **Apply** would strip them. Rows for other fingerprints are not
   copied. A copy that gets no `music_source` (a class on picked songs, item 5) gets no rows: it
   cannot be refitted (`not_changeable`). The insert guard allows that insert in
   `classbeat.classes.duplicate`. **Sequencing:** the table comes with BC1 (#553), which is not on
   `main` while DU1 (#547) is written, so the copy is a follow-up PR, DU3 (#585), that depends on
   BC1 and DU1 and adds the insert and its test. DU3 must merge before the beat-one panel ships, so
   no user sees a copy without its corrections.

**Independence.** The two classes share no row: changing, revising, saving, updating or deleting
one never touches the other (`duplicated_from` is an opaque id in the copy's provenance, read only
to show the source's title). The duplicate's revision 1 is not a chat revision, so the revision
limit (§4) counts from zero, which is why the duplicate takes a class credit (ADR 0011).

**Account data.** No table or column is added: the class and its revision are exported and deleted
with the account like any class (ADR 0014, guard 12 and the retention registry unchanged); deleting
the source leaves the duplicate whole.

**Known differences in the figures (recorded, not fixed now).** A duplicate's credit is a
`committed` `credit_ledger` row with `job_id` NULL, which the admin user page's ledger list and the
account export show exactly like a committed credit whose job was deleted with its class; the
account page's held count includes duplicates, while Usage's `classes_designed` counts completed
design jobs only, so the two numbers can differ by the month's duplicates. A "duplicate" marker on
the ledger row would need a column (a migration and the ledger trigger); it is left for a later
change if the admin view needs it.

**A guard keeps the exception narrow.** DU1 adds a source guard (`tests/test_insert_guard.py`) that
allows `INSERT INTO classes` only in `classbeat.jobs.user` and `classbeat.classes.duplicate`,
`INSERT INTO class_revisions` only in the job final-transaction modules (`design.revision`,
`songs.song_list`, `swedebeat_match.revision`) and `classbeat.classes.duplicate`, and, since BC1/DU3
and BC4, `INSERT INTO beat_one_corrections` only in `classbeat.classes.duplicate` (the copied rows) and
`classbeat.classes.beat_one` (the corrections API); a new site fails CI until it is added there with a
reason.

**Tests.** Timeline fields and compare (unit and API; 404 for another user's class or revisions of
another class); duplicate: isolation (another user's class or revision 404, nothing created), hash
and not-ready refusals, flags, every quota refusal creating nothing, N parallel duplicates at a
limit of 1 giving one, the copy's provenance and music (no `song_list_id`, `music_source` rules),
nothing of item 6 copied, the idempotent replay and mismatch, `revise_draft` refused
`not_changeable` on a copy without a music source with no `class_messages` row and no job created,
a soundless class with the gate off (`feature_off`, and `soundless_not_evaluated` for an uncovered
plan, nothing created) and on (the copy and its revision stay `none`, and a revise of the copy is
answered by `music_kind.admit_class`), then a revise of the copy (fake model) and a Save of the copy leaving the
source's rows unchanged and vice versa, account export and deletion covering the copy, no request to
any fake; page tests (Vitest), and Playwright on the fakes with the axe audit of the timeline,
compare view and the duplicate dialog.

**Issues** (one PR each): HI1 #545, timeline fields and compare (backend; Sonnet Developer, Opus
review: new routes that take revision ids, tenant isolation); HI2 #546, timeline, compare view and Make current on the page with e2e (Sonnet, Sonnet;
depends on HI1); DU1 #547, duplicate (backend; Opus Developer, Opus review: class-owned data,
quotas, music ownership); DU2 #548, Duplicate on the class page and My classes with e2e (Sonnet,
Sonnet; depends on DU1 and HI2); DU3 #585, copy the beat-one corrections (backend; Opus, Opus;
depends on BC1 #553 and DU1).
