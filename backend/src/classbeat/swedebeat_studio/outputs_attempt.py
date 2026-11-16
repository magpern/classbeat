"""An output's current attempt in a ``produce_outputs`` run (ADR 0036 section 6, SR C7).

``current(p, output, detail)`` is the attempt this run works on, in one fenced transaction with
the row locked:

- none yet: attempt 1 is opened with ``detail()``, everything that would otherwise vary between
  runs (the names with the mix's random suffix, the body, the render plan, the analysed sha256s),
  **stored before the first write intent**;
- the newest one marked ``superseded`` by the user's **Create another anyway**: the next attempt is
  opened the same way, with new names and therefore new keys; the old row stays on record;
- otherwise the newest one, as stored: a resumed output continues from its stored ids.

It answers ``None`` when nothing may be written to that output now: the attempt is ``done``, or
**in doubt** (an operation ``uncertain``, or ``pending`` with no running holder): an output in
doubt is never retried by a job and blocks every new write to it until the user's Check again or
Create another anyway (the row is marked ``in_doubt``). Opening an attempt takes the limits
(``outputs_rows.check_limits``: ``output_limit``).

``settle(p, row, failure)`` records the attempt's state after its steps, derived from its
operations (``output_state_policy.derived``) with the failure's category when there is one.
"""

from collections.abc import Callable, Mapping
from typing import Any

from classbeat.jobs import JobFailure
from classbeat.studio.output_state_policy import derived
from classbeat.swedebeat_studio import outputs_rows
from classbeat.swedebeat_studio.outputs_job import Producer
from classbeat.swedebeat_studio.outputs_rows import OutputRow


async def current(
    p: Producer, output: str, detail: Callable[[], Mapping[str, Any]]
) -> OutputRow | None:
    owner, given = p.owner_id, p.given
    async with p.run.fenced() as fs:
        row = await outputs_rows.latest(
            fs.db, owner, p.class_id, given.revision_id, output, lock=True
        )
        if row is None or row.detail.get("superseded") is True:
            attempt = 1 if row is None else row.attempt + 1
            return await outputs_rows.open_attempt(
                fs.db, owner, p.class_id, given.revision_id, output, attempt, detail()
            )
        steps = await outputs_rows.operations(fs.db, owner, row)
        state = derived(output, steps, row.state, job_open=True)
        if state == "in_doubt":
            if row.state != "in_doubt":
                await outputs_rows.update(fs.db, owner, row.id, state="in_doubt")
            return None
        if state == "done":
            if row.state != "done":
                await outputs_rows.update(fs.db, owner, row.id, state="done")
            return None
        if row.state == "published_not_listed":
            return None  # the mix is never published again; the user's Check again looks
        await outputs_rows.update(fs.db, owner, row.id, state="running")
        return row


async def settle(p: Producer, row: OutputRow, failure: JobFailure | None) -> str:
    """Store the attempt's state after its steps; returns it."""
    async with p.run.fenced() as fs:
        now = await outputs_rows.latest(
            fs.db, p.owner_id, row.class_id, row.revision_id, row.output, lock=True
        )
        if now is None or now.id != row.id:
            raise JobFailure("internal")
        steps = await outputs_rows.operations(fs.db, p.owner_id, now)
        stored = now.state if failure is None else "failed"
        if failure is not None and failure.category == "uncertain_write":
            stored = "in_doubt"
        state = derived(row.output, steps, stored, job_open=failure is None)
        patch = {} if failure is None else {"category": failure.category}
        await outputs_rows.update(fs.db, p.owner_id, row.id, state=state, patch=patch)
    return state


async def mark(p: Producer, row: OutputRow, state: str, **patch: Any) -> None:
    async with p.run.fenced() as fs:
        await outputs_rows.update(fs.db, p.owner_id, row.id, state=state, patch=patch)
