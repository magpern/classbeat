"""The jobs API: create, get and cancel (ADR 0005 section 2; #87, J3), through the real app on
the app role under RLS, with the test-only kind. No provider is contacted."""

import asyncio
import hashlib
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from authtools import ORIGIN, Env, session_cookie
from classbeat.jobs.routes import canonical_json
from jobapi import (
    UNKNOWN,
    Browser,
    new_key,
    owner_lock_key,
    plant_running,
    until_waiting_on_the_owner_lock,
)
from jobcore import JobsEnv
from jobtools import engine_for

pytestmark = pytest.mark.usefixtures("jenv")


async def test_create_queues_a_job_on_a_new_class_with_its_event_and_credit(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    response = await alice.create(note="hills")
    assert response.status_code == 201, response.text
    shown = response.json()
    assert (shown["state"], shown["kind"], shown["cancel_requested"]) == (
        "queued",
        "test_kind",
        False,
    )
    assert set(shown) == {
        "id",
        "kind",
        "state",
        "stage",
        "class_id",
        "cancel_requested",
        "approval_revision_id",
        "expires_at",
        "approved_at",
        "error_category",
        "error_message",
        "created_at",
        "updated_at",
        "finished_at",
        "usage",
    }  # fmt: skip  (no input, key, hash, lease or worker name)
    job_id = UUID(shown["id"])
    row = jenv.row(job_id)
    assert (row.owner_id, row.credit_state, row.attempt, row.next_seq) == (
        alice.user_id, "reserved", 0, 1
    )  # fmt: skip
    assert row.input == {
        "class_id": None, "title": "Tuesday climb", "note": "hills",
        "mowl_connection_id": str(alice.connection_id),
    }  # fmt: skip
    title = jenv.sql("SELECT title FROM classes WHERE id = :i", i=row.class_id).scalar_one()
    assert title == "Tuesday climb"
    assert jenv.events(job_id) == [(1, "queued", {}, 0)]
    got = await alice.get(f"/api/jobs/{job_id}")
    assert (got.status_code, got.json()) == (200, shown)


async def test_create_on_an_existing_class_and_class_busy(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    first = (await alice.create()).json()
    again = await alice.create(class_id=first["class_id"])
    assert (again.status_code, again.json()) == (409, {"detail": {"reason": "class_busy"}})
    jenv.sql(
        "UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE id = :i", i=first["id"]
    )
    second = await alice.create(class_id=first["class_id"])
    assert second.status_code == 201, second.text
    assert second.json()["class_id"] == first["class_id"]


async def test_another_users_class_is_not_found_like_an_unknown_one(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    bob: Browser = await browsers()
    bobs_class = (await bob.create()).json()["class_id"]
    theirs = await alice.create(class_id=bobs_class)
    nothing = await alice.create(class_id=UNKNOWN)
    assert (theirs.status_code, theirs.text) == (nothing.status_code, nothing.text)
    assert theirs.json() == {"detail": {"reason": "class_not_found"}}
    assert jenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=alice.user_id).scalar() == 0


async def test_idempotency_same_key_same_body_other_body_and_two_keys(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    key = new_key()
    first = await alice.create(key, note="a")
    replay = await alice.create(key, note="a")
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.json() == first.json()
    other = await alice.create(key, note="b")
    assert (other.status_code, other.json()) == (
        409,
        {"detail": {"reason": "idempotency_mismatch"}},
    )
    second = await alice.create(new_key(), note="a")
    assert second.status_code == 201 and second.json()["id"] != first.json()["id"]  # noqa: PT018
    third = await alice.create(new_key(), note="a")  # the per-user limit (2) is reached
    assert (third.status_code, third.json()) == (429, {"detail": {"reason": "busy"}})
    assert jenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=alice.user_id).scalar() == 2
    # A replay still answers once the limit is reached, and writes nothing.
    assert (await alice.create(key, note="a")).status_code == 200
    stored = jenv.row(UUID(first.json()["id"]))
    body = {"kind": "test_kind", "input": stored.input}
    assert bytes(stored.request_hash) == hashlib.sha256(canonical_json(body)).digest()


async def test_concurrent_creates_with_one_key_make_one_job(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    key = new_key()
    responses = await asyncio.gather(*(alice.create(key) for _ in range(5)))
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 200, 201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert jenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=alice.user_id).scalar() == 1


async def test_concurrent_creates_never_pass_the_limit(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    responses = await asyncio.gather(*(alice.create() for _ in range(6)))
    assert sorted(r.status_code for r in responses) == [201, 201, 429, 429, 429, 429]


async def test_create_counts_the_limit_only_after_the_owner_lock(
    jenv: JobsEnv, browsers: Any, database_url: str
) -> None:
    """While another transaction holds the owner's lock, a create waits; a job that transaction
    commits is then counted (without the lock before the count, the create would pass)."""
    alice: Browser = await browsers()
    plant_running(jenv, alice.user_id)
    with engine_for(database_url) as engine, engine.connect() as holder:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": owner_lock_key(alice.user_id)},
        )
        task = asyncio.create_task(alice.create())
        await until_waiting_on_the_owner_lock(jenv)
        assert not task.done()
        plant_running(jenv, alice.user_id)  # the limit (2) is now reached
        holder.commit()
        response = await task
    assert (response.status_code, response.json()) == (429, {"detail": {"reason": "busy"}})


async def test_the_mowl_connection_must_be_the_users_own(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    bob: Browser = await browsers()
    nobody: Browser = await browsers(mowl=False)
    for browser, connection in ((alice, bob.connection_id), (nobody, alice.connection_id)):
        response = await browser.create(mowl_connection_id=str(connection))
        assert (response.status_code, response.json()) == (
            409, {"detail": {"reason": "not_connected"}}
        )  # fmt: skip
    assert jenv.sql("SELECT count(*) FROM jobs").scalar() == 0


async def test_header_and_body_refusals(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    body = alice.body()
    missing = await alice.post("/api/jobs", body)
    assert (missing.status_code, missing.json()["detail"]["reason"]) == (
        400,
        "idempotency_key_missing",
    )
    for bad in ("short", "x" * 65, "has space in it!!", "dots.are.not.allowed.here"):
        response = await alice.post("/api/jobs", body, {"Idempotency-Key": bad})
        assert response.json()["detail"]["reason"] == "idempotency_key_invalid", bad
    unknown = await alice.post(
        "/api/jobs", {**body, "kind": "nope"}, {"Idempotency-Key": new_key()}
    )
    assert (unknown.status_code, unknown.json()["detail"]["reason"]) == (422, "unknown_kind")
    planted = "PLANTED-VALUE-iiiiiiiiiiiiiiii"
    for broken in (
        {**body, "extra": planted},
        {"kind": "test_kind", "input": {**body["input"], "note": 7, "zzz": planted}},
        {"kind": "test_kind", "input": {**body["input"], "class_id": UNKNOWN, "brief": planted}},
        {"kind": "test_kind", "input": {"mowl_connection_id": planted}},
        {"kind": "test_kind", "input": {**body["input"], "title": " "}},
        {"kind": "test_kind", "input": {**body["input"], "note": planted * 600}},
    ):
        response = await alice.post("/api/jobs", broken, {"Idempotency-Key": new_key()})
        assert response.status_code == 422, response.text
        assert planted not in response.text
        assert response.json()["detail"]["reason"] in ("invalid_input", "too_large")
    # An extra key's name is the client's text too: its error points at the parent (#115 item 3).
    secret_key = "PLANTED-KEY-kkkkkkkkkkkkkkkk"
    for broken in (
        {**body, secret_key: 1},
        {"kind": "test_kind", "input": {**body["input"], secret_key: 1}},  # the kind's model
    ):
        response = await alice.post("/api/jobs", broken, {"Idempotency-Key": new_key()})
        assert response.status_code == 422, response.text
        assert secret_key not in response.text
        assert {"loc": [], "type": "extra_forbidden"} in response.json()["detail"]["errors"]
    assert jenv.sql("SELECT count(*) FROM jobs").scalar() == 0


async def test_malformed_ids_and_approve_bodies_echo_nothing(jenv: JobsEnv, browsers: Any) -> None:
    """#115 item 2: a malformed path id is the 404 of an unknown one; a bad approve body is a 422
    that names neither the submitted values nor unknown keys."""
    alice: Browser = await browsers()
    planted = "PLANTED-VALUE-pppppppppppppppp"
    for path in (f"/api/jobs/{planted}", f"/api/jobs/{planted}/revision"):
        response = await alice.get(path)
        assert (response.status_code, response.json()) == (
            404, {"detail": {"reason": "not_found"}}
        )  # fmt: skip
    for path in (f"/api/jobs/{planted}/cancel", f"/api/jobs/{planted}/approve"):
        response = await alice.post(path, {"revision_id": UNKNOWN, "content_hash": "0" * 64})
        assert (response.status_code, response.json()) == (
            404, {"detail": {"reason": "not_found"}}
        )  # fmt: skip
    for body in (
        {"revision_id": planted, "content_hash": "0" * 64},
        {"revision_id": UNKNOWN, "content_hash": planted},
        {"revision_id": UNKNOWN, "content_hash": "0" * 64, planted: planted},
        [planted],
    ):
        response = await alice.post(f"/api/jobs/{UNKNOWN}/approve", body)
        assert response.status_code == 422, response.text
        assert planted not in response.text
        assert response.json()["detail"]["reason"] == "invalid_input"


async def test_anonymous_and_cross_site_requests_are_refused(env: Env, browsers: Any) -> None:
    alice: Browser = await browsers()
    anonymous = await env.client.post(
        "/api/jobs", json=alice.body(), headers={"Origin": ORIGIN, "Idempotency-Key": new_key()}
    )
    assert anonymous.status_code == 403  # the CSRF middleware runs first
    no_csrf = await env.client.post(
        "/api/jobs",
        json=alice.body(),
        headers={"Origin": ORIGIN, "Idempotency-Key": new_key()} | session_cookie(alice.token),
    )
    assert (no_csrf.status_code, no_csrf.json()["detail"]["reason"]) == (403, "csrf_token_missing")
    env.client.cookies.clear()
    assert (await env.client.get(f"/api/jobs/{UNKNOWN}")).status_code == 401


async def test_another_users_job_answers_exactly_like_an_unknown_id(
    jenv: JobsEnv, browsers: Any
) -> None:
    alice: Browser = await browsers()
    bob: Browser = await browsers()
    bobs_job = (await bob.create()).json()["id"]
    before = jenv.row(UUID(bobs_job))
    for path in ("", "/cancel", "/approve"):
        if path == "":
            theirs, nothing = (
                await alice.get(f"/api/jobs/{bobs_job}"),
                await alice.get(f"/api/jobs/{UNKNOWN}"),
            )
        elif path == "/cancel":
            theirs = await alice.post(f"/api/jobs/{bobs_job}/cancel")
            nothing = await alice.post(f"/api/jobs/{UNKNOWN}/cancel")
        else:
            theirs = await alice.approve(bobs_job, UNKNOWN, "0" * 64)
            nothing = await alice.approve(UNKNOWN, UNKNOWN, "0" * 64)
        assert (theirs.status_code, theirs.text) == (nothing.status_code, nothing.text), path
        assert theirs.json() == {"detail": {"reason": "not_found"}}
    after = jenv.row(UUID(bobs_job))
    assert (after.state, after.cancel_requested_at, after.next_seq) == ("queued", None, 1)
    assert before.updated_at == after.updated_at


async def test_cancel_queued_running_and_ended_jobs(jenv: JobsEnv, browsers: Any) -> None:
    alice: Browser = await browsers()
    queued = (await alice.create()).json()["id"]
    done = await alice.post(f"/api/jobs/{queued}/cancel")
    again = await alice.post(f"/api/jobs/{queued}/cancel")
    assert (done.status_code, done.json()["state"]) == (200, "cancelled")
    assert (again.status_code, again.json()["state"]) == (200, "cancelled")
    assert [k for _, k, _, _ in jenv.events(UUID(queued))] == ["queued", "cancelled"]

    running = plant_running(jenv, alice.user_id)
    requested = await alice.post(f"/api/jobs/{running}/cancel")
    assert requested.status_code == 200
    assert (requested.json()["state"], requested.json()["cancel_requested"]) == ("running", True)
    assert (await alice.post(f"/api/jobs/{running}/cancel")).status_code == 200
    assert jenv.kinds(running) == ["cancel_requested"]  # no second event

    jenv.sql(
        "UPDATE jobs SET state = 'completed', finished_at = now(), leased_by = NULL, "
        "lease_expires_at = NULL WHERE id = :i",
        i=running,
    )
    ended = await alice.post(f"/api/jobs/{running}/cancel")
    assert (ended.status_code, ended.json()) == (409, {"detail": {"reason": "not_cancellable"}})


async def test_a_body_over_the_cap_is_refused_before_it_is_read(
    jenv: JobsEnv, browsers: Any
) -> None:
    """#115 item 8: the jobs API reads at most ``BODY_MAX_BYTES`` of a body (413 beyond)."""
    from classbeat.jobs.routes import BODY_MAX_BYTES

    alice: Browser = await browsers()
    huge = alice.body(note="x" * BODY_MAX_BYTES)
    response = await alice.post("/api/jobs", huge, {"Idempotency-Key": new_key()})
    assert (response.status_code, response.json()) == (413, {"detail": {"reason": "too_large"}})
    approve = await alice.post(
        f"/api/jobs/{UNKNOWN}/approve", {"revision_id": UNKNOWN, "content_hash": "0" * 64,
                                         "pad": "x" * BODY_MAX_BYTES},
    )  # fmt: skip
    assert approve.status_code == 413
    assert jenv.sql("SELECT count(*) FROM jobs").scalar() == 0


def test_jsonb_size_is_one_measure_for_the_input_and_the_brief_limit() -> None:
    """``jobs.inputs.jsonb_size`` is what the API's input cap and the design kind's brief limit
    both use: jsonb's ``", "`` and ``": "`` separators, UTF-8 bytes, no escaping of non-ASCII."""
    from classbeat.design import revision
    from classbeat.jobs import inputs, routes

    assert inputs.jsonb_size({"a": [1, 2], "b": "å"}) == len('{"a": [1, 2], "b": "å"}'.encode())
    assert not hasattr(revision, "jsonb_size")  # the copies are gone
    assert not hasattr(routes, "_jsonb_size")
