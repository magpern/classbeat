"""The user's view of a studio class's outputs and their actions on one (ADR 0036 section 6,
ADR 0005 section 7; SR C7). User actions in the web process on the caller's own rows (RLS as
``ctx.user_id`` plus ``owner_id``): another user's class answers ``not_found``, like an unknown one.

- ``view``: per draft and output the newest attempt with its state **derived from its
  operations** (``output_state_policy``), the safe actions, a note code, the stored name, the
  failure's category and, for a mix in doubt, the MOWL entry found by its unique name; and
  ``enabled_outputs``, the outputs this environment runs for the user
  (``CLASSBEAT_SWEDEBEAT_STUDIO``, ``spotify_playlist`` with song picking) in ``OUTPUTS`` order:
  the ones the page may offer. Never a key, a catalogue reference, a SwedeBeat or Spotify id, a
  URL or a body.
- ``check_again`` (ADR 0005's reconciler, once more): an orphaned ``pending`` step (no running
  holder) is first marked ``uncertain``; then each ``uncertain`` step through its kind's
  reconciler (a publish via every page of ``GET /playlists/shared``; a create stays in doubt; a
  Spotify write by its ``ref``), recorded conditional on ``state = 'uncertain'``. A **mix** in
  doubt is looked up in the user's MOWL listing by its unique name: a hit is **shown**
  (``candidate``), never taken; without MOWL it stays in doubt. A MOWL output
  ``published_not_listed`` reads the listing again: an entry with the URL's object key and the
  duration makes it ``pending``, so the next approval attaches it (nothing is attached here).
- ``confirm``: the user's decision that the shown entry is their mix: the merge step
  ``succeeded`` with that entry's URL, the output ``done``.
- ``create_another`` (ADR 0005's **Do it anyway**, as ``attempt + 1``): only for an output in
  doubt, with the duplicate risk acknowledged; the attempt is marked ``superseded`` and stays on
  record (its operations untouched), and the next ``produce_outputs`` opens the next attempt with
  new names and new keys. Idempotent: a superseded attempt answers as it is.

Only codes are returned; nothing is logged but categories.
"""

from typing import Any, Final
from uuid import UUID

from mowl import api as mowl_api
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import ConnectionFailure, call_opened, open_connection
from classbeat.context import UserContext, require_context
from classbeat.crypto.keks import KeyRing
from classbeat.db import user_session
from classbeat.egress import OutboundBlocked
from classbeat.operations import OPERATION_KINDS, ReconcileInput
from classbeat.operations.protocol import RESOLVE, operation_of, reconcile, succeeded
from classbeat.operations.requests import NOT_ACTIONABLE_SQL
from classbeat.studio import mowl_match_policy
from classbeat.studio.output_choice_policy import OUTPUTS
from classbeat.studio.output_state_policy import actions, derived, note
from classbeat.swedebeat_studio import outputs_rows
from classbeat.swedebeat_studio.admit import studio_on
from classbeat.swedebeat_studio.outputs_admit import enabled
from classbeat.swedebeat_studio.outputs_ops import url_digest
from classbeat.swedebeat_studio.outputs_rows import OutputRow

Factory = Any
SHOWN: Final = ("name", "category", "superseded")
_CLASS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_OPEN_JOB = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'produce_outputs' AND state IN ('queued', 'running') LIMIT 1"
)
_COLUMNS = (
    "id, kind, idempotency_key, state, external_id, result, job_id, lease_version, class_id, "
    f"request_context, pending_at, call_ended_at, {NOT_ACTIONABLE_SQL} AS waiting"
)
_STEP_ROWS = text(
    f"SELECT {_COLUMNS} FROM external_operations "  # noqa: S608 - constant column list
    "WHERE owner_id = :owner_id AND class_id = :class_id AND idempotency_key = :key"
)
# An orphaned write intent (its holder is no longer running under that token) may have been
# sent: it becomes ``uncertain``, as ``operations.orphans`` does for the class writes.
_ORPHAN = text(
    "UPDATE external_operations o SET state = 'uncertain', updated_at = now() "
    "WHERE o.owner_id = :owner_id AND o.class_id = :class_id AND o.idempotency_key = :key "
    "AND o.state = 'pending' AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.owner_id = o.owner_id "
    "AND j.id = o.job_id AND j.state = 'running' AND j.lease_version = o.lease_version)"
)


