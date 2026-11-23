"""``spotify`` on the class list and the class page (#1453).

True only when the class's stored ``music_source`` is a Spotify playlist's
(``mowl_spotify_playlist``, picked songs included); false for a SwedeBeat mix, a studio class, a
soundless class, a MOWL playlist, no source yet or a source with no ``source`` key. The page reads
it to tell a Spotify draft from a SwedeBeat-mix class. The API tests run as the web app, and
another user's Spotify class is neither listed nor shown.
"""

from typing import Any

import pytest
from sqlalchemy.engine import Connection

from classbeat.classes.class_list_sql import CLASSES
from classbeat.classes.schemas import ClassOut
from classbeat.design.revision import MUSIC_SOURCE
from classbeat.swedebeat_studio.song_list_model import SOURCE as STUDIO
from jobapi import Browser
from jobtools import new_class
from test_classes_api import Plant
from test_classes_studio_flag import mine, row, set_source

__all__ = ["mine"]  # the fixture, shared with the studio flag's tests


@pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False), (None, False)])
def test_the_summary_is_spotify_only_when_the_row_says_true(value: Any, expected: bool) -> None:
    assert ClassOut.of(row(spotify=value)).spotify is expected


def test_the_wire_name_is_spotify_and_it_defaults_to_false() -> None:
    assert ClassOut.of(row(spotify=True)).model_dump()["spotify"] is True
    assert ClassOut.model_fields["spotify"].default is False


def test_the_statement_compares_the_stored_source_with_the_spotify_constant() -> None:
    sql = str(CLASSES)
    assert f"c.music_source ->> 'source' = '{MUSIC_SOURCE}'" in sql
    assert "AS spotify" in sql
    assert MUSIC_SOURCE == "mowl_spotify_playlist"  # the source a Spotify draft's class stores


needs_db = pytest.mark.usefixtures("jobs_admin")


async def flags(browser: Browser, class_id: Any) -> tuple[bool, bool]:
    listed = (await browser.get("/api/classes")).json()["classes"]
    [item] = [c for c in listed if c["id"] == str(class_id)]
    shown = (await browser.get(f"/api/classes/{class_id}")).json()
    return item["spotify"], shown["spotify"]


@needs_db
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ({"source": MUSIC_SOURCE, "playlist_id": "p"}, True),
        ({"source": MUSIC_SOURCE, "origin": "song_pick"}, True),
        ({"source": "mowl_swedebeat_mix", "mix_id": "m"}, False),
        ({"source": STUDIO}, False),
        ({"source": "none"}, False),
        ({"source": "mowl_playlist"}, False),
        ({"source": "spotify"}, False),
        ({}, False),
        (None, False),
    ],
)
async def test_a_class_is_spotify_only_for_the_spotify_playlist_source(
    mine: Plant, source: dict[str, Any] | None, expected: bool
) -> None:
    set_source(mine.admin, mine.class_id, source)
    assert await flags(mine.browser, mine.class_id) == (expected, expected)


@needs_db
async def test_another_users_spotify_class_is_not_in_my_list(
    browsers: Any, jobs_admin: Connection
) -> None:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    theirs = new_class(jobs_admin, b.user_id)
    set_source(jobs_admin, theirs, {"source": MUSIC_SOURCE})
    own = new_class(jobs_admin, a.user_id)
    listed = (await a.get("/api/classes")).json()["classes"]
    assert {c["id"]: c["spotify"] for c in listed} == {str(own): False}
    assert (await a.get(f"/api/classes/{theirs}")).status_code == 404
