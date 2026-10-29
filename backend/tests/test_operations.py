"""External operations by a job (ADR 0005 section 7, X1): the attach of a Spotify playlist in
MOWL through the worker's allowlist opener and the vendored library, against the in-process
fake MOWL (``optools``). Never the network.

Covered: one attach then the stored link; per-kind key and request hash; the uncertain write
(timeout after MOWL accepted it) never sent again; a stale ``pending`` row from a crashed
holder; two jobs claimed at the same moment on one playlist (no row, and a ``failed`` row); a
MOWL disconnect during the call; an account switch; how each call outcome is recorded.
"""

import asyncio
import hashlib
import json
import logging
import threading
import uuid
from typing import Any

import pytest
from mowl import api as mowl_api

from classbeat import connections
from classbeat.crypto.envelope import canonical_json
from classbeat.jobs import queue
from classbeat.operations import OPERATION_KINDS, OperationKind, attach
from classbeat.operations.kinds import Reconciliation, canonical
from ctx import make_context
from optools import (
    OTHER_PLAYLIST,
    PLAYLIST,
    TOKEN_A,
    TOKEN_B,
    FakeMowlAttach,
    OpsEnv,
    first_done,
    wait_entered,
)

ATTACH = "mowl_attach_spotify_playlist"


def hash_for(source_id: str) -> bytes:
    body = {"kind": ATTACH, "source": "spotify", "source_id": source_id}
    return hashlib.sha256(canonical_json(body)).digest()


def op_events(env: OpsEnv, job_id: uuid.UUID) -> list[dict[str, Any]]:
    return [p for _, kind, p, _ in env.events(job_id) if kind == "external_operation"]


async def owner_with_job(
    env: OpsEnv, token: str = TOKEN_A
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    owner = await env.user()
    connection = await env.connect(owner, token)
    return owner, connection, env.job(owner, connection)


async def test_one_attach_stores_the_link_and_the_next_job_needs_no_write(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    owner, connection, job_id = await owner_with_job(xenv)
    await xenv.run_next()
    assert xenv.row(job_id).state == "completed"
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST)]
    [op] = xenv.operations(owner)
    assert (op.kind, op.idempotency_key, op.state, op.resolved_by) == (
        ATTACH, f"spotify:{PLAYLIST}", "succeeded", "worker"
    )  # fmt: skip
    assert bytes(op.request_hash) == hash_for(PLAYLIST)
    assert (op.external_id, op.job_id, op.lease_version) == ("1001", job_id, 1)
    assert xenv.links(owner) == [(PLAYLIST, 1001)]
    assert xenv.playlists == [1001]
    assert op_events(xenv, job_id) == [
        {"kind": ATTACH, "state": "pending"},
        {"kind": ATTACH, "state": "succeeded"},
    ]
    # Another job on the same playlist uses the link: no second write, no new row.
    second = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(second).state == "completed"
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST)]
    assert len(xenv.operations(owner)) == 1
    assert xenv.playlists == [1001, 1001]
    # No token, playlist id or MOWL id in any event or log line.
    text = json.dumps([xenv.events(j) for j in (job_id, second)]) + caplog.text
    for secret in (TOKEN_A, "1001", PLAYLIST):
        assert secret not in text


async def test_the_key_and_hash_name_only_the_write() -> None:
    assert canonical({"b": "é", "a": "1"}) == canonical_json({"b": "é", "a": "1"})
    request = attach.attach_request(PLAYLIST)
    assert request.idempotency_key == f"spotify:{PLAYLIST}"
    assert request.request_hash == hash_for(PLAYLIST)
    assert attach.attach_request(OTHER_PLAYLIST).request_hash != request.request_hash
    assert request.timeout_seconds == 120


