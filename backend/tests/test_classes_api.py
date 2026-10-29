"""The class API (ADR 0007 sections 2, 11, 12; issue C2): reads, the pick of the current revision
and the class delete, against Postgres. Setup rows are written as the superuser; everything under
test runs as ``classbeat_app`` through the web app. Fakes only."""

import hashlib
import json
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection

from authtools import session_cookie
from classbeat.classes import seams
from jobapi import Browser
from jobtools import digest, new_class, new_job

pytestmark = pytest.mark.usefixtures("jobs_admin")

DRAFT: dict[str, Any] = {
    "name": "Tuesday climb",
    "description": "",
    "segments": [
        {
            "name": "Warm up",
            "kind": "warmup",
            "intervals": [
                {"sec": 60, "zone": 2, "rpm": [80, 90], "standing": False, "cue": ""},
                {"sec": 120, "zone": 3, "rpm": [85, 95], "standing": False, "cue": ""},
            ],
        },
        {
            "name": "Climb",
            "kind": "main",
            "intervals": [{"sec": 300, "zone": 5, "rpm": [60, 70], "standing": True, "cue": ""}],
        },
    ],
}
SECRET_LINK = str(uuid.uuid4())
SECRET_FINGERPRINT = "ab" * 32
SECRET_RUN = str(uuid.uuid4())
SPOTIFY = "37i9dQZF1DXcBWIGoYBM5M"
FORBIDDEN = (
    "program_id",
    "playlist_id",
    "link_id",
    "fingerprint",
    SECRET_LINK,
    SECRET_RUN,
    SPOTIFY,
)


def content_digest(content: dict[str, Any]) -> bytes:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).digest()


def insert(conn: Connection, table: str, **values: Any) -> UUID:
    values = {"id": uuid.uuid4(), **values}
    jsonb = {"content", "music", "provenance", "change", "request_context", "result", "input"}
    params = ", ".join(f"CAST(:{k} AS jsonb)" if k in jsonb else f":{k}" for k in values)
    row = {k: json.dumps(v) if k in jsonb else v for k, v in values.items()}
    return conn.execute(
        text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({params}) RETURNING id"),  # noqa: S608
        row,
    ).scalar_one()


def revision(conn: Connection, owner: UUID, class_id: UUID, number: int = 1, **more: Any) -> UUID:
    content = {**DRAFT, "name": f"v{number}"}
    values: dict[str, Any] = {
        "music": {
            "link_id": SECRET_LINK,
            "fingerprint": SECRET_FINGERPRINT,
            "unknown_bpm": [{"index": 2, "title": "Some Song"}],
        },
        "provenance": {
            "provider": "fake",
            "model": "m",
            "agent_run_ids": [SECRET_RUN],
            "cost_micros": 1234,
            "origin": "design_draft",
            "usage": {"input_tokens": 5, "output_tokens": 6},
        },
        **more,
    }
    return insert(
        conn,
        "class_revisions",
        owner_id=owner,
        class_id=class_id,
        number=number,
        kind="draft",
        content=content,
        content_hash=content_digest(content),
        **values,
    )


def make_current(conn: Connection, class_id: UUID, revision_id: UUID) -> None:
    conn.execute(
        text("UPDATE classes SET current_revision_id = :r WHERE id = :c"),
        {"r": revision_id, "c": class_id},
    )


def operation(
    conn: Connection, owner: UUID, class_id: UUID | None, state: str, **more: Any
) -> UUID:
    values: dict[str, Any] = {
        "kind": "mowl_save_workout",
        "idempotency_key": f"k{uuid.uuid4()}",
        "request_hash": digest("op"),
        "lease_version": 1,
        "pending_at": datetime.now(UTC),
        "request_context": {"bound_seconds": 600},
        **more,
    }
    if state == "succeeded":
        values["external_id"] = "41"
    return insert(
        conn, "external_operations", owner_id=owner, class_id=class_id, state=state, **values
    )


