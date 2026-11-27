"""The music answer reads the class row under its lock (``music_answer.answer``, #1495; #1510).

The song fill's create locks the same row (``song_fill._Hold``), so an answer either lands before
the fill reads it or is refused. Without ``lock=True`` on the answer's read, the two would race:
this holds the row's lock in a second transaction and shows the answer waits for it, then lands
once it is released. The answer repeats the stored one, so it writes nothing: only the locked read
can make it wait (a write would block on the held row by itself). Against Postgres; never the
network."""

import asyncio
from typing import Any

import pytest

from classbeat.classes import music_answer
from classbeat.db import user_session
from classbeat.design import interpretation_row
from classbeat.design.music_decision import MusicAnswer
from composertools import ComposerEnv
from ctx import make_context

pytestmark = pytest.mark.usefixtures("jenv")

PICK: dict[str, Any] = {"state": "decided", "choice": "pick"}


async def test_the_answer_waits_for_the_class_row_lock_and_lands_after_it(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    # A pick needs a hint of what to play (#1538): a song the instructor named is one.
    class_id, _ = await cenv.sketched(rider, pinned=[{"title": "Africa"}], decision=PICK)
    ctx = make_context(rider.user_id)
    factory = cenv.songenv.factory
    given = MusicAnswer(choice="pick")

    async with user_session(factory, ctx) as holder:
        held = await interpretation_row.read(holder, rider.user_id, class_id, lock=True)
        assert held is not None
        answering = asyncio.create_task(music_answer.answer(factory, ctx, class_id, given))
        done, _ = await asyncio.wait({answering}, timeout=1.0)
        assert not done, "the answer did not wait for the class row lock"
    result = await asyncio.wait_for(answering, timeout=10)
    assert result == music_answer.MusicDecisionOut(state="decided", choice="pick", playlists=0)
    stored = cenv.sql(
        "SELECT interpretation -> 'music_decision' FROM classes WHERE id = :c", c=class_id
    ).scalar()
    assert (stored["state"], stored["choice"]) == ("decided", "pick")
