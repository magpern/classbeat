"""The class API (ADR 0007 sections 2, 11, 12; issue C2): reads, the pick of the current revision
and the class delete, against Postgres. Setup rows are written as the superuser; everything under
test runs as ``classbeat_app`` through the web app. Fakes only."""

import base64
import hashlib
import json
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
    saved_id = insert(
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
    extra = {"songs", "playlist", "spotify_playlists"}  # the single class adds the song state
    assert {k: v for k, v in shown.json().items() if k not in extra} == row
    assert shown.json()["songs"] == {
        "state": "none", "job_id": None, "revision_id": None, "content_hash": None,
        "expires_at": None,
    }  # fmt: skip
    assert (shown.json()["playlist"], shown.json()["spotify_playlists"]) == (None, [])
    assert (row["current_revision_id"], row["current_revision_number"]) == (str(mine.r1), 1)
    assert (row["revision_count"], row["open_job_id"]) == (2, str(queued))
    assert row["open_job"] == {"id": str(queued), "kind": "test_kind", "state": "queued"}
    assert (row["saved"]["count"], row["saved"]["latest_revision_number"]) == (1, 1)
    assert row["saved"]["latest_up_to_date"] is True
    assert row["saved"]["latest_saved_workout_id"] == str(saved_id)
    assert row["saved"]["detached_count"] == 0

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
        for forbidden in (*FORBIDDEN, "987654"):
            assert forbidden not in body, forbidden


# --- the saved summary's workout id and detached count (#167) ---------------------------------

_PROGRAMS = iter(range(700001, 799999))


def saved_row(
    plant: Plant,
    class_id: UUID | None = None,
    *,
    age: int = 0,
    detached: bool = False,
    **more: Any,
) -> UUID:
    """A ``saved_workouts`` row of the plant's owner, ``age`` minutes old (by ``updated_at``)."""
    at = datetime.now(UTC) - timedelta(minutes=age)
    return insert(
        plant.admin,
        "saved_workouts",
        owner_id=plant.owner,
        class_id=class_id or plant.class_id,
        program_id=next(_PROGRAMS),
        revision_number=1,
        content_hash=content_digest({**DRAFT, "name": "v1"}),
        name="Tuesday climb",
        created_at=at,
        updated_at=at,
        detached_at=at if detached else None,
        **more,
    )


async def saved_summary(plant: Plant, class_id: UUID | None = None) -> dict[str, Any]:
    """The class's ``saved`` object; the listing and the one-class read must agree."""
    wanted = str(class_id or plant.class_id)
    shown = await plant.browser.get(f"/api/classes/{wanted}")
    assert shown.status_code == 200, shown.text
    listed = (await plant.browser.get("/api/classes")).json()["classes"]
    [row] = [c for c in listed if c["id"] == wanted]
    assert row["saved"] == shown.json()["saved"]
    for body in (shown.text, (await plant.browser.get("/api/classes")).text):
        for forbidden in FORBIDDEN:
            assert forbidden not in body, forbidden
    return shown.json()["saved"]


async def test_the_saved_summary_names_the_newest_live_row_and_counts_detached_ones(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    none = await saved_summary(mine)
    assert none == {
        "count": 0,
        "latest_revision_number": None,
        "latest_saved_at": None,
        "latest_up_to_date": None,
        "latest_saved_workout_id": None,
        "saved_state": "none",
        "detached_count": 0,
    }

    saved_row(mine, age=10)
    newest = saved_row(mine, age=1)
    saved_row(mine, age=0, detached=True)  # newer, but detached: never the latest
    summary = await saved_summary(mine)
    assert (summary["count"], summary["latest_saved_workout_id"]) == (2, str(newest))
    assert summary["saved_state"] == "ok"
    assert summary["detached_count"] == 1

    # every live row detached (as a MOWL disconnect or reconnect does): no id, count 0
    mine.sql("UPDATE saved_workouts SET detached_at = now() WHERE class_id = :c", c=mine.class_id)
    gone = await saved_summary(mine)
    assert (gone["count"], gone["latest_saved_workout_id"], gone["detached_count"]) == (0, None, 3)
    assert gone["latest_up_to_date"] is None
    assert gone["saved_state"] == "none"


async def test_another_users_class_and_my_other_class_never_contribute(
    two: tuple[Plant, Plant],
) -> None:
    mine, theirs = two
    my_other = new_class(mine.admin, mine.owner, "Other")
    # Newer rows, live and detached, on their class and on my other class only.
    their_live = saved_row(theirs, age=0)
    saved_row(theirs, age=0, detached=True)
    saved_row(theirs, age=0, detached=True)
    other_live = saved_row(mine, my_other, age=0)
    saved_row(mine, my_other, age=0, detached=True)

    empty = await saved_summary(mine)
    assert (empty["count"], empty["latest_saved_workout_id"], empty["detached_count"]) == (
        0,
        None,
        0,
    )

    old = saved_row(mine, age=30)
    saved_row(mine, age=40, detached=True)
    summary = await saved_summary(mine)
    assert (summary["count"], summary["latest_saved_workout_id"], summary["detached_count"]) == (
        1,
        str(old),
        1,
    )
    other = await saved_summary(mine, my_other)
    assert (other["count"], other["latest_saved_workout_id"], other["detached_count"]) == (
        1,
        str(other_live),
        1,
    )
    theirs_summary = await saved_summary(theirs)
    assert (
        theirs_summary["count"],
        theirs_summary["latest_saved_workout_id"],
        theirs_summary["detached_count"],
    ) == (1, str(their_live), 2)
    # None of their ids or mine leak into the other user's responses.
    for plant, foreign in ((mine, their_live), (theirs, old), (theirs, other_live)):
        listed = await plant.browser.get("/api/classes")
        assert str(foreign) not in listed.text


async def test_a_latest_row_whose_creating_operation_is_another_classs_names_no_id(
    two: tuple[Plant, Plant],
) -> None:
    """The ``operation_id`` foreign key is owner-inclusive, not class-inclusive (#156 review).
    Update refuses such a row, so the summary does not offer its id (it still counts)."""
    mine, _ = two
    my_other = new_class(mine.admin, mine.owner, "Other")
    foreign_op = operation(mine.admin, mine.owner, my_other, "succeeded")
    own_op = operation(mine.admin, mine.owner, mine.class_id, "succeeded")
    saved_row(mine, age=0, operation_id=foreign_op)
    inconsistent = await saved_summary(mine)
    assert (inconsistent["count"], inconsistent["latest_saved_workout_id"]) == (1, None)
    # A stable field tells the page why there is no id, instead of the id just vanishing.
    assert inconsistent["saved_state"] == "inconsistent"
    mine.sql("UPDATE saved_workouts SET operation_id = :o WHERE class_id = :c",
             o=own_op, c=mine.class_id)  # fmt: skip
    consistent = await saved_summary(mine)
    assert consistent["latest_saved_workout_id"] is not None
    assert consistent["saved_state"] == "ok"


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


async def test_pick_needs_csrf(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    response = await mine.browser.client.post(
        f"/api/classes/{mine.class_id}/current",
        json={"revision_id": str(mine.r2), "content_hash": mine.hash_of(2)},
        headers=session_cookie(mine.browser.token),
    )
    assert response.status_code == 403
    current = mine.sql("SELECT current_revision_id FROM classes WHERE id = :c", c=mine.class_id)
    assert current.scalar_one() == mine.r1


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


@pytest.mark.parametrize("job_state", ["queued", "running"])
async def test_a_pending_row_refuses_even_long_after_its_bound(
    two: tuple[Plant, Plant], job_state: str
) -> None:
    """A row of an open job is its holder's (``resolve`` leaves it ``pending``), and a ``pending``
    row refuses the delete whatever its age. The job is of another class, so ``class_busy``
    does not answer first: this is the delete's own check of ``pending``."""
    mine, _ = two
    lease: dict[str, Any] = {
        "attempt": 1,
        "leased_by": "other-worker",
        "lease_expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    holder = new_job(
        mine.admin, mine.owner, state=job_state, **(lease if job_state == "running" else {})
    )
    row = operation(
        mine.admin,
        mine.owner,
        mine.class_id,
        "pending",
        job_id=holder,
        pending_at=datetime.now(UTC) - timedelta(days=2),
    )
    path = f"/api/classes/{mine.class_id}"
    assert reason(await delete(mine.browser, path)) == (409, "call_may_be_running")
    assert reason(await delete(mine.browser, path + "?confirm_uncertain=true")) == (
        409,
        "call_may_be_running",
    )
    state = mine.sql("SELECT state FROM external_operations WHERE id = :i", i=row).scalar_one()
    assert state == "pending"


async def test_a_pending_row_without_a_job_past_its_bound_needs_confirmation(
    two: tuple[Plant, Plant],
) -> None:
    """No job (it was deleted, migration 0004 sets ``job_id`` NULL): the row is an orphan,
    ``resolve`` marks it ``uncertain`` (ADR 0007 section 8), and past its bound the delete needs
    the user's confirmation."""
    mine, _ = two
    row = operation(
        mine.admin,
        mine.owner,
        mine.class_id,
        "pending",
        pending_at=datetime.now(UTC) - timedelta(days=2),
    )
    path = f"/api/classes/{mine.class_id}"
    assert reason(await delete(mine.browser, path)) == (409, "uncertain_write")
    state = mine.sql("SELECT state FROM external_operations WHERE id = :i", i=row).scalar_one()
    assert state == "uncertain"
    done = await delete(mine.browser, path + "?confirm_uncertain=true")
    assert done.status_code == 200, done.text
    assert mine.count("class_revisions") == 0


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


async def test_only_save_and_update_rows_of_this_class_gate_a_delete(
    two: tuple[Plant, Plant],
) -> None:
    """The delete check looks at the class's own save and update rows. An attach belongs to no
    class (the table's check refuses a class on it), so a pending one never blocks, and nor does
    another class's pending write."""
    mine, theirs = two
    attach = operation(mine.admin, mine.owner, None, "pending", kind="mowl_attach_spotify_playlist")
    assert attach
    other = new_class(mine.admin, mine.owner, "Other")
    operation(mine.admin, mine.owner, other, "pending")
    with pytest.raises(Exception, match="check"):
        operation(
            mine.admin, mine.owner, mine.class_id, "pending", kind="mowl_attach_spotify_playlist"
        )
    mine.admin.rollback()
    operation(theirs.admin, theirs.owner, theirs.class_id, "pending")
    assert (await delete(mine.browser, f"/api/classes/{mine.class_id}")).status_code == 200


async def test_succeeded_and_failed_writes_do_not_block_a_delete(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    operation(mine.admin, mine.owner, mine.class_id, "succeeded")
    operation(mine.admin, mine.owner, mine.class_id, "failed", kind="mowl_update_workout")
    assert (await delete(mine.browser, f"/api/classes/{mine.class_id}")).status_code == 200


async def test_orphans_are_resolved_before_the_checks(
    two: tuple[Plant, Plant], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``classbeat.operations.orphans.resolve`` runs first (class view and delete), under the
    user's session, and what it marks stays marked when the delete is refused."""
    mine, theirs = two
    done = finished_job(mine.admin, mine.owner, mine.class_id)
    orphan = operation(
        mine.admin,
        mine.owner,
        mine.class_id,
        "pending",
        job_id=done,
        pending_at=datetime.now(UTC) - timedelta(hours=2),
    )
    other = operation(
        theirs.admin,
        theirs.owner,
        theirs.class_id,
        "pending",
        pending_at=datetime.now(UTC) - timedelta(hours=2),
    )
    calls: list[UUID] = []
    real = seams.resolve_orphans

    async def resolve(db: Any, ctx: Any) -> int:
        calls.append(ctx.user_id)
        return await real(db, ctx)

    monkeypatch.setattr(seams, "resolve_orphans", resolve)

    def state(row: UUID) -> str:
        found = mine.sql("SELECT state FROM external_operations WHERE id = :i", i=row)
        return found.scalar_one()

    assert (await mine.browser.get(f"/api/classes/{mine.class_id}")).status_code == 200
    assert calls == [mine.owner]  # the class view
    assert state(orphan) == "uncertain"
    assert state(other) == "pending"  # only the caller's rows
    mine.sql("UPDATE external_operations SET state = 'pending' WHERE id = :i", i=orphan)
    path = f"/api/classes/{mine.class_id}"
    assert reason(await delete(mine.browser, path)) == (409, "uncertain_write")
    assert calls == [mine.owner, mine.owner]
    assert state(orphan) == "uncertain"  # the refusal did not roll the marking back


def test_the_seams_are_the_operations_modules_own() -> None:
    """No shim: class delete uses X2's ``resolve`` and its one ``actionable_at`` rule."""
    from classbeat.operations import orphans, requests

    assert seams.resolve_orphans is orphans.resolve
    assert seams.NOT_ACTIONABLE_SQL is requests.NOT_ACTIONABLE_SQL


async def test_users_without_classes_get_an_empty_list(browsers: Any) -> None:
    browser: Browser = await browsers(mowl=False)
    assert (await browser.get("/api/classes")).json() == {"classes": [], "next_cursor": None}


async def test_the_listing_pages_with_a_cursor(browsers: Any, jobs_admin: Connection) -> None:
    browser: Browser = await browsers(mowl=False)
    base = datetime.now(UTC) - timedelta(hours=1)
    ids = [new_class(jobs_admin, browser.user_id, f"C{i}") for i in range(5)]
    for i, class_id in enumerate(ids):  # three share one instant: the id breaks the tie
        jobs_admin.execute(
            text("UPDATE classes SET updated_at = :t WHERE id = :c"),
            {"t": base + timedelta(minutes=max(i - 2, 0)), "c": class_id},
        )
    everything = (await browser.get("/api/classes")).json()
    assert everything["next_cursor"] is None
    expected = [c["id"] for c in everything["classes"]]
    assert len(expected) == 5

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        query = "limit=2" + ("" if cursor is None else f"&cursor={cursor}")
        page = (await browser.get(f"/api/classes?{query}")).json()
        assert len(page["classes"]) <= 2
        seen += [c["id"] for c in page["classes"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == expected  # no gap, no repeat

    for bad in ("limit=0", "limit=501", "limit=x"):
        assert (await browser.get(f"/api/classes?{bad}")).status_code == 422
    naive = json.dumps(["2026-01-01T00:00:00", expected[0]]).encode()  # no offset
    naive_cursor = base64.urlsafe_b64encode(naive).decode().rstrip("=")
    for cursor in ("!!", "e30", "W10", naive_cursor):  # not base64, {}, [], a naive time
        refused = await browser.get(f"/api/classes?cursor={cursor}")
        assert reason(refused) == (422, "invalid_cursor")


async def test_the_list_profile_is_the_current_revision_and_never_another_users(
    two: tuple[Plant, Plant],
) -> None:
    mine, theirs = two
    # Their current revision has a zone and a length no draft of mine has.
    foreign_content = {
        **DRAFT,
        "segments": [{"name": "X", "kind": "main", "intervals": [{"sec": 777, "zone": 7}]}],
    }
    theirs.sql(
        "UPDATE class_revisions SET content = CAST(:c AS jsonb) WHERE id = :r",
        c=json.dumps(foreign_content),
        r=theirs.r1,
    )
    no_current = new_class(mine.admin, mine.owner, "Empty")

    listed = (await mine.browser.get("/api/classes")).json()["classes"]
    by_id = {c["id"]: c for c in listed}
    assert by_id[str(mine.class_id)]["profile"] == [[2, 60], [3, 120], [5, 300]]
    assert by_id[str(no_current)]["profile"] == []
    assert all([7, 777] not in c["profile"] for c in listed)

    theirs_listed = (await theirs.browser.get("/api/classes")).json()["classes"]
    assert [c["profile"] for c in theirs_listed] == [[[7, 777]]]
    assert str(mine.class_id) not in json.dumps(theirs_listed)

    # The detail answer carries the same field, for the owner only.
    detail = await mine.browser.get(f"/api/classes/{mine.class_id}")
    assert detail.json()["profile"] == [[2, 60], [3, 120], [5, 300]]
    assert (await mine.browser.get(f"/api/classes/{theirs.class_id}")).status_code == 404


async def test_a_bad_interval_is_left_out_of_the_profile_and_never_fails_the_list(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    odd = {
        **DRAFT,
        "segments": [
            {
                "name": "X",
                "kind": "main",
                "intervals": [
                    {"sec": 60, "zone": 2.5},  # not a whole zone
                    {"sec": 60.5, "zone": 3},  # not a whole length
                    {"sec": "90", "zone": 3},  # a string, not a number
                    {"sec": -5, "zone": 3},
                    {"sec": 10**9, "zone": 3},
                    {"zone": 4},
                    "junk",
                    {"sec": 120, "zone": 4},
                ],
            }
        ],
    }
    mine.sql(
        "UPDATE class_revisions SET content = CAST(:c AS jsonb) WHERE id = :r",
        c=json.dumps(odd),
        r=mine.r1,
    )
    listed = await mine.browser.get("/api/classes")
    assert listed.status_code == 200
    assert listed.json()["classes"][0]["profile"] == [[4, 120]]