def finished_job(conn: Connection, owner: UUID, class_id: UUID) -> UUID:
    return new_job(conn, owner, class_id, state="completed", finished_at=datetime.now(UTC))


class Plant:
    """A user with a browser and a class with two revisions, the first current."""

    def __init__(self, admin: Connection, browser: Browser) -> None:
        self.admin, self.browser, self.owner = admin, browser, browser.user_id
        self.class_id = new_class(admin, self.owner)
        self.r1 = revision(admin, self.owner, self.class_id, 1)
        self.r2 = revision(admin, self.owner, self.class_id, 2, parent_id=self.r1)
        make_current(admin, self.class_id, self.r1)

    def hash_of(self, number: int) -> str:
        return content_digest({**DRAFT, "name": f"v{number}"}).hex()

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), params)

    def count(self, table: str) -> int:
        return self.sql(
            f"SELECT count(*) FROM {table} WHERE class_id = :c",  # noqa: S608
            c=self.class_id,
        ).scalar_one()


@pytest.fixture
async def two(browsers: Any, jobs_admin: Connection) -> tuple[Plant, Plant]:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    return Plant(jobs_admin, a), Plant(jobs_admin, b)


async def delete(browser: Browser, path: str) -> httpx.Response:
    response = await browser.client.delete(path, headers=await browser.headers())
    browser.client.cookies.clear()
    return response


NOT_FOUND = {"detail": {"reason": "not_found"}}


async def test_every_endpoint_answers_404_for_another_users_ids_and_unknown_ones(
    two: tuple[Plant, Plant],
) -> None:
    mine, theirs = two
    unknown = str(uuid.uuid4())
    revision_body = {"revision_id": str(theirs.r1), "content_hash": theirs.hash_of(1)}
    for cls, rev in ((str(theirs.class_id), str(theirs.r1)), (unknown, unknown), ("nope", "nope")):
        base = f"/api/classes/{cls}"
        for path in (base, f"{base}/revisions", f"{base}/revisions/{rev}", f"{base}/messages"):
            got = await mine.browser.get(path)
            assert (got.status_code, got.json()) == (404, NOT_FOUND), path
        posted = await mine.browser.post(f"{base}/current", revision_body)
        assert (posted.status_code, posted.json()) == (404, NOT_FOUND)
        deleted = await delete(mine.browser, base)
        assert (deleted.status_code, deleted.json()) == (404, NOT_FOUND)
    # Their class and revisions are untouched, and my own class is not reachable through theirs.
    assert theirs.count("class_revisions") == 2
    assert theirs.sql("SELECT count(*) FROM classes WHERE id = :c", c=theirs.class_id).scalar_one()
    # Mine does not list theirs.
    listed = (await mine.browser.get("/api/classes")).json()["classes"]
    assert [c["id"] for c in listed] == [str(mine.class_id)]


async def test_a_revision_of_another_class_is_not_found_under_my_class(
    two: tuple[Plant, Plant],
) -> None:
    mine, theirs = two
    own_other = new_class(mine.admin, mine.owner, "Other")
    foreign = revision(mine.admin, mine.owner, own_other, 1)
    path = f"/api/classes/{mine.class_id}"
    assert (await mine.browser.get(f"{path}/revisions/{foreign}")).status_code == 404
    for target, number in ((foreign, 1), (theirs.r2, 2)):
        body = {"revision_id": str(target), "content_hash": theirs.hash_of(number)}
        assert (await mine.browser.post(f"{path}/current", body)).status_code == 404
    assert (
        mine.sql(
            "SELECT current_revision_id FROM classes WHERE id = :c", c=mine.class_id
        ).scalar_one()
        == mine.r1
    )


