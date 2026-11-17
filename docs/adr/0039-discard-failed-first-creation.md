# ADR 0039: a class whose first creation failed is discarded; its ended job stays readable

Status: Proposed (owner decision 2026-11-17: "If the first design failed, why save it?"; subject to
review). Amends ADR 0029 section 5 (the hand-off), ADR 0005 section 1 (`jobs.class_id`) and its check
`jobs_class_id` as ADR 0034 section 5 (migration 0052, `player_remove`) and ADR 0036 section 9
(migration 0056, `spotify_sync`) left it.

Related: ADR 0005 sections 2, 3 and 7, ADR 0007 sections 8, 10 and 11, ADR 0011 section 3, ADR 0029
section 5.

## Context

`POST /api/jobs` with `interpret_request` inserts the class row with its job (ADR 0029 section 5);
the browser goes to `/classes/{id}`, and once the reading is stored the page asks for the first design
(`design_draft`, or `design_with_songs` when songs are picked). When the reading or that first design
fails, the class stays forever as an empty "No version yet" row on Home and My classes; only the user's
own delete removes it.

The user must still be told why it stopped and be able to try again with the same words (the browser
keeps the request per class id, `rememberRequest`/`recallRequest`). `jobs.class_id` cascades from
`classes`, so deleting the class also deletes the job and its events: the page would lose the reason
and turn into a bare 404.

## Decision

1. **What is discarded (an allowlist, `classbeat.jobs.discard_policy`).** An ending job `J` of state
   `failed` or `cancelled` discards its class only when, read under the class row lock:
   - `J` is the class's `interpret_request`, the class has no other job and no stored reading
     (`classes.interpretation`); or `J` is a
     `design_draft` or `design_with_songs` whose idempotency key is the hand-off key of the class's one
     other job, a **completed** `interpret_request` (so `J` is that reading's own first design);
   - the class has no current revision, no approved Swedebeat match and **no revision of any kind**
     (a draft, a song list waiting or approved);
   - no row of the class in `saved_workouts`, `external_operations`, `class_playlists`,
     `swedebeat_handoffs`, `beat_one_corrections`, `class_outputs`, `spotify_syncs` or
     `player_publications`; no `external_operations` or `mail_handoff` row of the class's other job
     (which goes with the class); and no `mail_handoff` row of `J` nor an `external_operations` row of
     `J` still `pending` or `uncertain` (a finished one, such as the music stage's MOWL attach, stays
     with the detached job);
   - `J`'s credit is not `committed`.
   Anything else keeps the class: another kind or ending (`expired` needs a song list), a second design
   attempt, a design made from the class form, a retry, an uncertain write (ADR 0005 section 7 keeps
   blocking). `tests/test_jobs_discard_schema.py` fails when a table that names a class or a job is in
   neither list, so a new table is decided in its own PR.
2. **Where (`classbeat.jobs.discard.discard_first_creation`).** In the transaction that ends the job,
   while that job row is locked: the worker's fenced terminal transition (`transitions._terminal`, so
   exactly once and never by a stale lease) and the user's cancel of a queued job
   (`jobs.service.cancel`). It locks the class row (`LOCK_CLASS`, as the class delete does) before it
   reads the facts, so no job, revision or operation of the class can be inserted meanwhile (their
   foreign keys wait on the lock) and what committed before is seen. The lock order is job, then class;
   the class delete takes the class first but refuses `class_busy` while the job is still open, so it
   never waits on the job row. A replay finds no class and does nothing.
3. **The ended job is kept, detached.** `jobs.discarded_class_id` (migration 0058) takes the class id,
   `class_id` becomes NULL (`jobs_class_id` allows it for such a job, `jobs_discarded` holds the shape),
   `input` is emptied (no request text outlives the class, ADR 0007 section 11) and the idempotency key
   is replaced by a fresh one, so the start screen's same key starts a new class instead of replaying the
   discarded one. Then the class is deleted with the class delete's own statement (`DELETE_CLASS`); the
   cascade removes the completed reading, the messages and the interpretation.
