"""The class history (ADR 0007 amendment of 2026-11-03, part H; issue #545): the timeline and the
comparison of two revisions, against Postgres. Setup rows are written as the superuser; the routes
run as ``classbeat_app`` through the web app. No fake is called: both routes are reads."""

import hashlib
import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobapi import Browser
from jobtools import new_class

pytestmark = pytest.mark.usefixtures("jobs_admin")

LINK = str(uuid.uuid4())
OTHER_LINK = str(uuid.uuid4())
FINGERPRINT = "ab" * 32
RUN = str(uuid.uuid4())
NOT_FOUND = {"detail": {"reason": "not_found"}}


def interval(sec: int, zone: int) -> dict[str, Any]:
    return {"sec": sec, "zone": zone, "rpm": [80, 90], "standing": False, "cue": ""}


def draft(name: str, *intervals: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": "",
        "segments": [
            {"name": "Warm up", "kind": "warmup", "intervals": [interval(120, 2)]},
            {"name": "Main", "kind": "main", "intervals": list(intervals)},
        ],
    }


V1 = draft("v1", interval(300, 4), interval(120, 3))
V2 = draft("v2", interval(300, 5), interval(120, 3), interval(180, 4))


def digest(content: dict[str, Any]) -> bytes:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).digest()


def insert(conn: Connection, table: str, **values: Any) -> UUID:
    values = {"id": uuid.uuid4(), **values}
    jsonb = {"content", "music", "provenance", "brief"}
    params = ", ".join(f"CAST(:{k} AS jsonb)" if k in jsonb else f":{k}" for k in values)
    row = {k: json.dumps(v) if k in jsonb else v for k, v in values.items()}
    return conn.execute(
        text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({params}) RETURNING id"),  # noqa: S608
        row,
    ).scalar_one()


def provenance(**more: Any) -> dict[str, Any]:
    return {"provider": "fake", "model": "m", "agent_run_ids": [RUN], "cost_micros": 7, **more}


def music(**more: Any) -> dict[str, Any]:
    return {"link_id": LINK, "fingerprint": FINGERPRINT, "unknown_bpm": [], **more}


def revision(
    conn: Connection,
    owner: UUID,
    class_id: UUID,
    number: int,
    content: dict[str, Any],
    **more: Any,
) -> UUID:
    values: dict[str, Any] = {"music": music(), "provenance": provenance(), **more}
    return insert(
        conn,
        "class_revisions",
        owner_id=owner,
        class_id=class_id,
        number=number,
        kind="draft",
        content=content,
        content_hash=digest(content),
        **values,
    )


def sql(conn: Connection, statement: str, **params: Any) -> Any:
    return conn.execute(text(statement), params)


class Plant:
    """A class with a design (r1), a chat revision (r2, current, saved) and a draft on picked
    songs (r3); r1 also has a detached saved row."""

    def __init__(self, admin: Connection, browser: Browser) -> None:
        self.admin, self.browser, self.owner = admin, browser, browser.user_id
        o = self.owner
        self.class_id = new_class(admin, o)
        self.r1 = revision(
            admin, o, self.class_id, 1, V1, provenance=provenance(origin="design_draft")
        )
        self.message = insert(
            admin, "class_messages", owner_id=o, class_id=self.class_id, seq=1, role="user",
            body="Make the main set harder",
        )  # fmt: skip
        self.r2 = revision(
            admin, o, self.class_id, 2, V2, parent_id=self.r1,
            music=music(unknown_bpm=[{"index": 1, "title": "A Song"}]),
            provenance=provenance(
                origin="revise_draft", base_revision_id=str(self.r1),
                request_message_id=str(self.message),
            ),
        )  # fmt: skip
        self.r3 = revision(
            admin, o, self.class_id, 3, draft("v3", interval(240, 3)), parent_id=self.r2,
            music=music(song_list_id=str(uuid.uuid4()), link_id=OTHER_LINK, fingerprint="cd" * 32),
            provenance=provenance(origin="design_draft"),
        )  # fmt: skip
        sql(
            admin,
            "UPDATE classes SET current_revision_id = :r WHERE id = :c",
            r=self.r2,
            c=self.class_id,
        )
        self.saved(self.r2, 2, V2, program_id=41)
        self.saved(self.r1, 1, V1, program_id=40, detached_at=True)

    def saved(
        self, revision_id: UUID, number: int, content: dict[str, Any], *, program_id: int,
        detached_at: bool = False,
    ) -> None:  # fmt: skip
        sql(
            self.admin,
            "INSERT INTO saved_workouts (id, owner_id, class_id, program_id, revision_id, "
            "revision_number, content_hash, name, detached_at) VALUES (:id, :o, :c, :p, :r, :n, "
            ":h, 'x', CASE WHEN :d THEN now() END)",
            id=uuid.uuid4(), o=self.owner, c=self.class_id, p=program_id, r=revision_id, n=number,
            h=digest(content), d=detached_at,
        )  # fmt: skip