async def _class(db: AsyncSession, ctx: UserContext, class_id: UUID) -> bool:
    params = {"owner_id": ctx.user_id, "class_id": class_id}
    return (await db.execute(_CLASS, params)).first() is not None


async def _shown(db: AsyncSession, ctx: UserContext, row: OutputRow) -> dict[str, Any]:
    steps = await outputs_rows.operations(db, ctx.user_id, row)
    params = {"owner_id": ctx.user_id, "class_id": row.class_id}
    job_open = (await db.execute(_OPEN_JOB, params)).first() is not None
    state = derived(row.output, steps, row.state, job_open=job_open)
    detail = row.detail
    candidate = detail.get("candidate") if isinstance(detail.get("candidate"), dict) else None
    done = [s for s, (st, _) in steps.items() if st == "succeeded"]
    item: dict[str, Any] = {
        "revision_id": str(row.revision_id),
        "output": row.output,
        "attempt": row.attempt,
        "state": state,
        "actions": ["retry"]
        if detail.get("superseded")
        else actions(state, candidate=bool(candidate)),
        "note": note(row.output, state, done, detail),
        **{k: detail.get(k) for k in SHOWN if detail.get(k) is not None},
    }
    if candidate is not None and state == "in_doubt":
        item["candidate"] = {k: candidate.get(k) for k in ("name", "duration_sec")}
    return item


async def view(factory: Factory, ctx: UserContext, class_id: UUID) -> dict[str, Any] | str:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if not await studio_on(db):
            return "feature_off"
        if not await _class(db, ctx, class_id):
            return "not_found"
        rows = await outputs_rows.all_latest(db, ctx.user_id, class_id)
        offered = await enabled(db, ctx)
        return {
            "enabled_outputs": [o for o in OUTPUTS if o in offered],
            "outputs": [await _shown(db, ctx, r) for r in rows],
        }


async def _row(
    db: AsyncSession, ctx: UserContext, class_id: UUID, revision_id: UUID, output: str
) -> OutputRow | str:
    if not await studio_on(db):
        return "feature_off"
    if not await _class(db, ctx, class_id):
        return "not_found"
    found = await outputs_rows.latest(db, ctx.user_id, class_id, revision_id, output, lock=True)
    return "not_found" if found is None else found


async def _mowl_listing(
    factory: Factory, keyring: KeyRing | None, ctx: UserContext
) -> list[mowl_match_policy.Listed] | None:
    if keyring is None or ctx.connection("mowl") is None:
        return None
    try:
        opened = await open_connection(factory, keyring, ctx, "mowl")
        answer = await call_opened(
            factory, ctx, opened, lambda creds: mowl_api.swedebeat_mixes(creds)
        )
    except (ConnectionFailure, OutboundBlocked):
        return None
    return mowl_match_policy.entries(answer)


async def _reconcile_steps(
    factory: Factory, keyring: KeyRing | None, ctx: UserContext, row: OutputRow
) -> None:
    for step, key in outputs_rows.step_keys(row).items():
        params = {"owner_id": ctx.user_id, "class_id": row.class_id, "key": key}
        async with user_session(factory, ctx) as db:
            await db.execute(_ORPHAN, params)
            found = (await db.execute(_STEP_ROWS, params)).first()
        if found is None or found.state != "uncertain" or found.waiting or step == "merge":
            continue
        kind = OPERATION_KINDS.require(found.kind)
        read = operation_of(found)
        outcome = await reconcile(kind, ReconcileInput(factory, keyring, ctx, read))
        if outcome.outcome == "inconclusive":
            continue
        ids = {"id": found.id, "owner_id": ctx.user_id}
        async with user_session(factory, ctx) as db:
            moved = await db.execute(
                RESOLVE,
                {**ids, "state": outcome.outcome, "external_id": outcome.external_id,
                 "by": "user", "pending_at": read.pending_at},
            )  # fmt: skip
            if moved.first() is not None and outcome.outcome == "succeeded":
                now = (await db.execute(_STEP_ROWS, params)).one()
                await succeeded(db, kind, ctx.user_id, operation_of(now))


