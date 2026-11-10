"""A chat revision of a legacy Ghost Target base (ADR 0029 section 3, MA1a-1; issue #877).

Before the model sees the base, each retired game becomes a regular interval of the same length
and zone (``classbeat.design.revise_base``); the revision's change summary records the
``game_retired`` Not as asked item. The model is never asked to pick another game. Through the
jobs API and the worker side with the fake MOWL and ``FakeProvider``; never the network.
"""

import json
from typing import Any

import pytest

from classbeat.agent.schemas import RunningDraft, WorkoutDraft
from classbeat.agent.stored_draft import stored_draft
from classbeat.design import music_kind, revise_base
from legacytools import has_ghost, legacy_child, with_ghost_target
from modeltools import clear_gate
from revisetools import ReviseEnv
from savetools import DRAFT, RUNNING_DRAFT
from test_soundless_jobs import soundless_class

pytestmark = pytest.mark.usefixtures("jenv")


def payload(message: str) -> dict[str, Any]:
    return json.loads(message[message.index('{"brief"') :])


def test_a_retired_game_becomes_a_regular_interval_and_nothing_else_changes() -> None:
    legacy, (segment, interval) = with_ghost_target(DRAFT)
    draft, retired = revise_base.without_retired_games(stored_draft(legacy, None))
    assert retired is True
    assert type(draft) is WorkoutDraft  # the strict schema: what a model may see
    got = draft.model_dump(mode="json", exclude_none=True)
    expected = json.loads(json.dumps(legacy))
    del expected["segments"][segment]["intervals"][interval]["game"]
    assert got == expected  # the same sec, zone, cadence, position and cue
    assert not has_ghost(got)
    kept, none = revise_base.without_retired_games(stored_draft(DRAFT, None))
    assert none is False
    assert kept.model_dump(mode="json", exclude_none=True) == DRAFT  # Racetrack stays
    running = stored_draft(RUNNING_DRAFT, "running")
    assert revise_base.without_retired_games(running) == (running, False)
    assert isinstance(running, RunningDraft)


def test_the_note_is_added_only_for_a_retired_game() -> None:
    change = {"intervals": {"changed": 1}}
    assert revise_base.noted(change, True) == {**change, "not_as_asked": ["game_retired"]}
    assert revise_base.noted(change, False) == change
    assert len(json.dumps(revise_base.noted(change, True))) < 1024


async def test_a_legacy_base_is_revised_without_ghost_target_and_the_note_is_recorded(
    renv: ReviseEnv,
) -> None:
    class_id, first = await renv.design()
    base, content, (segment, interval) = legacy_child(renv.sql, class_id, first)
    job_id, _ = await renv.revise(class_id, base, "revise_good")
    row = renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    sent = renv.providers[-1].sent[0].spec
    assert not has_ghost(sent.first_message)
    assert "ghost" not in sent.first_message.lower()
    shown = payload(sent.first_message)["base_draft"]["segments"][segment]["intervals"][interval]
    was = content["segments"][segment]["intervals"][interval]
    assert "game" not in shown
    assert (shown["sec"], shown["zone"]) == (was["sec"], was["zone"])
    *_, newest = renv.revisions(class_id)
    assert newest.parent_id == base
    assert not has_ghost(newest.content)
    assert renv.current(class_id) == newest.id
    note = renv.messages(class_id)[-1]
    assert note.role == "assistant"
    assert note.change["not_as_asked"] == ["game_retired"]


async def test_a_modern_base_records_no_not_as_asked_item(renv: ReviseEnv) -> None:
    class_id, base = await renv.design()
    job_id, _ = await renv.revise(class_id, base, "revise_good")
    assert renv.denv.ops.row(job_id).state == "completed"
    assert "not_as_asked" not in renv.messages(class_id)[-1].change


async def test_a_soundless_legacy_base_is_rewritten_the_same_way(renv: ReviseEnv) -> None:
    # The soundless gate on, as ``test_soundless_jobs.soundless_on`` sets it for its module.
    music_kind.configure(music_kind.SoundlessGate(enabled=True, fake_covers=True))
    try:
        class_id, design_job = await soundless_class(renv)
        [first] = renv.denv.revisions(design_job)
        base, _, _ = legacy_child(renv.sql, class_id, first.id)
        job_id, _ = await renv.revise(class_id, base, "soundless_revise_good", "Make it easier.")
        assert renv.denv.ops.row(job_id).state == "completed"
        assert not has_ghost(renv.providers[-1].sent[0].spec.first_message)
        assert renv.messages(class_id)[-1].change["not_as_asked"] == ["game_retired"]
    finally:
        music_kind.configure(None)
        clear_gate(renv.denv.ops.admin)
