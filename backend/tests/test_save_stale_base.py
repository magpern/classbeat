"""Save and Update refuse a stale base (ADR 0042 section 4, #1391): with ``base_revision_id``, a
class whose current revision is another (another tab moved it) is refused ``stale_revision`` at
creation and nothing is created; the current base is admitted. The in-process fake MOWL
(``savetools``); never the network.
"""

from typing import Any
from uuid import UUID

from savetools import FakeMowlPrograms, SaveEnv
from updatetools import DRAFT_2, UpdateEnv

JOBS = "SELECT count(*) FROM jobs WHERE owner_id = :o"


def make_current(senv: SaveEnv, class_id: UUID, revision_id: UUID) -> None:
    senv.sql("UPDATE classes SET current_revision_id = :r WHERE id = :c", r=revision_id, c=class_id)


def jobs(senv: SaveEnv, owner: UUID) -> int:
    return senv.sql(JOBS, o=owner).scalar_one()


async def test_a_save_on_a_stale_base_is_refused_and_creates_nothing(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    newer = senv.plant_revision(saved.owner, saved.class_id, saved.link_id)
    make_current(senv, saved.class_id, newer)  # another tab moved the class on
    browser = await save_browser(saved)
    before = jobs(senv, saved.owner)

    answer = await browser.save(base_revision_id=str(saved.revision_id))
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "stale_revision"
    assert jobs(senv, saved.owner) == before
    assert fake_programs.writes == []

    answer = await browser.save(base_revision_id=str(newer))  # the page's base is current
    assert answer.status_code == 201, answer.text
    assert jobs(senv, saved.owner) == before + 1


async def test_a_save_without_a_current_revision_is_stale_and_without_a_base_is_not(
    senv: SaveEnv, save_browser: Any
) -> None:
    saved = await senv.saved_class()  # the class names no current revision
    browser = await save_browser(saved)
    answer = await browser.save(base_revision_id=str(saved.revision_id))
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "stale_revision"
    answer = await browser.save()  # no base: the guard does not apply (an older client)
    assert answer.status_code == 201, answer.text


async def test_an_update_on_a_stale_base_is_refused_and_creates_nothing(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    revision = uenv.revision(live)
    newer = uenv.revision(live)
    make_current(uenv.s, live.saved.class_id, newer)
    browser = await update_browser(live)
    writes, before = len(fake_programs.writes), jobs(uenv.s, live.saved.owner)

    answer = await browser.update(revision, DRAFT_2, base_revision_id=str(revision))
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "stale_revision"
    assert jobs(uenv.s, live.saved.owner) == before
    assert len(fake_programs.writes) == writes

    answer = await browser.update(revision, DRAFT_2, base_revision_id=str(newer))
    assert answer.status_code == 201, answer.text
    assert jobs(uenv.s, live.saved.owner) == before + 1
