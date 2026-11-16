"""The MOWL output of a studio class: attach the merge-published mix (ADR 0036 section 6, SR C7).

Only after the mix is ``done`` (its URL stored). With the user's MOWL connection (``not_connected``
otherwise), and only when no attach operation is open yet:

1. **Poll** the user's MOWL mix listing (read-only) with backoff (``POLL_SECONDS``, about 30
   minutes in all), a checkpoint before every read;
2. **attach only** the one entry whose ``URL`` has the stored URL's S3 object key **and** whose
   duration is within tolerance of the composed mix (``mowl_match_policy.attachable``), **never by
   name**: one ``PUT`` (``mowl.api.attach_swedebeat``) as the external operation
   ``mowl_attach_studio_mix`` (key ``out:...:mowl:<n>:attach``), whose ``PlaylistID`` is stored;
3. nothing matched after the last poll: the output is ``published_not_listed`` with **Check
   again**. The mix is never published again.
"""

import asyncio
from typing import Any, Final

from mowl import api as mowl_api

from classbeat.connections import OpenConnection, call_opened, hold, open_connection
from classbeat.jobs import JobFailure
from classbeat.operations import CallResult, perform
from classbeat.operations.attach import answered_playlist_id
from classbeat.operations.requests import Guard
from classbeat.studio import mowl_match_policy
from classbeat.studio.output_key_policy import parse
from classbeat.swedebeat_studio import outputs_ops
from classbeat.swedebeat_studio.outputs_attempt import mark
from classbeat.swedebeat_studio.outputs_job import Producer
from classbeat.swedebeat_studio.outputs_rows import OutputRow, step_keys

# Waits before each listing read: about 30 minutes in all.
POLL_SECONDS: Final = (0.0, 15.0, 30.0, 60.0, 120.0, 240.0, 300.0, 300.0, 300.0, 300.0, 120.0)


async def _open(p: Producer) -> OpenConnection:
    connection_id = p.run.ctx.connection("mowl")
    if connection_id is None:
        raise JobFailure("not_connected")
    opened = await open_connection(p.run.factory, p.keyring, p.run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    return opened


async def listing(p: Producer, opened: OpenConnection) -> list[mowl_match_policy.Listed]:
    answer = await call_opened(
        p.run.factory, p.run.ctx, opened, lambda creds: mowl_api.swedebeat_mixes(creds)
    )
    return mowl_match_policy.entries(answer)


def _guard(p: Producer, opened: OpenConnection) -> Guard:
    approved = p.still_approved()

    async def guard(db: Any) -> bool:
        return await hold(db, p.run.ctx, opened) and await approved(db)

    return guard


async def attach(p: Producer, row: OutputRow) -> None:
    url, duration = row.detail.get("mix_url"), row.detail.get("duration_sec")
    if not isinstance(url, str) or not isinstance(duration, int | float):
        raise JobFailure("internal")
    okey = parse(step_keys(row)["attach"])
    if okey is None:  # pragma: no cover - built by the same policy
        raise JobFailure("internal")
    opened = await _open(p)
    mix_id = row.detail.get("mix_id")
    if not isinstance(mix_id, str):
        found = None
        for wait in POLL_SECONDS:
            if wait:
                await asyncio.sleep(wait)
            await p.run.checkpoint()
            found = mowl_match_policy.attachable(await listing(p, opened), url, float(duration))
            if found is not None:
                break
        if found is None:
            await mark(p, row, "published_not_listed")
            return
        mix_id = found.mix_id
        await mark(p, row, "running", mix_id=mix_id)  # stored before the write intent
    request = outputs_ops.output_request(
        okey,
        p.draft.content_hash,
        {"mix_url": url, "mix_id": mix_id},
        timeout_seconds=outputs_ops.ATTACH_SECONDS,
        guard=_guard(p, opened),
        context={"mix_id": mix_id},
    )

    async def call() -> CallResult:
        answer = await call_opened(
            p.run.factory,
            p.run.ctx,
            opened,
            lambda creds: mowl_api.attach_swedebeat(creds, mix_id),
            single_request=True,  # one PUT: a refusal proves it had no effect
        )
        return CallResult(str(answered_playlist_id(answer)))

    await perform(p.run, request, call, keyring=p.keyring)
