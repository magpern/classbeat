"""``studio`` on the class list and the class page (ADR 0036 section 5, #1173).

True only when the class's stored ``music_source`` is the studio source; false for every other
class (Spotify, MOWL, soundless, no source yet, a source with no ``source`` key). The page reads it
to skip ``GET /classes/{id}/timeline``. The pure tests need no database; the API tests run as the
web app, and another user's studio class is neither listed nor shown.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.classes.class_list_sql import CLASSES
from classbeat.classes.schemas import ClassOut
from classbeat.swedebeat_studio.song_list_model import SOURCE
from jobapi import Browser
from jobtools import new_class
from test_classes_api import Plant

# -- pure: the mapping and the statement -------------------------------------------------------


def row(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "00000000-0000-4000-8000-000000000001",
        "title": "t",
        "created_at": "2026-11-18T10:00:00+00:00",
        "updated_at": "2026-11-18T10:00:00+00:00",
        "current_revision_id": None,
        "current_revision_number": None,
        "revision_count": 0,
        "open_job_id": None,
        "saved_count": 0,
        "saved_revision_number": None,
        "saved_at": None,
        "saved_up_to_date": None,
        "saved_workout_id": None,
        "saved_verified": None,
        "detached_count": 0,
        "profile": [],
        "tss_approximate": False,
        "sport": "cycling",
        "soundless": False,
        "studio": False,
        "spotify": False,
        "swedebeat_written": False,
        "request": None,
        "song_count": None,
    }
    return SimpleNamespace(**{**base, **over})


@pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False), (None, False)])
def test_the_summary_is_studio_only_when_the_row_says_true(value: Any, expected: bool) -> None:
    assert ClassOut.of(row(studio=value)).studio is expected


def test_the_wire_name_is_studio_and_it_defaults_to_false() -> None:
    assert ClassOut.of(row(studio=True)).model_dump()["studio"] is True
    assert ClassOut.model_fields["studio"].default is False


def test_the_statement_compares_the_stored_source_with_the_studio_constant() -> None:
    sql = str(CLASSES)
    assert f"c.music_source ->> 'source' = '{SOURCE}'" in sql
    assert "AS studio" in sql
    assert SOURCE == "swedebeat_studio"  # the wire value the page and the ADR name


needs_db = pytest.mark.usefixtures("jobs_admin")

# -- database: the list and the detail, owner-scoped -------------------------------------------


def set_source(admin: Connection, class_id: Any, source: dict[str, Any] | None) -> None:
    admin.execute(
        text("UPDATE classes SET music_source = CAST(:s AS jsonb) WHERE id = :c"),
        {"s": None if source is None else json.dumps(source), "c": class_id},
    )


@pytest.fixture
async def mine(browsers: Any, jobs_admin: Connection) -> Plant:
    return Plant(jobs_admin, await browsers(mowl=False))


async def flags(browser: Browser, class_id: Any) -> tuple[bool, bool]:
    listed = (await browser.get("/api/classes")).json()["classes"]
    [item] = [c for c in listed if c["id"] == str(class_id)]
    shown = (await browser.get(f"/api/classes/{class_id}")).json()
    return item["studio"], shown["studio"]


@needs_db
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ({"source": SOURCE}, True),
        ({"source": "none"}, False),
        ({"source": "mowl_playlist"}, False),
        ({"source": "spotify"}, False),
        ({"source": "swedebeat_studio_x"}, False),
        ({}, False),
        (None, False),
    ],
)
async def test_a_class_is_studio_only_for_the_studio_source(
    mine: Plant, source: dict[str, Any] | None, expected: bool
) -> None:
    set_source(mine.admin, mine.class_id, source)
    assert await flags(mine.browser, mine.class_id) == (expected, expected)


@needs_db
async def test_another_users_studio_class_is_not_in_my_list(
    browsers: Any, jobs_admin: Connection
) -> None:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    theirs = new_class(jobs_admin, b.user_id)
    set_source(jobs_admin, theirs, {"source": SOURCE})
    mine = new_class(jobs_admin, a.user_id)
    listed = (await a.get("/api/classes")).json()["classes"]
    assert {c["id"]: c["studio"] for c in listed} == {str(mine): False}
    assert (await a.get(f"/api/classes/{theirs}")).status_code == 404
