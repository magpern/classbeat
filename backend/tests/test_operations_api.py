"""Check again, Do it anyway and the operation listing through the API (ADR 0005 section 7),
the MOWL disconnect deleting links and attach operations (ADR 0006 section 3), the
``jobs stats`` CLI, and the ``not_applied`` classification of a provider failure."""

import argparse
import asyncio
import io
import socket
import uuid
from typing import Any
from urllib.error import HTTPError, URLError

import pytest
from mowl.errors import MowlError

from authtools import unsafe_headers
from classbeat.cli import add_subcommands, run_command
from classbeat.connections import ConnectionFailure
from classbeat.connections.errors import not_applied
from classbeat.operations import OPERATION_KINDS, OperationKind, Reconciliation, attach
from optools import PLAYLIST, FakeMowlAttach, OpsEnv
from test_operations import ATTACH, hash_for, owner_with_job

OPS = "/api/external-operations"


async def uncertain_attach(xenv: OpsEnv, fake: FakeMowlAttach) -> tuple[uuid.UUID, uuid.UUID, Any]:
    owner, connection, job_id = await owner_with_job(xenv)
    fake.mode = "timeout_after_accept"
    await xenv.run_next()
    assert xenv.row(job_id).error_category == "uncertain_write"
    fake.mode = "ok"
    [op] = xenv.operations(owner)
    return owner, connection, op


async def test_check_again_inconclusive_then_do_it_anyway_then_a_new_attach(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, make_actor: Any
) -> None:
    owner, connection, op = await uncertain_attach(xenv, fake_attach)
    actor = await make_actor(owner)
    listed = (await actor.get(f"{OPS}?job_id={op.job_id}")).json()["operations"]
    assert [(o["id"], o["state"], o["kind"]) for o in listed] == [(str(op.id), "uncertain", ATTACH)]
    shown = listed[0]
    assert set(shown) == {
        "id", "kind", "state", "job_id", "resolved_by", "created_at", "updated_at", "resolved_at"
    }  # fmt: skip
    checked = await actor.post(f"{OPS}/{op.id}/check-again")
    assert checked.status_code == 200, checked.text
    assert (checked.json()["state"], checked.json()["resolved_by"]) == ("uncertain", None)
    assert len(fake_attach.attaches) == 1

    decided = await actor.post(f"{OPS}/{op.id}/do-it-anyway")
    assert decided.status_code == 200
    assert (decided.json()["state"], decided.json()["resolved_by"]) == ("failed", "user")
    again = await actor.post(f"{OPS}/{op.id}/do-it-anyway")  # idempotent
    assert (again.status_code, again.json()["state"]) == (200, "failed")
    refused = await actor.post(f"{OPS}/{op.id}/check-again")
    assert (refused.status_code, refused.json()) == (409, {"detail": {"reason": "not_uncertain"}})
    assert (await actor.get(OPS)).json()["operations"] == []

    # The user's new job takes the failed row over and attaches (a second wrapper at worst).
    new_job = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(new_job).state == "completed"
    assert len(fake_attach.attaches) == 2
    assert xenv.links(owner) == [(PLAYLIST, 1002)]
    for action in ("check-again", "do-it-anyway"):  # a succeeded row is final
        final = await actor.post(f"{OPS}/{op.id}/{action}")
        assert (final.status_code, final.json()) == (409, {"detail": {"reason": "final"}})
    assert (await actor.get(f"{OPS}/{op.id}")).json()["state"] == "succeeded"


async def test_a_check_again_that_finds_the_attach_writes_the_link(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, make_actor: Any
) -> None:
    owner, connection, op = await uncertain_attach(xenv, fake_attach)

    async def found(_: object) -> Reconciliation:
        return Reconciliation("succeeded", "777")

    original = OPERATION_KINDS.replace(OperationKind(ATTACH, found, attach.ATTACH.on_succeeded))
    try:
        actor = await make_actor(owner)
        checked = await actor.post(f"{OPS}/{op.id}/check-again")
    finally:
        assert original is not None
        OPERATION_KINDS.replace(original)
    assert checked.status_code == 200
    assert (checked.json()["state"], checked.json()["resolved_by"]) == ("succeeded", "user")
    assert xenv.links(owner) == [(PLAYLIST, 777)]
    [row] = xenv.operations(owner)
    assert row.external_id == "777"
    # The next job uses the link: no write.
    job_id = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(job_id).state == "completed"
    assert len(fake_attach.attaches) == 1
    assert xenv.playlists[-1] == 777


async def test_a_pending_operation_is_not_the_users_to_decide(
    xenv: OpsEnv, make_actor: Any
) -> None:
    owner = await xenv.user()
    operation = uuid.uuid4()
    xenv.sql(
        "INSERT INTO external_operations (id, owner_id, kind, idempotency_key, request_hash, "
        "state, lease_version) VALUES (:id, :o, :k, :key, :h, 'pending', 1)",
        id=operation, o=owner, k=ATTACH, key=f"spotify:{PLAYLIST}", h=hash_for(PLAYLIST),
    )  # fmt: skip
    actor = await make_actor(owner)
    for action in ("check-again", "do-it-anyway"):
        response = await actor.post(f"{OPS}/{operation}/{action}")
        assert (response.status_code, response.json()) == (
            409, {"detail": {"reason": "not_uncertain"}}
        )  # fmt: skip
    assert [o.state for o in xenv.operations(owner)] == ["pending"]