4. **What the user sees.** `GET /api/jobs/{id}` still answers the job (its state, category, sentence and
   events) and names the class id it had, so the job page's "Try again" opens the start screen with the
   request kept for that id. `GET /api/classes/{id}` of a discarded class answers 404 `class_discarded`
   with the job's id (only to its owner; anyone else gets the plain `not_found`). The class page shows
   "Class not kept", the job's failure text or "The design was stopped", and "Try again with the same
   request" when the tab kept it. A page watching the reading switches to it on its next poll.
5. **Credit and usage.** Unchanged: the reading takes no credit; a first design keeps its `reserved`
   credit on the detached job and the settle step releases it once, like any ended job (ADR 0011 section
   3). `agent_runs` keep their rows (ADR 0007 section 10): the design's stay linked, the reading's lose
   their job id (`SET NULL`), as on any class delete.

## The composer flow (ADR 0038)

[ADR 0038](0038-composer-flow.md) (roadmap Amendment 29) adds a composer behind the flag
`composer_flow`, off by default until the owner's live check. Its implementation (#1185, #1186, #1188 to
#1191) is not on `main` yet: on 2026-11-17 `main` holds only that ADR, with no composer code, migration or
flag. What follows is how this rule, as the code stands, applies to the flow ADR 0038 describes.

- **A failed or stopped first composer reading** is rule (a): the class goes when the reading was its only
  job and stored no interpretation.
- **A failed or stopped first sketch** is rule (b), *if* the sketch is the `design_draft` the ADR 0029
  section 5 hand-off enqueues (ADR 0038 section 4) with the hand-off key `interpret-<reading id hex>`, and no
  revision exists yet. Then the class goes, and its credit is released by settle as for any failed design.
  If the sketch job carries any other key, the check `readings == 1` fails and the class is **kept**.
- **Anything after a sketch exists keeps the class.** The sketch is a revision (`origin = "sketch"`), so the
  `revisions == 0` rule refuses every later ending: a failed fill, swap, change or phase 2. The fill's own key
  (a hash of the class, the sketch and an attempt, ADR 0038 section 5) never matches the hand-off key either.
- **Chat messages are not looked at today.** `class_messages` is in `DISCARDED_WITH`: a discarded class's
  messages go with it, whatever they say and whatever their state. The composer stores a message the user
  sends while a job runs as `pending` (ADR 0038 section 8, storage in #1186). Today, a first sketch that fails
  with such a message waiting would discard the class and that message with it.
- **The implementers must re-check this rule**, in the PR that adds pending messages at the latest: either
  keep a class with any message beyond the request (or any `pending` one), or show the user the message
  that goes. New columns on `classes` (the fill `attempt`, `save_pending`) are not seen by
  `tests/test_jobs_discard_schema.py`, which lists tables that name a class or a job; they only exist after
  a sketch, which already keeps the class. A new table that names a class fails that test until it is
  listed. After merging `main` (2026-11-17) there is none: `main` adds no migration after 0057.
- Keeping is the safe default: every mismatch above keeps the class, and the user can delete it by hand.

## Alternatives

- **Delete the class and its job.** The reason is gone with the job, and the page has nothing to show.
- **A separate tombstone table.** A new owned table, its policy, export and retention entries, for what
  the job row already holds.
- **Hide the shell from the lists and keep it.** The row and the request stay forever, and every list
  and count must learn the filter.
- **Discard in the settle step.** The reading takes no credit and never reaches it; settle runs after
  the ending is visible, so a watching page would race the removal.

## Consequences

- A job the reaper ends (`lease_lost` after the last attempt, or a queued row with spent attempts) is
  ended in SQL outside these transactions, so its class stays as before; the user's delete still
  removes it. This is rare (three lost leases) and safe (nothing is removed).
- A refused reading (`not_connected`, `out_of_scope`) completes, so it is not a failure and its class
  stays with its "Interpret again" and "Try again" offers.
- Detached jobs live until the account goes, like the class-less `player_remove` and `spotify_sync`
  jobs (migrations 0052 and 0056); they hold no content.
- The downgrade of 0058 deletes the detached jobs.
