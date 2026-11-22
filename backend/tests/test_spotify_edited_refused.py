"""#1424, ADR 0042 section 3 ("On AI regeneration"): an AI revise, a song revise and a refit of an
edited Spotify draft are refused ``has_user_edits`` (the overlay is not carried on those paths
yet, so the user's work would be dropped), before any message, job or credit; the base stays
current and nothing is stored. An unedited Spotify draft is revised and refitted as before, and
so is an edited one once the edits are undone. Fakes only; no network."""

from typing import Any
from uuid import UUID

import pytest

from revisesongtools import change, current, picked
from revisetools import ReviseEnv
from songtools import SongEnv
from test_refit_draft import ask as ask_refit
from test_refit_draft import correct_all
from test_spotify_draft_edit import count, edit

pytestmark = pytest.mark.usefixtures("jenv")

LOCK: dict[str, Any] = {"op": "lock", "block": 0, "scope": "all"}


def jobs(renv: ReviseEnv) -> int:
    return renv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=renv.owner).scalar_one()


def messages(renv: ReviseEnv, class_id: UUID) -> int:
    return renv.sql(
        "SELECT count(*) FROM class_messages WHERE class_id = :c", c=class_id
    ).scalar_one()


def refused(response: Any) -> None:
    assert (response.status_code, response.json()["detail"]["reason"]) == (409, "has_user_edits")


async def edited(renv: ReviseEnv) -> tuple[UUID, UUID, UUID]:
    class_id, base = await renv.design()
    made = await edit(renv, class_id, base, LOCK)
    assert made.status_code == 200, made.text
    return class_id, base, UUID(made.json()["revision_id"])


async def test_a_revise_of_an_edited_draft_is_refused_and_stores_nothing(renv: ReviseEnv) -> None:
    class_id, _base, new = await edited(renv)
    stored, queued, said = count(renv, class_id), jobs(renv), messages(renv, class_id)
    refused(await renv.ask(class_id, new))
    assert (count(renv, class_id), jobs(renv), messages(renv, class_id)) == (stored, queued, said)
    assert renv.current(class_id) == new


async def test_a_refit_of_an_edited_draft_is_refused_and_stores_nothing(renv: ReviseEnv) -> None:
    class_id, _base, new = await edited(renv)
    correct_all(renv, class_id)
    stored, queued = count(renv, class_id), jobs(renv)
    refused(await ask_refit(renv, class_id, new))
    assert (count(renv, class_id), jobs(renv)) == (stored, queued)
    assert renv.current(class_id) == new


async def test_an_unedited_draft_is_still_revised(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    job_id, _ = await renv.revise(class_id, base, "revise_good")
    assert renv.denv.ops.row(job_id).state == "completed"


async def test_after_undo_the_draft_is_revised_again(renv: ReviseEnv) -> None:
    class_id, base, new = await edited(renv)
    view = (await renv.browser.get(f"/api/classes/{class_id}/overlay")).json()
    back = await renv.browser.post(
        f"/api/classes/{class_id}/current",
        {
            "revision_id": str(base),
            "content_hash": view["undo"]["content_hash"],
            "expected_current_id": str(new),
        },
    )
    assert back.status_code == 200, back.text
    job_id, _ = await renv.revise(class_id, base, "revise_good")
    assert renv.denv.ops.row(job_id).state == "completed"


async def test_a_song_revise_of_an_edited_draft_is_refused(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    class_id, base, _list = await picked(songenv, rider)
    made = await rider.browser.post(
        f"/api/classes/{class_id}/edits", {"base_revision_id": str(base), "edit": LOCK}
    )
    assert made.status_code == 200, made.text
    new = UUID(made.json()["revision_id"])
    before = songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=rider.user_id)
    queued = before.scalar_one()
    refused(await change(rider, class_id, new))
    after = songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=rider.user_id)
    assert after.scalar_one() == queued
    assert current(songenv, class_id) == new
