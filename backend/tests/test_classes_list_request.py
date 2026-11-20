"""The class list's ``request`` and ``song_count`` (#1234): the user's own request text and the
number of songs the current revision plays, owner-scoped like every column of the list."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from classbeat.classes.kinds import SONG_DESIGN_KIND
from jobapi import Browser
from jobtools import new_class, new_job
from test_classes_api import Plant, by_class

pytestmark = pytest.mark.usefixtures("jobs_admin")


# Fixture note: the rows below are planted with the admin connection, but the foreign keys still
# bind them. `class_revisions` and `jobs` reference `classes` by the composite key `(owner_id,
# class_id)` (migration 0004), so a revision or job must carry the same `owner_id` as its class;
# `Plant` and `new_class` do, which is why each user's rows can never point at the other's class.
@pytest.fixture
async def two(browsers: Any, jobs_admin: Connection) -> tuple[Plant, Plant]:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    return Plant(jobs_admin, a), Plant(jobs_admin, b)


def set_brief(plant: Plant, brief: Any) -> None:
    plant.sql(
        "UPDATE classes SET brief = CAST(:b AS jsonb) WHERE id = :c",
        b=json.dumps(brief),
        c=plant.class_id,
    )


def set_songs(plant: Plant, songs: Any) -> None:
    plant.sql(
        "UPDATE class_revisions SET music = music || CAST(:m AS jsonb) WHERE id = :r",
        m=json.dumps({"songs": songs}),
        r=plant.r1,
    )


def songs_of(count: int) -> list[dict[str, Any]]:
    return [
        {"song": n, "start": n * 200.0, "end": n * 200.0 + 190, "bpm": 90.0, "estimate": False}
        for n in range(1, count + 1)
    ]


async def listed(plant: Plant) -> dict[str, Any]:
    return by_class(await plant.browser.get("/api/classes"), plant)


async def test_a_class_without_a_request_or_songs_reads_null(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    row = await listed(mine)
    assert row["request"] is None
    assert row["song_count"] is None


async def test_the_request_is_the_stored_brief_text(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    set_brief(mine, {"length_min": 45, "text": "A hilly 45 minutes, 80s rock"})
    assert (await listed(mine))["request"] == "A hilly 45 minutes, 80s rock"
    # The detail answer carries the same, for the owner only.
    detail = await mine.browser.get(f"/api/classes/{mine.class_id}")
    assert detail.json()["request"] == "A hilly 45 minutes, 80s rock"


async def test_a_long_request_is_cut_to_280_characters_after_the_trim(
    two: tuple[Plant, Plant],
) -> None:
    mine, _ = two
    set_brief(mine, {"length_min": 45, "text": "  " + "a" * 300})
    assert (await listed(mine))["request"] == "a" * 280
    set_brief(mine, {"length_min": 45, "text": "b" * 280})
    assert (await listed(mine))["request"] == "b" * 280


@pytest.mark.parametrize("blank", ["", "   "])
async def test_a_blank_request_reads_null(two: tuple[Plant, Plant], blank: str) -> None:
    mine, _ = two
    set_brief(mine, {"length_min": 45, "text": blank})
    assert (await listed(mine))["request"] is None


async def test_a_brief_without_text_reads_null(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    set_brief(mine, {"length_min": 45})
    assert (await listed(mine))["request"] is None


async def test_before_a_design_the_request_is_the_newest_song_design_jobs_brief(
    two: tuple[Plant, Plant], jobs_admin: Connection
) -> None:
    mine, theirs = two
    first = new_class(jobs_admin, mine.owner, "Fresh")
    new_job(
        jobs_admin,
        mine.owner,
        first,
        kind=SONG_DESIGN_KIND,
        state="completed",
        finished_at=datetime.now(UTC),
        input=json.dumps({"brief": {"length_min": 30, "text": "older ask"}}),
    )
    new_job(
        jobs_admin,
        mine.owner,
        first,
        kind=SONG_DESIGN_KIND,
        state="queued",
        input=json.dumps({"brief": {"length_min": 30, "text": "newer ask"}}),
    )
    # Another user's job never reaches my class, even for the same kind.
    new_job(
        jobs_admin,
        theirs.owner,
        new_class(jobs_admin, theirs.owner, "Theirs"),
        kind=SONG_DESIGN_KIND,
        input=json.dumps({"brief": {"length_min": 30, "text": "not mine"}}),
    )
    rows = {c["id"]: c for c in (await mine.browser.get("/api/classes")).json()["classes"]}
    assert rows[str(first)]["request"] == "newer ask"
    assert rows[str(mine.class_id)]["request"] is None
    assert "not mine" not in json.dumps(rows)


async def test_a_job_of_another_kind_is_not_a_request(
    two: tuple[Plant, Plant], jobs_admin: Connection
) -> None:
    mine, _ = two
    other = new_class(jobs_admin, mine.owner, "Other")
    new_job(
        jobs_admin,
        mine.owner,
        other,
        input=json.dumps({"brief": {"length_min": 30, "text": "wrong kind"}}),
    )
    rows = {c["id"]: c for c in (await mine.browser.get("/api/classes")).json()["classes"]}
    assert rows[str(other)]["request"] is None


async def test_the_stored_brief_wins_over_a_job(
    two: tuple[Plant, Plant], jobs_admin: Connection
) -> None:
    mine, _ = two
    set_brief(mine, {"length_min": 45, "text": "stored"})
    new_job(
        jobs_admin,
        mine.owner,
        mine.class_id,
        kind=SONG_DESIGN_KIND,
        state="completed",
        finished_at=datetime.now(UTC),
        input=json.dumps({"brief": {"length_min": 45, "text": "from the job"}}),
    )
    assert (await listed(mine))["request"] == "stored"


async def test_the_song_count_is_the_current_revisions_songs(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    set_songs(mine, songs_of(12))
    assert (await listed(mine))["song_count"] == 12
    # Only the current revision counts: r2 has songs, r1 (current) has none.
    mine.sql(
        "UPDATE class_revisions SET music = music || CAST(:m AS jsonb) WHERE id = :r",
        m=json.dumps({"songs": songs_of(3)}),
        r=mine.r2,
    )
    set_songs(mine, [])
    assert (await listed(mine))["song_count"] is None


async def test_a_soundless_or_songless_class_has_no_song_count(two: tuple[Plant, Plant]) -> None:
    mine, _ = two
    mine.sql(
        "UPDATE class_revisions SET music = CAST(:m AS jsonb) WHERE id = :r",
        m=json.dumps({"source": "none"}),
        r=mine.r1,
    )
    assert (await listed(mine))["song_count"] is None
    set_songs(mine, "not a list")
    assert (await listed(mine))["song_count"] is None


async def test_another_users_request_and_songs_never_reach_my_list(
    two: tuple[Plant, Plant],
) -> None:
    mine, theirs = two
    set_brief(theirs, {"length_min": 45, "text": "their secret ask"})
    set_songs(theirs, songs_of(9))
    mine_listed = (await mine.browser.get("/api/classes")).json()["classes"]
    assert [c["id"] for c in mine_listed] == [str(mine.class_id)]
    assert "their secret ask" not in json.dumps(mine_listed)
    assert mine_listed[0]["song_count"] is None
    theirs_row = await listed(theirs)
    assert (theirs_row["request"], theirs_row["song_count"]) == ("their secret ask", 9)
    assert (await mine.browser.get(f"/api/classes/{theirs.class_id}")).status_code == 404