async def test_an_uncertain_attach_fails_the_job_and_is_never_sent_again(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, connection, job_id = await owner_with_job(xenv)
    fake_attach.mode = "timeout_after_accept"
    await xenv.run_next()
    row = xenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    [op] = xenv.operations(owner)
    assert (op.state, op.external_id, op.resolved_by, op.error_category) == (
        "uncertain", None, None, "upstream_unavailable"
    )  # fmt: skip
    assert op_events(xenv, job_id) == [
        {"kind": ATTACH, "state": "pending"},
        {"kind": ATTACH, "state": "uncertain"},
    ]
    assert xenv.links(owner) == []
    # A new job on the same playlist meets the uncertain row: reconciled once (inconclusive),
    # fails the same way, and sends nothing.
    fake_attach.mode = "ok"
    again = xenv.job(owner, connection)
    await xenv.run_next()
    assert (xenv.row(again).state, xenv.row(again).error_category) == ("failed", "uncertain_write")
    assert len(fake_attach.attaches) == 1
    assert [o.state for o in xenv.operations(owner)] == ["uncertain"]


async def test_a_stale_pending_row_from_a_crashed_holder_becomes_uncertain(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    fake_attach.gate = threading.Event()
    execution = await xenv.claim()
    task = asyncio.ensure_future(xenv.execute(execution))
    await wait_entered(fake_attach)
    task.cancel()  # the worker dies mid-call
    with pytest.raises(asyncio.CancelledError):
        await task
    fake_attach.gate.set()  # MOWL completes the attach the dead worker sent
    xenv.sql(
        "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id",
        id=job_id,
    )
    assert await queue.reap(xenv.factory) == 1
    [op] = xenv.operations(owner)
    assert (op.state, op.lease_version) == ("pending", 1)
    xenv.ready(job_id)
    await xenv.run_next()
    row = xenv.row(job_id)
    assert (row.state, row.error_category, row.lease_version) == ("failed", "uncertain_write", 3)
    [op] = xenv.operations(owner)
    assert (op.state, op.lease_version) == ("uncertain", 1)
    assert await asyncio.to_thread(fake_attach.finished.wait, 10)  # the dead worker's call
    assert len(fake_attach.attaches) == 1  # never re-sent


@pytest.mark.parametrize("planted", ["none", "failed"])
async def test_two_jobs_racing_on_one_playlist_give_one_attach(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, planted: str
) -> None:
    owner = await xenv.user()
    connection = await xenv.connect(owner)
    jobs = [xenv.job(owner, connection), xenv.job(owner, connection)]
    if planted == "failed":
        xenv.sql(
            "INSERT INTO external_operations (id, owner_id, job_id, kind, idempotency_key, "
            "request_hash, state, lease_version, error_category, resolved_by, resolved_at) "
            "VALUES (gen_random_uuid(), :o, NULL, :k, :key, :h, 'failed', 3, "
            "'upstream_unavailable', 'worker', now())",
            o=owner, k=ATTACH, key=f"spotify:{PLAYLIST}", h=hash_for(PLAYLIST),
        )  # fmt: skip
    fake_attach.gate = threading.Event()
    first, second = await xenv.claim(), await xenv.claim()  # claimed at the same moment
    done, pending = await first_done(xenv.execute(first), xenv.execute(second))
    assert len(done) == 1 and len(pending) == 1  # noqa: PT018
    busy = first if first.result == "queued" else second
    holder = second if busy is first else first
    busy_row = xenv.row(busy.claimed.job_id)
    assert (busy_row.state, busy_row.attempt) == ("queued", 0)  # no attempt lost
    retries = [p for _, k, p, _ in xenv.events(busy.claimed.job_id) if k == "retry_scheduled"]
    assert [p["category"] for p in retries] == ["operation_busy"]
    fake_attach.gate.set()
    await asyncio.wait_for(next(iter(pending)), 15)
    assert xenv.row(holder.claimed.job_id).state == "completed"
    xenv.ready(busy.claimed.job_id)
    await xenv.run_next()
    for job_id in jobs:
        row = xenv.row(job_id)
        assert (row.state, row.error_category) == ("completed", None)
    assert len(fake_attach.attaches) == 1
    [op] = xenv.operations(owner)
    assert (op.state, op.external_id) == ("succeeded", "1001")
    assert xenv.links(owner) == [(PLAYLIST, 1001)]
    assert xenv.playlists == [1001, 1001]


async def test_a_disconnect_during_the_call_writes_no_link_and_ends_not_connected(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    fake_attach.gate = threading.Event()
    task = asyncio.ensure_future(xenv.run_next())
    await wait_entered(fake_attach)
    assert [o.state for o in xenv.operations(owner)] == ["pending"]
    assert await connections.disconnect(xenv.factory, make_context(owner), "mowl")
    assert xenv.operations(owner) == []
    fake_attach.gate.set()
    await asyncio.wait_for(task, 15)
    row = xenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert xenv.links(owner) == []
    assert xenv.operations(owner) == []


async def test_an_account_switch_never_reuses_the_old_link_or_external_id(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, first_connection, first_job = await owner_with_job(xenv, TOKEN_A)
    await xenv.run_next()
    assert xenv.links(owner) == [(PLAYLIST, 1001)]
    ctx = make_context(owner)
    assert await connections.disconnect(xenv.factory, ctx, "mowl")
    assert (xenv.links(owner), xenv.operations(owner)) == ([], [])
    second_connection = await xenv.connect(owner, TOKEN_B)  # another fake MOWL account
    assert second_connection != first_connection
    second_job = xenv.job(owner, second_connection)
    await xenv.run_next()
    assert xenv.row(second_job).state == "completed"
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST), (TOKEN_B, PLAYLIST)]
    [op] = xenv.operations(owner)
    assert (op.state, op.external_id, op.job_id) == ("succeeded", "2002", second_job)
    assert xenv.links(owner) == [(PLAYLIST, 2002)]
    assert xenv.row(first_job).state == "completed"


async def test_a_refused_connection_is_failed_and_the_retry_takes_the_row_over(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    fake_attach.mode = "refused"  # never left: provably no effect
    await xenv.run_next()
    row = xenv.row(job_id)
    assert (row.state, row.attempt) == ("queued", 1)  # retryable, with backoff
    [op] = xenv.operations(owner)
    assert (op.state, op.error_category, op.resolved_by) == (
        "failed", "upstream_unavailable", "worker"
    )  # fmt: skip
    fake_attach.mode = "ok"
    xenv.ready(job_id)
    await xenv.run_next()
    assert xenv.row(job_id).state == "completed"
    [op] = xenv.operations(owner)
    assert (op.state, op.lease_version, op.error_category) == ("succeeded", 3, None)
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST)]


@pytest.mark.parametrize(
    ("mode", "op_state", "job_state", "category"),
    [
        ("status:400", "failed", "failed", "internal"),
        ("status:401", "failed", "failed", "needs_reconnect"),
        ("status:503", "uncertain", "failed", "uncertain_write"),
        ("garbled", "uncertain", "failed", "uncertain_write"),
        ("timeout_after_accept", "uncertain", "failed", "uncertain_write"),
    ],
)
async def test_each_call_outcome_is_recorded_as_the_adr_says(
    xenv: OpsEnv,
    fake_attach: FakeMowlAttach,
    mode: str,
    op_state: str,
    job_state: str,
    category: str,
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    fake_attach.mode = mode
    await xenv.run_next()
    row = xenv.row(job_id)
    assert (row.state, row.error_category) == (job_state, category)
    [op] = xenv.operations(owner)
    assert op.state == op_state
    assert xenv.links(owner) == []
    if mode == "status:401":
        [info] = await connections.list_connections(xenv.factory, make_context(owner))
        assert info.status == "needs_reconnect"


async def test_a_host_off_the_allowlist_is_refused_and_recorded_failed(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    monkeypatch.setattr(mowl_api, "API", "https://evil.example/")
    await xenv.run_next()
    row = xenv.row(job_id)
    assert (row.state, row.error_category) == ("failed", "outbound_blocked")
    [op] = xenv.operations(owner)
    assert (op.state, op.error_category) == ("failed", "outbound_blocked")
    assert fake_attach.requests == []


async def test_another_request_hash_on_the_same_key_is_internal(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    xenv.sql(
        "INSERT INTO external_operations (id, owner_id, kind, idempotency_key, request_hash, "
        "state, lease_version) VALUES (gen_random_uuid(), :o, :k, :key, :h, 'failed', 0)",
        o=owner, k=ATTACH, key=f"spotify:{PLAYLIST}", h=hash_for(OTHER_PLAYLIST),
    )  # fmt: skip
    await xenv.run_next()
    assert (xenv.row(job_id).state, xenv.row(job_id).error_category) == ("failed", "internal")
    assert fake_attach.requests == []


async def test_a_job_whose_connection_is_not_the_users_makes_no_row(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await xenv.user()
    await xenv.connect(owner)
    job_id = xenv.job(owner, uuid.uuid4())
    await xenv.run_next()
    assert (xenv.row(job_id).state, xenv.row(job_id).error_category) == ("failed", "not_connected")
    assert xenv.operations(owner) == []
    assert fake_attach.requests == []


async def test_an_invalid_spotify_id_is_invalid_input(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await xenv.user()
    connection = await xenv.connect(owner)
    job_id = xenv.job(owner, connection, source_id="not-a-spotify-id")
    await xenv.run_next()
    assert xenv.row(job_id).error_category == "invalid_input"
    assert fake_attach.requests == []


async def test_a_reconciler_that_finds_the_effect_is_used_by_the_next_job(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, connection, job_id = await owner_with_job(xenv)
    fake_attach.mode = "timeout_after_accept"
    await xenv.run_next()
    assert xenv.row(job_id).error_category == "uncertain_write"

    async def found(_: object) -> Reconciliation:
        return Reconciliation("succeeded", "4242")

    original = OPERATION_KINDS.replace(OperationKind(ATTACH, found, attach.ATTACH.on_succeeded))
    try:
        again = xenv.job(owner, connection)
        await xenv.run_next()
    finally:
        assert original is not None
        OPERATION_KINDS.replace(original)
    assert xenv.row(again).state == "completed"
    [op] = xenv.operations(owner)
    assert (op.state, op.external_id, op.resolved_by) == ("succeeded", "4242", "worker")
    assert xenv.links(owner) == [(PLAYLIST, 4242)]
    assert len(fake_attach.attaches) == 1