@pytest.fixture
async def two(browsers: Any, jobs_admin: Connection) -> tuple[Plant, Plant]:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    return Plant(jobs_admin, a), Plant(jobs_admin, b)


def leaks(body: str) -> list[str]:
    return [
        s for s in (LINK, OTHER_LINK, FINGERPRINT, RUN, '"link_id"', '"fingerprint"') if s in body
    ]


async def test_the_timeline_says_what_asked_for_each_revision_and_where_it_stands(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    got = await mine.browser.get(f"/api/classes/{mine.class_id}/history")
    assert got.status_code == 200
    assert leaks(got.text) == []
    body = got.json()
    assert body["sport"] == "cycling"
    assert body["can_change"] is False  # the planted class has no brief or music source
    r1, r2, r3 = body["revisions"]
    assert [r["number"] for r in (r1, r2, r3)] == [1, 2, 3]
    assert (r1["origin"], r1["request"], r1["saved"], r1["is_current"]) == (
        "design_draft", None, "detached", False,
    )  # fmt: skip
    assert (r2["origin"], r2["request"], r2["saved"], r2["is_current"]) == (
        "revise_draft", "Make the main set harder", "live", True,
    )  # fmt: skip
    assert r2["parent_number"] == 1
    assert (r3["from_songs"], r3["saved"], r1["from_songs"]) == (True, "none", False)
    assert r1["content_hash"] == digest(V1).hex()
    assert r1["summary"] == {"segments": 2, "intervals": 3, "total_sec": 540}
    assert r2["summary"]["total_sec"] == 720
    assert isinstance(r1["tss"], float)
    assert r2["tss"] > r1["tss"]
    assert all(r["duplicated_from"] is None for r in (r1, r2, r3))


async def test_a_duplicate_names_the_class_it_came_from_while_that_class_is_the_users(
    two: tuple[Plant, Plant],
) -> None:
    mine, theirs = two
    copy = new_class(mine.admin, mine.owner, "Copy")
    for number, source in ((1, mine.class_id), (2, uuid.uuid4()), (3, theirs.class_id)):
        revision(
            mine.admin, mine.owner, copy, number, V1,
            provenance=provenance(
                origin="duplicate", agent_run_ids=[],
                duplicated_from={"class_id": str(source), "revision_id": str(mine.r2), "number": 2},
            ),
        )  # fmt: skip
    body = (await mine.browser.get(f"/api/classes/{copy}/history")).json()
    named, gone, foreign = (r["duplicated_from"] for r in body["revisions"])
    assert named == {"class_id": str(mine.class_id), "class_title": "Tuesday climb", "number": 2}
    assert gone == {"class_id": None, "class_title": None, "number": 2}
    assert foreign == {"class_id": None, "class_title": None, "number": 2}  # never their title


async def test_compare_says_what_changed_with_booleans_for_the_music(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    base = f"/api/classes/{mine.class_id}/history/compare"
    got = await mine.browser.get(f"{base}?from={mine.r1}&to={mine.r2}")
    assert got.status_code == 200
    assert leaks(got.text) == []
    body = got.json()
    assert (body["from_number"], body["to_number"]) == (1, 2)
    assert body["segments"] == {"added": 0, "removed": 0}
    assert body["intervals"] == {"added": 1, "removed": 0, "changed": 1}
    assert body["duration_sec"] == {"before": 540, "after": 720}
    assert body["tss"]["after"] > body["tss"]["before"]
    assert body["text_changed"] is True  # the workout's name
    assert body["warnings"] == {
        "from": {"unknown_bpm": 0, "unsure_beat_one": 0},
        "to": {"unknown_bpm": 1, "unsure_beat_one": 0},
    }
    assert body["music"] == {"same_source": True, "same_fingerprint": True}
    other = (await mine.browser.get(f"{base}?from={mine.r2}&to={mine.r3}")).json()
    assert other["music"] == {"same_source": False, "same_fingerprint": False}
    same = (await mine.browser.get(f"{base}?from={mine.r1}&to={mine.r1}")).json()
    assert same["intervals"] == {"added": 0, "removed": 0, "changed": 0}


async def test_another_users_class_or_revisions_and_malformed_ids_are_not_found(
    two: tuple[Plant, Plant],
) -> None:
    mine, theirs = two
    elsewhere = new_class(mine.admin, mine.owner, "Other")
    foreign_rev = revision(mine.admin, mine.owner, elsewhere, 1, V1)
    song_list = insert(
        mine.admin, "class_revisions", owner_id=mine.owner, class_id=mine.class_id, number=9,
        kind="song_list", content={"songs": []}, content_hash=digest({"songs": []}),
        music={}, provenance=provenance(),
    )  # fmt: skip
    for path in (
        f"/api/classes/{theirs.class_id}/history",
        f"/api/classes/{uuid.uuid4()}/history",
        "/api/classes/nope/history",
        f"/api/classes/{theirs.class_id}/history/compare?from={theirs.r1}&to={theirs.r2}",
        f"/api/classes/{mine.class_id}/history/compare?from={theirs.r1}&to={mine.r2}",
        f"/api/classes/{mine.class_id}/history/compare?from={mine.r1}&to={foreign_rev}",
        f"/api/classes/{mine.class_id}/history/compare?from={mine.r1}&to={song_list}",
        f"/api/classes/{mine.class_id}/history/compare?from=nope&to={mine.r2}",
    ):
        got = await mine.browser.get(path)
        assert (got.status_code, got.json()) == (404, NOT_FOUND), path
    # The song list is not a timeline entry.
    listed = (await mine.browser.get(f"/api/classes/{mine.class_id}/history")).json()
    assert [r["number"] for r in listed["revisions"]] == [1, 2, 3]


async def test_a_stored_draft_that_no_longer_validates_is_internal_with_the_reason_only(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    broken = {"name": "x", "segments": [{"name": "y", "kind": "main", "intervals": "no"}]}
    bad = revision(mine.admin, mine.owner, mine.class_id, 4, broken)
    for path in (
        f"/api/classes/{mine.class_id}/history",
        f"/api/classes/{mine.class_id}/history/compare?from={mine.r1}&to={bad}",
    ):
        got = await mine.browser.get(path)
        assert (got.status_code, got.json()) == (500, {"detail": {"reason": "internal"}}), path


async def test_a_soundless_class_cannot_be_changed_while_its_revise_is_refused(
    two: tuple[Plant, Plant],
) -> None:
    """``can_change`` asks ``music_kind.admit_class``, as the revise's ``admit`` does: a soundless
    class is refused until SL2 (#528), so it is false (and then follows the gate)."""
    mine, _ = two
    quiet = new_class(mine.admin, mine.owner, "Quiet")
    sql(mine.admin, "UPDATE classes SET brief = CAST(:b AS jsonb), "
        "music_source = CAST(:m AS jsonb) WHERE id = :c",
        b=json.dumps({"length_min": 30}), m=json.dumps({"source": "none"}), c=quiet)  # fmt: skip
    revision(mine.admin, mine.owner, quiet, 1, V1, music={"source": "none"})
    body = (await mine.browser.get(f"/api/classes/{quiet}/history")).json()
    assert body["can_change"] is False
    assert [r["number"] for r in body["revisions"]] == [1]


async def test_a_running_class_is_read_with_the_running_schema(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    run = new_class(mine.admin, mine.owner, "Run")
    sql(mine.admin, "UPDATE classes SET brief = CAST(:b AS jsonb) WHERE id = :c",
        b=json.dumps({"sport": "running", "length_min": 30}), c=run)  # fmt: skip

    def running(name: str, *zones: int) -> dict[str, Any]:
        intervals = [{"sec": 300, "zone": z, "gradient": 0, "cue": ""} for z in zones]
        return {"name": name, "description": "", "segments": [
            {"name": "Run", "kind": "main", "intervals": intervals}]}  # fmt: skip

    a = revision(mine.admin, mine.owner, run, 1, running("a", 2, 3))
    b = revision(mine.admin, mine.owner, run, 2, running("b", 2, 4, 3), parent_id=a)
    body = (await mine.browser.get(f"/api/classes/{run}/history")).json()
    assert body["sport"] == "running"
    assert body["can_change"] is False  # a brief but no music source
    source = {"source": "mowl_spotify_playlist", "spotify_playlist_id": "x" * 22}
    sql(mine.admin, "UPDATE classes SET music_source = CAST(:m AS jsonb) WHERE id = :c",
        m=json.dumps(source), c=run)  # fmt: skip
    assert (await mine.browser.get(f"/api/classes/{run}/history")).json()["can_change"] is True
    assert [r["summary"]["total_sec"] for r in body["revisions"]] == [600, 900]
    change = (await mine.browser.get(f"/api/classes/{run}/history/compare?from={a}&to={b}")).json()
    assert change["intervals"] == {"added": 1, "removed": 0, "changed": 1}
    # A cycling draft under a running brief does not validate: never shown with the wrong schema.
    revision(mine.admin, mine.owner, run, 3, V1)
    got = await mine.browser.get(f"/api/classes/{run}/history")
    assert got.status_code == 500