async def test_another_users_operation_is_not_found(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, make_actor: Any
) -> None:
    owner, _, op = await uncertain_attach(xenv, fake_attach)
    stranger = await make_actor(await xenv.user())
    for path in (f"{OPS}/{op.id}", f"{OPS}/{uuid.uuid4()}"):
        response = await stranger.get(path)
        assert (response.status_code, response.json()) == (404, {"detail": {"reason": "not_found"}})
    for action in ("check-again", "do-it-anyway"):
        response = await stranger.post(f"{OPS}/{op.id}/{action}")
        assert response.status_code == 404
    assert (await stranger.get(OPS)).json()["operations"] == []
    assert (await stranger.get(f"{OPS}?job_id={op.job_id}")).json()["operations"] == []
    assert [o.state for o in xenv.operations(owner)] == ["uncertain"]


async def test_the_actions_need_a_session_and_the_csrf_token(
    xenv: OpsEnv, fake_attach: FakeMowlAttach
) -> None:
    owner, _, op = await uncertain_attach(xenv, fake_attach)
    client = xenv.app.client
    assert (await client.get(OPS)).status_code == 401
    issued = await xenv.app.login(owner)
    response = await client.post(
        f"{OPS}/{op.id}/do-it-anyway", headers=unsafe_headers(issued.token)
    )
    assert response.status_code == 403
    anonymous = await client.post(f"{OPS}/{op.id}/do-it-anyway", headers=unsafe_headers())
    assert anonymous.status_code in (401, 403)
    assert [o.state for o in xenv.operations(owner)] == ["uncertain"]


async def test_the_disconnect_route_deletes_only_this_users_links_and_attaches(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, make_actor: Any
) -> None:
    mine, _, _ = await owner_with_job(xenv)
    await xenv.run_next()
    theirs, _, _ = await owner_with_job(xenv)
    await xenv.run_next()
    assert len(xenv.links(mine)) == len(xenv.links(theirs)) == 1
    actor = await make_actor(mine)
    assert (await actor.call("DELETE", "/api/connections/mowl")).status_code == 204
    assert (xenv.links(mine), xenv.operations(mine)) == ([], [])
    assert len(xenv.links(theirs)) == 1 and len(xenv.operations(theirs)) == 1  # noqa: PT018


async def test_jobs_stats_prints_counts_only(xenv: OpsEnv, fake_attach: FakeMowlAttach) -> None:
    await uncertain_attach(xenv, fake_attach)
    parser = argparse.ArgumentParser()
    add_subcommands(parser)
    out, err = io.StringIO(), io.StringIO()
    code = await asyncio.to_thread(
        run_command, parser.parse_args(["jobs", "stats"]), xenv.app.settings, out, err
    )
    assert code == 0, err.getvalue()
    lines = dict(line.split(" ") for line in out.getvalue().splitlines())
    assert lines["external_operations_uncertain"] == "1"
    assert lines["jobs_failed"] == "1"
    assert set(lines) == {
        "jobs_queued", "jobs_running", "jobs_waiting_for_approval", "jobs_completed",
        "jobs_failed", "jobs_cancelled", "jobs_expired", "external_operations_uncertain",
    }  # fmt: skip


def _http(code: int) -> HTTPError:
    return HTTPError("https://api.mowl.com/v1/x", code, "x", {}, None)  # pyright: ignore[reportArgumentType]


def _wrapped(cause: BaseException) -> MowlError:
    try:
        raise MowlError("x") from cause
    except MowlError as error:
        return error


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_wrapped(_http(400)), True),
        (_wrapped(_http(401)), True),
        (_wrapped(_http(429)), True),
        (_wrapped(_http(500)), False),
        (_wrapped(_http(503)), False),
        (MowlError("MOWL's own refusal"), True),
        (URLError(ConnectionRefusedError()), True),
        (URLError(socket.gaierror()), True),
        (URLError(TimeoutError()), False),
        (URLError(ConnectionResetError()), False),
        (TimeoutError(), False),
        (KeyError("Data"), False),
        (ValueError("bad json"), False),
    ],
)
def test_only_a_refusal_or_a_request_that_never_left_is_not_applied(
    exc: BaseException, expected: bool
) -> None:
    assert not_applied(exc) is expected


def test_failures_before_any_call_are_not_applied() -> None:
    for category in ("not_connected", "key_unavailable", "decrypt_failed"):
        assert ConnectionFailure(category).not_applied
    assert not ConnectionFailure("upstream_unavailable").not_applied
    assert ConnectionFailure("unknown", not_applied=True).not_applied


def test_a_reconciliation_names_an_id_only_when_it_succeeded() -> None:
    with pytest.raises(ValueError, match="names the external id"):
        Reconciliation("succeeded")
    with pytest.raises(ValueError, match="only a succeeded"):
        Reconciliation("failed", "1")
    with pytest.raises(ValueError, match="names the external id"):
        Reconciliation("succeeded", "x" * 201)
