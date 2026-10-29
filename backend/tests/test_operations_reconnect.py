"""External operations, review round 2 of #113 (ADR 0005 section 7, ADR 0006 section 3):

- an allowlist refusal on a redirect hop comes after MOWL got the request: ``uncertain``, never
  ``failed``, and nothing is re-sent; only a refusal of the first request is ``failed``;
- reconnecting MOWL in place (same row, no disconnect) forgets the old account's links and
  attach operations, as a disconnect then connect does;
- the connection guard of step 1 stops a call after an in-place reconnect or a status flip;
- the conditions of the "treat as uncertain" and "record the outcome" statements.

Fake MOWL only (``optools``)."""

import asyncio
import io
import json
import urllib.request
import urllib.response
import uuid
from email.message import Message
from typing import Any

import pytest

from classbeat.db import user_session
from classbeat.egress import WORKER_ALLOWLIST, OutboundBlocked, build_allowlist_opener
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry
from classbeat.jobs.runner import RunConfig, execute
from classbeat.operations import CallResult, OperationRequest, attach, perform
from classbeat.operations.protocol import _MARK_UNCERTAIN, _RECORD  # pyright: ignore[reportPrivateUsage]
from ctx import make_context
from optools import KIND, PLAYLIST, TOKEN_A, TOKEN_B, AttachInput, FakeMowlAttach, OpsEnv
from test_operations import ATTACH, hash_for, owner_with_job


@pytest.mark.parametrize(
    ("location", "category"),
    [
        # Another host: refused by the allowlist's redirect handler, after the PUT was sent.
        ("https://api.anthropic.com/v1/x", "outbound_blocked"),
        ("https://evil.example/v1/x", "outbound_blocked"),
        # The same host: urllib does not redirect a PUT at all (an HTTP 302 error).
        ("http://api.mowl.com/v1/x", "internal"),
    ],
)
async def test_a_refused_redirect_after_mowl_accepted_is_uncertain_and_never_resent(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, location: str, category: str
) -> None:
    owner, connection, job_id = await owner_with_job(xenv)
    fake_attach.mode = f"redirect:{location}"
    await xenv.run_next()
    assert fake_attach.attaches == [(TOKEN_A, PLAYLIST)]
    assert (xenv.row(job_id).state, xenv.row(job_id).error_category) == (
        "failed", "uncertain_write"
    )  # fmt: skip
    [op] = xenv.operations(owner)
    assert (op.state, op.error_category) == ("uncertain", category)
    fake_attach.mode = "ok"
    again = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(again).error_category == "uncertain_write"
    assert len(fake_attach.attaches) == 1  # never re-sent
    assert xenv.links(owner) == []


class Redirecting(urllib.request.BaseHandler):
    """Answers every ``https`` request with a 302 to ``location`` (no network)."""

    def __init__(self, location: str) -> None:
        self.location = location
        self.seen: list[str] = []

    def https_open(self, req: urllib.request.Request) -> Any:
        self.seen.append(req.full_url)
        headers = Message()
        headers["Location"] = self.location
        response = urllib.response.addinfourl(io.BytesIO(b"{}"), headers, req.full_url, 302)
        response.msg = "fake"  # pyright: ignore[reportAttributeAccessIssue]
        return response


@pytest.mark.parametrize(
    "location",
    ["https://api.anthropic.com/x", "http://api.mowl.com/x", "https://api.mowl.com:8443/x"],
)
def test_only_a_redirect_hop_refusal_says_the_request_was_sent(location: str) -> None:
    handler = Redirecting(location)
    opener = build_allowlist_opener(WORKER_ALLOWLIST, https_handler=handler)
    with pytest.raises(OutboundBlocked) as raised:
        opener.open("https://api.mowl.com/v1/x")
    assert raised.value.after_request is True
    assert handler.seen == ["https://api.mowl.com/v1/x"]  # the first request went out
    with pytest.raises(OutboundBlocked) as first:
        opener.open("https://evil.example/v1/x")
    assert first.value.after_request is False  # refused before anything was sent
    assert handler.seen == ["https://api.mowl.com/v1/x"]


async def attached(xenv: OpsEnv, token: str = TOKEN_A) -> tuple[uuid.UUID, uuid.UUID]:
    owner, connection, _ = await owner_with_job(xenv, token)
    await xenv.run_next()
    assert len(xenv.links(owner)) == 1
    return owner, connection


@pytest.mark.parametrize("token", [TOKEN_B, TOKEN_A])
async def test_a_reconnect_in_place_forgets_the_mowl_links_and_attaches(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, token: str
) -> None:
    owner, connection = await attached(xenv)
    other, _ = await attached(xenv)
    # No disconnect: the same row is rewritten (maybe as another MOWL account). The row records
    # no MOWL account identity, so even the same account forgets (a new attach at worst).
    again = await xenv.connect(owner, token)
    assert again == connection
    assert (xenv.links(owner), xenv.operations(owner)) == ([], [])
    assert len(xenv.links(other)) == len(xenv.operations(other)) == 1  # untouched
    job_id = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(job_id).state == "completed"
    assert fake_attach.attaches[-1] == (token, PLAYLIST)
    [op] = xenv.operations(owner)
    [(_, playlist)] = xenv.links(owner)
    assert op.external_id == str(playlist)
    assert xenv.playlists[-1] == playlist != xenv.playlists[0]