async def test_anonymous_requests_are_refused(browsers: Any, two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    for path in ("/api/classes", f"/api/classes/{mine.class_id}"):
        response = await mine.browser.client.get(path)
        assert response.status_code == 401


async def test_the_views_show_what_the_page_needs_and_no_provider_ids(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    insert(
        mine.admin,
        "saved_workouts",
        owner_id=mine.owner,
        class_id=mine.class_id,
        program_id=987654,
        playlist_id=555,
        revision_id=mine.r1,
        revision_number=1,
        content_hash=content_digest({**DRAFT, "name": "v1"}),
        name="Tuesday climb",
    )
    queued = new_job(mine.admin, mine.owner, mine.class_id)
    insert(
        mine.admin,
        "class_messages",
        owner_id=mine.owner,
        class_id=mine.class_id,
        seq=1,
        role="user",
        body="Make it longer",
        job_id=queued,
    )
    mine.sql(
        "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
        m=json.dumps({"source": "mowl_spotify_playlist", "spotify_playlist_id": SPOTIFY}),
        c=mine.class_id,
    )
    base = f"/api/classes/{mine.class_id}"
    texts: list[str] = []

    listed = await mine.browser.get("/api/classes")
    texts.append(listed.text)
    [row] = listed.json()["classes"]
    shown = await mine.browser.get(base)
    texts.append(shown.text)
    assert shown.json() == row
    assert (row["current_revision_id"], row["current_revision_number"]) == (str(mine.r1), 1)
    assert (row["revision_count"], row["open_job_id"]) == (2, str(queued))
    assert row["open_job"] == {"id": str(queued), "kind": "test_kind", "state": "queued"}
    assert (row["saved"]["count"], row["saved"]["latest_revision_number"]) == (1, 1)
    assert row["saved"]["latest_up_to_date"] is True

    revisions = await mine.browser.get(f"{base}/revisions")
    texts.append(revisions.text)
    first, second = revisions.json()["revisions"]
    assert (first["number"], first["parent_number"], first["is_current"]) == (1, None, True)
    assert (second["number"], second["parent_number"], second["is_current"]) == (2, 1, False)
    assert first["summary"] == {"segments": 2, "intervals": 3, "total_sec": 480}
    assert (first["origin"], first["cost_micros"]) == ("design_draft", 1234)

    one = await mine.browser.get(f"{base}/revisions/{mine.r2}")
    texts.append(one.text)
    detail = one.json()
    assert detail["content"]["name"] == "v2"
    assert detail["unknown_bpm"] == [{"index": 2, "title": "Some Song"}]
    assert detail["provenance"]["cost_micros"] == 1234
    assert detail["provenance"]["usage"]["input_tokens"] == 5
    assert "agent_run_ids" not in detail["provenance"]

    messages = await mine.browser.get(f"{base}/messages")
    texts.append(messages.text)
    [message] = messages.json()["messages"]
    assert (message["seq"], message["body"], message["job_id"]) == (
        1,
        "Make it longer",
        str(queued),
    )

    for body in texts:
        for forbidden in FORBIDDEN:
            assert forbidden not in body, forbidden


async def test_picking_a_revision(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    path = f"/api/classes/{mine.class_id}/current"
    body = {"revision_id": str(mine.r2), "content_hash": mine.hash_of(2)}

    stale = await mine.browser.post(path, {**body, "content_hash": mine.hash_of(1)})
    assert (stale.status_code, stale.json()["detail"]["reason"]) == (409, "hash_mismatch")
    bad = await mine.browser.post(path, {**body, "content_hash": "xyz"})
    assert bad.status_code == 422

    job = new_job(mine.admin, mine.owner, mine.class_id)  # queued: open
    busy = await mine.browser.post(path, body)
    assert (busy.status_code, busy.json()["detail"]["reason"]) == (409, "class_busy")
    mine.sql(
        "UPDATE jobs SET state = 'completed', finished_at = now(), credit_state = 'committed'"
        " WHERE id = :j",
        j=job,
    )

    done = await mine.browser.post(path, body)
    assert done.status_code == 200, done.text
    assert done.json()["current_revision_number"] == 2
    stamp = mine.sql("SELECT updated_at FROM classes WHERE id = :c", c=mine.class_id).scalar_one()

    again = await mine.browser.post(path, body)  # twice is a no-op
    assert (again.status_code, again.json()["current_revision_number"]) == (200, 2)
    assert (
        mine.sql("SELECT updated_at FROM classes WHERE id = :c", c=mine.class_id).scalar_one()
        == stamp
    )
    # Picking the current revision again is fine even while a job is open.
    new_job(mine.admin, mine.owner, mine.class_id)
    assert (await mine.browser.post(path, body)).status_code == 200
    back = {"revision_id": str(mine.r1), "content_hash": mine.hash_of(1)}
    assert (await mine.browser.post(path, back)).status_code == 409


async def test_listing_200_classes_of_25_revisions_takes_a_fixed_number_of_queries(
    two: tuple[Plant, Plant], env: Any
) -> None:
    mine, theirs = two
    statements: list[str] = []
    engine = env.factory.kw["bind"].sync_engine

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        statements.append(statement)

    async def measure() -> int:
        statements.clear()
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = await mine.browser.get("/api/classes")
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200
        return len(statements)

    small = await measure()
    mine.sql(
        "INSERT INTO classes (id, owner_id, title) "
        "SELECT gen_random_uuid(), :o, 'Class ' || g FROM generate_series(1, 199) g",
        o=mine.owner,
    )
    mine.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance) "
        "SELECT gen_random_uuid(), c.owner_id, c.id, n, 'draft', CAST(:content AS jsonb), "
        "sha256(convert_to(n::text, 'UTF8')), '{}'::jsonb, '{}'::jsonb "
        "FROM classes c, generate_series(1, 25) n WHERE c.owner_id = :o AND c.id <> :keep",
        content=json.dumps(DRAFT),
        o=mine.owner,
        keep=mine.class_id,
    )
    assert (
        mine.sql(
            "SELECT count(*) FROM class_revisions WHERE owner_id = :o", o=mine.owner
        ).scalar_one()
        == 199 * 25 + 2
    )
    large = await measure()
    assert large == small
    assert large <= 10  # session lookups plus the listing, whatever the data
    listed = (await mine.browser.get("/api/classes")).json()["classes"]
    assert len(listed) == 200
    assert sorted(c["revision_count"] for c in listed)[-1] == 25
    assert [c["id"] for c in (await theirs.browser.get("/api/classes")).json()["classes"]] == [
        str(theirs.class_id)
    ]


async def test_delete_removes_the_class_and_keeps_usage(two: tuple[Plant, Plant]) -> None:
    mine, theirs = two
    job = finished_job(mine.admin, mine.owner, mine.class_id)
    insert(
        mine.admin,
        "job_events",
        owner_id=mine.owner,
        job_id=job,
        seq=1,
        kind="queued",
        lease_version=0,
    )
    insert(
        mine.admin,
        "class_messages",
        owner_id=mine.owner,
        class_id=mine.class_id,
        seq=1,
        role="user",
        body="Longer climb",
    )
    insert(
        mine.admin,
        "saved_workouts",
        owner_id=mine.owner,
        class_id=mine.class_id,
        program_id=41,
        revision_number=1,
        content_hash=digest("c"),
        name="Tuesday climb",
    )
    operation(mine.admin, mine.owner, mine.class_id, "succeeded")
    operation(mine.admin, mine.owner, mine.class_id, "failed")
    attach = insert(
        mine.admin,
        "external_operations",
        owner_id=mine.owner,
        kind="mowl_attach_spotify_playlist",
        idempotency_key="attach-1",
        request_hash=digest("a"),
        state="succeeded",
        external_id="9",
        lease_version=1,
    )
    run = insert(
        mine.admin,
        "agent_runs",
        owner_id=mine.owner,
        job_id=job,
        stage="design",
        attempt=1,
        provider="fake",
        model="m",
        effort="medium",
        prompt_version="v",
        cost_micros=77,
    )
    theirs_before = theirs.count("class_revisions")

    response = await delete(mine.browser, f"/api/classes/{mine.class_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["deleted"], body["mowl_workouts_kept"]) == (True, 1)
    assert "MOWL" in body["note"]
    for table in (
        "class_revisions",
        "class_messages",
        "saved_workouts",
        "external_operations",
        "jobs",
    ):
        assert mine.count(table) == 0, table
    assert mine.sql("SELECT count(*) FROM job_events WHERE job_id = :j", j=job).scalar_one() == 0
    assert mine.sql("SELECT count(*) FROM classes WHERE id = :c", c=mine.class_id).scalar_one() == 0
    kept = mine.sql("SELECT job_id, cost_micros FROM agent_runs WHERE id = :r", r=run).one()
    assert (kept.job_id, kept.cost_micros) == (None, 77)
    assert mine.sql("SELECT count(*) FROM external_operations WHERE id = :a", a=attach).scalar_one()
    assert theirs.count("class_revisions") == theirs_before
    assert (await delete(mine.browser, f"/api/classes/{mine.class_id}")).status_code == 404


async def test_delete_needs_csrf(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    response = await mine.browser.client.delete(
        f"/api/classes/{mine.class_id}", headers=session_cookie(mine.browser.token)
    )
    assert response.status_code == 403
    assert mine.count("class_revisions") == 2


def reason(response: httpx.Response) -> tuple[int, str]:
    return response.status_code, response.json()["detail"]["reason"]


async def test_delete_is_refused_while_a_job_is_open(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    new_job(
        mine.admin,
        mine.owner,
        mine.class_id,
        state="waiting_for_approval",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        approval_hash=digest("x"),
    )
    operation(mine.admin, mine.owner, mine.class_id, "pending")
    assert reason(await delete(mine.browser, f"/api/classes/{mine.class_id}")) == (
        409,
        "class_busy",
    )
    assert mine.count("class_revisions") == 2


@pytest.mark.parametrize("state", ["pending", "uncertain"])
async def test_delete_is_refused_while_a_call_may_be_running(
    two: tuple[Plant, Plant], state: str
) -> None:
    """``pending``, and ``uncertain`` still inside its bound (no ``call_ended_at``), may still
    land: the cascade would drop the only record of it."""
    mine, _ = two
    finished_job(mine.admin, mine.owner, mine.class_id)
    operation(mine.admin, mine.owner, mine.class_id, state, kind="mowl_update_workout")
    path = f"/api/classes/{mine.class_id}"
    assert reason(await delete(mine.browser, path)) == (409, "call_may_be_running")
    confirmed = await delete(mine.browser, path + "?confirm_uncertain=true")
    assert reason(confirmed) == (409, "call_may_be_running")  # confirming does not bypass it
    assert mine.count("external_operations") == 1


async def test_a_pending_row_refuses_even_long_after_its_bound(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    operation(
        mine.admin,
        mine.owner,
        mine.class_id,
        "pending",
        pending_at=datetime.now(UTC) - timedelta(days=2),
    )
    assert reason(await delete(mine.browser, f"/api/classes/{mine.class_id}")) == (
        409,
        "call_may_be_running",
    )


async def test_an_uncertain_write_past_its_bound_needs_confirmation(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    old = datetime.now(UTC) - timedelta(hours=2)
    operation(mine.admin, mine.owner, mine.class_id, "uncertain", pending_at=old)
    path = f"/api/classes/{mine.class_id}"
    assert reason(await delete(mine.browser, path)) == (409, "uncertain_write")
    assert reason(await delete(mine.browser, path + "?confirm_uncertain=false")) == (
        409,
        "uncertain_write",
    )
    assert mine.count("class_revisions") == 2
    done = await delete(mine.browser, path + "?confirm_uncertain=true")
    assert done.status_code == 200, done.text
    assert mine.count("class_revisions") == 0


async def test_an_uncertain_write_whose_call_returned_is_actionable_at_once(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    now = datetime.now(UTC)
    operation(
        mine.admin,
        mine.owner,
        mine.class_id,
        "uncertain",
        pending_at=now,
        call_ended_at=now + timedelta(seconds=1),
    )
    path = f"/api/classes/{mine.class_id}"
    assert reason(await delete(mine.browser, path)) == (409, "uncertain_write")


async def test_the_bound_comes_from_the_rows_context(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    pending_at = datetime.now(UTC) - timedelta(seconds=100)
    row = operation(
        mine.admin,
        mine.owner,
        mine.class_id,
        "uncertain",
        pending_at=pending_at,
        request_context={"bound_seconds": 60},
    )
    path = f"/api/classes/{mine.class_id}"
    assert reason(await delete(mine.browser, path)) == (409, "uncertain_write")  # 100 > 60 + 30
    mine.sql(
        "UPDATE external_operations SET request_context = CAST(:c AS jsonb) WHERE id = :i",
        c=json.dumps({"bound_seconds": 80}),
        i=row,
    )
    assert reason(await delete(mine.browser, path)) == (409, "call_may_be_running")  # < 80 + 30
    mine.sql("UPDATE external_operations SET request_context = '{}'::jsonb WHERE id = :i", i=row)
    assert reason(await delete(mine.browser, path)) == (409, "call_may_be_running")  # fallback


async def test_succeeded_and_failed_writes_do_not_block_a_delete(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    operation(mine.admin, mine.owner, mine.class_id, "succeeded")
    operation(mine.admin, mine.owner, mine.class_id, "failed", kind="mowl_update_workout")
    assert (await delete(mine.browser, f"/api/classes/{mine.class_id}")).status_code == 200


async def test_orphans_are_resolved_before_the_checks(
    two: tuple[Plant, Plant], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook for #143's ``orphans.resolve`` runs first (class view and delete), under the
    user's session, and what it marks stays marked when the delete is refused."""
    mine, _ = two
    finished_job(mine.admin, mine.owner, mine.class_id)
    orphan = operation(
        mine.admin,
        mine.owner,
        mine.class_id,
        "pending",
        pending_at=datetime.now(UTC) - timedelta(hours=2),
    )
    calls: list[UUID] = []

    async def resolve(db: Any, ctx: Any) -> None:
        calls.append(ctx.user_id)
        await db.execute(
            text(
                "UPDATE external_operations SET state = 'uncertain' WHERE state = 'pending' "
                "AND owner_id = :o"
            ),
            {"o": ctx.user_id},
        )

    fake = types.ModuleType(seams.ORPHANS_MODULE)
    fake.resolve = resolve  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, seams.ORPHANS_MODULE, fake)

    assert (await mine.browser.get(f"/api/classes/{mine.class_id}")).status_code == 200
    assert calls == [mine.owner]  # the class view
    path = f"/api/classes/{mine.class_id}"
    assert reason(await delete(mine.browser, path)) == (409, "uncertain_write")
    assert calls == [mine.owner, mine.owner]
    state = mine.sql("SELECT state FROM external_operations WHERE id = :i", i=orphan).scalar_one()
    assert state == "uncertain"  # the refusal did not roll the marking back


async def test_the_hook_does_nothing_until_the_module_exists(
    two: tuple[Plant, Plant], monkeypatch: pytest.MonkeyPatch
) -> None:
    mine, _ = two
    monkeypatch.setitem(sys.modules, seams.ORPHANS_MODULE, None)  # the import fails: absent
    assert (await mine.browser.get(f"/api/classes/{mine.class_id}")).status_code == 200


async def test_users_without_classes_get_an_empty_list(browsers: Any) -> None:
    browser: Browser = await browsers(mowl=False)
    assert (await browser.get("/api/classes")).json() == {"classes": []}
