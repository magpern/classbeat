"""Save and Update of a ``refit_draft`` revision (ADR 0007 amendment "beat-one corrections",
item 10; moved from #541 to #544): a refit revision is an ordinary ``draft`` with provenance
``origin: beat_one`` and ``music.beat_one``/``songs``, so Save and Update write its content with
the corrected timing, the saved state says "out of date" once a refit changes the content, and
"up to date" again after the Update. The in-process fake MOWL (``savetools``); never the network.
"""

import copy
import json
from typing import Any
from uuid import UUID

from classbeat import mowl_state
from optools import TOKEN_A
from savetools import DRAFT, LINKED_PLAYLIST, FakeMowlPrograms, spec_of
from updatetools import Live, UpdateEnv, draft_hash


def refitted(content: dict[str, Any]) -> dict[str, Any]:
    """The content as a refit leaves it: one cut moved a second later, the total kept."""
    out = copy.deepcopy(content)
    intervals = [i for s in out["segments"] for i in s["intervals"]]
    intervals[0]["sec"] += 1
    intervals[1]["sec"] -= 1
    return out


def plant_refit(uenv: UpdateEnv, live: Live, base: UUID, content: dict[str, Any]) -> UUID:
    music = {
        "source": "mowl_spotify_playlist",
        "link_id": str(live.saved.link_id),
        "fingerprint": "a" * 64,
        "unknown_bpm": [],
        "beat_one": [{"song": 1, "beats": 1}],
        "songs": [{"song": 1, "start": 0.0, "end": 240.0, "bpm": 124.0, "estimate": False,
                   "title": "Song"}],
    }  # fmt: skip
    revision = uenv.s.plant_revision(
        live.saved.owner, live.saved.class_id, live.saved.link_id, content, music=music
    )
    uenv.sql(
        "UPDATE class_revisions SET parent_id = :p, provenance = CAST(:prov AS jsonb) "
        "WHERE id = :r",
        p=base, r=revision,
        prov=json.dumps({"origin": "beat_one", "base_revision_id": str(base), "map_version": 1,
                         "crossfade_sec": 9.0, "classbeat_version": "t", "mowl_version": "t"}),
    )  # fmt: skip
    uenv.sql(
        "UPDATE classes SET current_revision_id = :r WHERE id = :c",
        r=revision, c=live.saved.class_id,
    )  # fmt: skip
    return revision


async def saved_state(update_browser: Any, live: Live) -> dict[str, Any]:
    browser = (await update_browser(live)).browser
    answer = await browser.get(f"/api/classes/{live.saved.class_id}")
    assert answer.status_code == 200, answer.text
    return answer.json()["saved"]


async def test_a_refit_revision_is_saved_and_updated_with_its_corrected_timing(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()  # the class's first revision (DRAFT), saved, state verified
    base = uenv.sql(
        "SELECT revision_id FROM saved_workouts WHERE id = :s", s=live.saved_workout_id
    ).scalar_one()
    uenv.sql(
        "UPDATE classes SET current_revision_id = :r WHERE id = :c", r=base, c=live.saved.class_id
    )
    assert (await saved_state(update_browser, live))["latest_up_to_date"] is True

    content = refitted(DRAFT)
    revision = plant_refit(uenv, live, base, content)
    # The refit changed the content, so the saved workout is out of date.
    assert (await saved_state(update_browser, live))["latest_up_to_date"] is False

    browser = await update_browser(live)
    answer = await browser.update(revision, content)
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    await uenv.run_next()
    assert (uenv.row(job_id).state, uenv.row(job_id).error_category) == ("completed", None)

    [program] = fake_programs.owned(TOKEN_A)
    assert program.id == live.program_id
    read = fake_programs.read_program(program)
    assert mowl_state.matches(spec_of(content), read, playlist_id=LINKED_PLAYLIST)
    saved = uenv.saved_row(live.saved_workout_id)
    assert (saved.revision_id, bytes(saved.content_hash)) == (revision, draft_hash(content))
    assert bytes(saved.state_hash) == mowl_state.state_hash(mowl_state.normalized_program(read))
    assert (await saved_state(update_browser, live))["latest_up_to_date"] is True


async def test_a_refit_revision_is_saved_as_a_new_workout(
    uenv: UpdateEnv, fake_programs: FakeMowlPrograms, update_browser: Any
) -> None:
    live = await uenv.live()
    base = uenv.sql(
        "SELECT revision_id FROM saved_workouts WHERE id = :s", s=live.saved_workout_id
    ).scalar_one()
    content = refitted(DRAFT)
    revision = plant_refit(uenv, live, base, content)
    browser = await update_browser(live)
    answer = await browser.save(revision_id=str(revision), content_hash=draft_hash(content).hex())
    assert answer.status_code == 201, answer.text
    await uenv.run_next()
    assert uenv.row(UUID(answer.json()["id"])).state == "completed"
    programs = fake_programs.owned(TOKEN_A)
    assert len(programs) == 2
    newest = max(programs, key=lambda p: p.id)
    read = fake_programs.read_program(newest)
    assert mowl_state.matches(spec_of(content), read, playlist_id=LINKED_PLAYLIST)
