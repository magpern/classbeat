"""The class API's saved summary against real saves (#167, ADR 0007 sections 5, 9 and 12): the
``latest_saved_workout_id`` it shows is the row a real ``save_workout`` job wrote, Update's
``admit`` accepts it for that class and refuses it for another, and a MOWL disconnect or
reconnect detaches the rows. The in-process fake MOWL (``savetools``); never the network.
"""

from typing import Any
from uuid import UUID

from classbeat.connections import repository
from ctx import make_context
from optools import TOKEN_A, TOKEN_B
from savetools import FakeMowlPrograms
from updatetools import Live, UpdateEnv


async def saved_of(browser: Any, class_id: UUID) -> dict[str, Any]:
    shown = await browser.browser.get(f"/api/classes/{class_id}")
    assert shown.status_code == 200, shown.text
    return shown.json()["saved"]


def no_program_id(live: Live, body: dict[str, Any]) -> None:
    assert str(live.program_id) not in str(body)
    assert "program_id" not in str(body)


async def test_the_shown_id_is_accepted_by_update_for_its_class_and_refused_for_another(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    sibling = await uenv.sibling(live)  # another class of the same user, saved
    browser = await update_browser(live)

    mine = await saved_of(browser, live.saved.class_id)
    theirs = await saved_of(browser, sibling.saved.class_id)
    assert mine["latest_saved_workout_id"] == str(live.saved_workout_id)
    assert theirs["latest_saved_workout_id"] == str(sibling.saved_workout_id)
    assert (mine["count"], mine["detached_count"]) == (1, 0)
    no_program_id(live, mine)
    no_program_id(sibling, theirs)

    # The sibling class's shown id, named under my class: refused, nothing created.
    jobs = "SELECT count(*) FROM jobs WHERE owner_id = :o"
    before = uenv.sql(jobs, o=live.saved.owner).scalar_one()
    refused = await browser.update(saved_workout_id=theirs["latest_saved_workout_id"])
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "invalid_input")
    assert uenv.sql(jobs, o=live.saved.owner).scalar_one() == before

    # My class's shown id: accepted.
    accepted = await browser.update(saved_workout_id=mine["latest_saved_workout_id"])
    assert accepted.status_code == 201, accepted.text


async def test_a_reconnect_detaches_the_rows_and_the_summary_shows_no_id(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    browser = await update_browser(live)
    await uenv.s.ops.connect(live.saved.owner, TOKEN_A)  # a reconnect, even to the same account
    summary = await saved_of(browser, live.saved.class_id)
    assert (summary["count"], summary["latest_saved_workout_id"]) == (0, None)
    assert summary["detached_count"] == 1
    assert summary["latest_up_to_date"] is None


async def test_a_disconnect_detaches_the_rows_and_the_summary_shows_no_id(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    other = await uenv.live(TOKEN_B)  # another user, also saved: untouched by my disconnect
    browser = await update_browser(live)
    other_browser = await update_browser(other)
    ctx = make_context(live.saved.owner)
    assert await repository.disconnect(uenv.s.ops.factory, ctx, "mowl")
    summary = await saved_of(browser, live.saved.class_id)
    assert (summary["count"], summary["latest_saved_workout_id"]) == (0, None)
    assert summary["detached_count"] == 1
    theirs = await saved_of(other_browser, other.saved.class_id)
    assert (theirs["count"], theirs["latest_saved_workout_id"]) == (1, str(other.saved_workout_id))
    assert theirs["detached_count"] == 0