async def _store_state(
    factory: Factory, ctx: UserContext, row: OutputRow, **patch: Any
) -> dict[str, Any] | str:
    async with user_session(factory, ctx) as db:
        now = await _row(db, ctx, row.class_id, row.revision_id, row.output)
        if isinstance(now, str):
            return now
        steps = await outputs_rows.operations(db, ctx.user_id, now)
        # Settled again from the operations alone: in doubt only while one still is.
        base = (
            "failed" if now.state == "in_doubt" and not now.detail.get("superseded") else now.state
        )
        state = derived(now.output, steps, base, job_open=False)
        await outputs_rows.update(db, ctx.user_id, now.id, state=state, patch=patch)
        moved = await outputs_rows.latest(
            db, ctx.user_id, now.class_id, now.revision_id, now.output
        )
        assert moved is not None  # noqa: S101 - locked above
        return await _shown(db, ctx, moved)


async def check_again(
    factory: Factory,
    keyring: KeyRing | None,
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    output: str,
) -> dict[str, Any] | str:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        row = await _row(db, ctx, class_id, revision_id, output)
    if isinstance(row, str):
        return row
    if row.state == "published_not_listed":
        listed = await _mowl_listing(factory, keyring, ctx)
        url, duration = row.detail.get("mix_url"), row.detail.get("duration_sec")
        found = None
        if listed is not None and isinstance(url, str) and isinstance(duration, int | float):
            found = mowl_match_policy.attachable(listed, url, float(duration))
        if found is None:
            return await _store_state(factory, ctx, row)
        async with user_session(factory, ctx) as db:
            await outputs_rows.update(db, ctx.user_id, row.id, state="pending")
        return await _store_state(factory, ctx, row)
    await _reconcile_steps(factory, keyring, ctx, row)
    patch: dict[str, Any] = {}
    if output == "swedebeat_mix" and isinstance(row.detail.get("name"), str):
        listed = await _mowl_listing(factory, keyring, ctx)
        named = [] if listed is None else mowl_match_policy.named(listed, row.detail["name"])
        if len(named) == 1:  # shown for the user's decision, never taken
            hit = named[0]
            patch["candidate"] = {
                "name": hit.name,
                "url": hit.url,
                "duration_sec": hit.duration_sec,
            }
    return await _store_state(factory, ctx, row, **patch)


async def confirm(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID
) -> dict[str, Any] | str:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        row = await _row(db, ctx, class_id, revision_id, "swedebeat_mix")
        if isinstance(row, str):
            return row
        candidate = row.detail.get("candidate")
        url = candidate.get("url") if isinstance(candidate, dict) else None
        params = {"owner_id": ctx.user_id, "class_id": class_id,
                  "key": outputs_rows.step_keys(row)["merge"]}  # fmt: skip
        found = (await db.execute(_STEP_ROWS, params)).first()
        if not isinstance(url, str) or found is None or found.state != "uncertain":
            return "nothing_to_confirm"
        moved = await db.execute(
            RESOLVE,
            {"id": found.id, "owner_id": ctx.user_id, "state": "succeeded",
             "external_id": url_digest(url), "by": "user", "pending_at": found.pending_at},
        )  # fmt: skip
        if moved.first() is None:
            return "nothing_to_confirm"
        await outputs_rows.update(
            db, ctx.user_id, row.id, state="done", external_id=url,
            patch={"mix_url": url, "confirmed": True},
        )  # fmt: skip
        settled = await outputs_rows.latest(db, ctx.user_id, class_id, revision_id, row.output)
        return await _shown(db, ctx, settled or row)


async def create_another(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID, output: str
) -> dict[str, Any] | str:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        row = await _row(db, ctx, class_id, revision_id, output)
        if isinstance(row, str):
            return row
        for key in outputs_rows.step_keys(row).values():
            await db.execute(_ORPHAN, {"owner_id": ctx.user_id, "class_id": class_id, "key": key})
        if row.detail.get("superseded") is not True:
            steps = await outputs_rows.operations(db, ctx.user_id, row)
            if derived(row.output, steps, row.state, job_open=False) != "in_doubt":
                return "not_in_doubt"
            await outputs_rows.update(
                db, ctx.user_id, row.id, state="in_doubt", patch={"superseded": True}
            )
        moved = await outputs_rows.latest(db, ctx.user_id, class_id, revision_id, output)
        assert moved is not None  # noqa: S101 - locked above
        return await _shown(db, ctx, moved)
