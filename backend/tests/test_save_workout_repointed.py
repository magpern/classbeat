"""Save after a re-point of the link (#1535, #1546; ADR 0006 amendment of 2026-11-26, item 2):
an attach of a newer write points the user's link at a new MOWL wrapper as a **new** link row (a
new id, ``_POINT_LINK``), so a revision that named the older link no longer finds it and Save is
refused ``music_unlinked`` instead of saving the class onto the other wrapper's tracks, both when
the job is created and when the worker runs it. The same wrapper keeps its row.

Fake MOWL only (``savetools``), never the network.
"""

from typing import Any
from uuid import UUID

from optools import TOKEN_A
from savetools import LINKED_PLAYLIST, FakeMowlPrograms, SaveEnv
from writtentools import Written


async def repointed(senv: SaveEnv, owner: UUID, connection: UUID, link_id: UUID) -> UUID:
    """Two writes attached: the first gets the planted link's wrapper back (the row stays), the
    second a new wrapper (a new row). The new link's id."""
    written = Written(senv.ops)
    for key in ("write-1", "write-2"):
        assert (await written.run(written.job(owner, connection, key))).state == "completed"
        if key == "write-1":
            assert written.link_ids(owner) == [link_id]  # the same wrapper keeps its row
    assert written.playlists == [LINKED_PLAYLIST, LINKED_PLAYLIST + 1]
    [new_link] = written.link_ids(owner)  # a new id: the assertions below show it
    return new_link


async def test_a_revision_naming_a_repointed_link_is_refused_music_unlinked_at_creation(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    new_link = await repointed(senv, saved.owner, saved.connection, saved.link_id)
    browser = await save_browser(saved)
    answer = await browser.save()
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"]["reason"] == "music_unlinked"
    # The same content on a revision naming the current link is saved.
    current = senv.plant_revision(saved.owner, saved.class_id, new_link)
    assert (await browser.save(revision_id=str(current))).status_code == 201
    assert fake_programs.writes == []


async def test_a_repoint_after_creation_is_music_unlinked_and_sends_nothing(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any
) -> None:
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    answer = await browser.save()
    assert answer.status_code == 201, answer.text
    await repointed(senv, saved.owner, saved.connection, saved.link_id)
    await senv.run_next()
    row = senv.row(UUID(answer.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "music_unlinked")
    assert fake_programs.writes == []
    assert fake_programs.owned(TOKEN_A) == []