async def test_the_guard_stops_a_call_after_a_reconnect_in_place_before_step_one(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    real_open = attach.open_connection

    async def open_then_reconnect(*args: Any, **kwargs: Any) -> Any:
        opened = await real_open(*args, **kwargs)
        assert await xenv.connect(owner, TOKEN_B) == opened.id  # same row, new data key
        return opened

    monkeypatch.setattr(attach, "open_connection", open_then_reconnect)
    await xenv.run_next()
    assert (xenv.row(job_id).state, xenv.row(job_id).error_category) == ("failed", "not_connected")
    assert fake_attach.requests == []
    assert xenv.operations(owner) == []


async def test_the_guard_stops_a_call_after_the_connection_needs_reconnect(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _, job_id = await owner_with_job(xenv)
    real_open = attach.open_connection

    async def open_then_flip(*args: Any, **kwargs: Any) -> Any:
        opened = await real_open(*args, **kwargs)
        xenv.sql("UPDATE connections SET status = 'needs_reconnect' WHERE owner_id = :o", o=owner)
        return opened

    monkeypatch.setattr(attach, "open_connection", open_then_flip)
    await xenv.run_next()
    assert (xenv.row(job_id).state, xenv.row(job_id).error_category) == ("failed", "not_connected")
    assert fake_attach.requests == []


async def plant(xenv: OpsEnv, owner: uuid.UUID, job_id: uuid.UUID, state: str) -> uuid.UUID:
    operation = uuid.uuid4()
    xenv.sql(
        "INSERT INTO external_operations (id, owner_id, job_id, kind, idempotency_key, "
        "request_hash, state, lease_version) VALUES (:id, :o, :j, :k, :key, :h, :s, 1)",
        id=operation, o=owner, j=job_id, k=ATTACH, key=f"spotify:{PLAYLIST}",
        h=hash_for(PLAYLIST), s=state,
    )  # fmt: skip
    return operation


async def matched(xenv: OpsEnv, owner: uuid.UUID, statement: Any, **params: Any) -> bool:
    ctx = make_context(owner)
    async with user_session(xenv.factory, ctx) as db:
        return (await db.execute(statement, {"owner_id": owner, **params})).first() is not None


async def test_treating_as_uncertain_needs_the_same_holder_and_token(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await xenv.user()
    connection = await xenv.connect(owner)
    holder, other = xenv.job(owner, connection), xenv.job(owner, connection)
    op = await plant(xenv, owner, holder, "pending")
    mark = {"id": op, "job_id": other}  # ``other`` is the job that meets the stale row
    assert not await matched(xenv, owner, _MARK_UNCERTAIN, **mark, old_job=other, old_v=1)
    assert not await matched(xenv, owner, _MARK_UNCERTAIN, **mark, old_job=holder, old_v=2)
    assert [(o.state, o.job_id) for o in xenv.operations(owner)] == [("pending", holder)]
    assert await matched(xenv, owner, _MARK_UNCERTAIN, **mark, old_job=holder, old_v=1)
    # It follows the job that marked it (#125); the call's token stays.
    assert [(o.state, o.job_id, o.lease_version) for o in xenv.operations(owner)] == [
        ("uncertain", other, 1)
    ]


async def test_recording_an_outcome_needs_a_pending_row_of_this_job_and_token(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner = await xenv.user()
    connection = await xenv.connect(owner)
    mine, other = xenv.job(owner, connection), xenv.job(owner, connection)
    values = {"state": "succeeded", "external_id": "1", "result": json.dumps({}), "category": None}
    op = await plant(xenv, owner, mine, "uncertain")
    assert not await matched(xenv, owner, _RECORD, id=op, job_id=mine, v=1, **values)
    xenv.sql("UPDATE external_operations SET state = 'pending' WHERE id = :id", id=op)
    assert not await matched(xenv, owner, _RECORD, id=op, job_id=other, v=1, **values)
    assert not await matched(xenv, owner, _RECORD, id=op, job_id=mine, v=2, **values)
    assert [o.state for o in xenv.operations(owner)] == ["pending"]
    assert await matched(xenv, owner, _RECORD, id=op, job_id=mine, v=1, **values)
    assert [o.state for o in xenv.operations(owner)] == ["succeeded"]


async def test_a_later_request_refused_in_a_multi_request_call_is_uncertain(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    """A call of several requests (``single_request=False``) whose later request is refused by
    the allowlist: an earlier one already reached the provider, so the refusal (a separate
    ``urlopen``, ``after_request`` False) is ``uncertain`` and nothing is sent again."""
    owner = await xenv.user()
    connection = await xenv.connect(owner)
    calls: list[str] = []
    request = OperationRequest(
        kind=ATTACH,
        idempotency_key=f"spotify:{PLAYLIST}",
        request_hash=hash_for(PLAYLIST),
        timeout_seconds=30,
        single_request=False,
    )

    async def call() -> CallResult:
        calls.append("first request reached the provider")
        raise OutboundBlocked("evil.example")  # the second request, refused before it left

    async def run(job_run: JobRun) -> Complete:
        await job_run.stage("music")
        await perform(job_run, request, call)
        return Complete()

    registry = KindRegistry()
    registry.register(JobKind(KIND, AttachInput, ("music",), run))
    jobs = [xenv.job(owner, connection), xenv.job(owner, connection)]
    for job_id in jobs:
        await execute(
            await xenv.claim(),
            xenv.factory,
            registry,
            RunConfig(lease_seconds=xenv.app.settings.job_lease_seconds),
            asyncio.Event(),
        )
        assert (xenv.row(job_id).state, xenv.row(job_id).error_category) == (
            "failed", "uncertain_write"
        )  # fmt: skip
    [op] = xenv.operations(owner)
    assert (op.state, op.error_category) == ("uncertain", "outbound_blocked")
    assert len(calls) == 1  # the next job never calls again
    assert fake_attach.requests == []
