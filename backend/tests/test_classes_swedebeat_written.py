"""``swedebeat_written`` on the class list and the class page (ADR 0036 section 6).

True only when an output attempt of the class wrote to SwedeBeat (a playlist or a mix: done, in
doubt, published but not listed, or with an external id); the class delete card says it stays
there. Setup rows are written as the superuser; the reads go through the web app.
"""

from typing import Any

import pytest
from sqlalchemy.engine import Connection

from jobapi import Browser
from test_classes_api import Plant

pytestmark = pytest.mark.usefixtures("jobs_admin")


@pytest.fixture
async def mine(browsers: Any, jobs_admin: Connection) -> Plant:
    return Plant(jobs_admin, await browsers(mowl=False))


def output(plant: Plant, name: str, state: str, external_id: str | None = None) -> None:
    plant.sql(
        "INSERT INTO class_outputs (owner_id, class_id, revision_id, output, attempt, state, "
        "external_id) VALUES (:o, :c, :r, :out, 1, :s, :e)",
        o=plant.owner,
        c=plant.class_id,
        r=plant.r1,
        out=name,
        s=state,
        e=external_id,
    )


async def written(browser: Browser, plant: Plant) -> tuple[bool, bool]:
    listed = (await browser.get("/api/classes")).json()["classes"]
    [row] = [c for c in listed if c["id"] == str(plant.class_id)]
    shown = (await browser.get(f"/api/classes/{plant.class_id}")).json()
    return row["swedebeat_written"], shown["swedebeat_written"]


async def test_a_class_with_no_swedebeat_output_is_not_marked(mine: Plant) -> None:
    assert await written(mine.browser, mine) == (False, False)


@pytest.mark.parametrize("name", ["swedebeat_playlist", "swedebeat_mix"])
@pytest.mark.parametrize("state", ["done", "in_doubt", "published_not_listed"])
async def test_a_written_or_doubtful_swedebeat_output_marks_the_class(
    mine: Plant, name: str, state: str
) -> None:
    output(mine, name, state)
    assert await written(mine.browser, mine) == (True, True)


async def test_a_failed_output_with_nothing_written_does_not_mark_it(mine: Plant) -> None:
    output(mine, "swedebeat_mix", "failed")
    assert await written(mine.browser, mine) == (False, False)


async def test_a_failed_output_that_kept_an_external_id_marks_it(mine: Plant) -> None:
    output(mine, "swedebeat_playlist", "failed", external_id="stored-id")
    assert await written(mine.browser, mine) == (True, True)


@pytest.mark.parametrize("name", ["spotify_playlist", "mowl"])
async def test_other_outputs_do_not_mark_it(mine: Plant, name: str) -> None:
    output(mine, name, "done", external_id="x")
    assert await written(mine.browser, mine) == (False, False)
