"""#1423 (ADR 0042 section 5, "Pins and regeneration"): a studio revise of a draft with cut pins
re-applies them to the model's new spec. Each pin lands by its rule (a confirmed one exactly where
it was confirmed, a ``bar`` one snapped), the stored revise draft passes B9 (the outputs admit it),
a model spec that cannot hold a confirmed pin is a correction round and, when no correction holds
it, the run ends ``invalid_output`` with nothing stored; the overlay view undoes a revise in one
step. On the fakes (the SwedeBeat gateway, spotdown's ``exact`` maps, ``FakeProvider``) and
Postgres as ``classbeat_app``; never the network."""

import copy
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from classbeat.design.revision_hash import revision_hash
from classbeat.design.workout_edit_policy import blocks
from studiooutputs import (
    choice,
    outputs_on,  # noqa: F401 - fixture
)
from studiotools import StudioEnv
from test_studio_mix_not_applied import post
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    LENGTH,
    MIX_8,
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    design_script,
    designed,
    revise,
    run_kind,
    spotdown,  # noqa: F401 - the fake spotdown the stand-in answers from
)
from test_swedebeat_studio_songs import _sent  # pyright: ignore[reportPrivateUsage]
from test_user_edit_routes import current_id, edit, overlay_of, revision_count

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on")

MIX = ("swedebeat_playlist", "swedebeat_mix")
PINNED = 225.0  # piece 0's end (list track 1), pinned exact and confirmed
CUT = copy.deepcopy(MIX_8)
CUT["tracks"][-1]["end_sec"] = 230.0  # the model's change: its piece 0 plays whole again
# A model spec that cannot hold the pin: list track 1 starts at 170 s, so its pinned end at 225 s
# leaves 55 s (``window_too_short``); list track 3 plays again at the end to keep the length.
BAD = copy.deepcopy(MIX_8)
BAD["tracks"][0]["start_sec"] = 170.0
BAD["tracks"].append({"track": 3, "start_sec": 0, "end_sec": LENGTH})
BAD["crossfades"].append(8)


def spec_of(studio: StudioEnv, revision_id: UUID) -> Any:
    return studio.songs.revision(revision_id).music["mix_spec"]


async def pinned(studio: StudioEnv) -> tuple[Any, UUID, UUID]:
    """A designed class with piece 0's end pinned and confirmed, and piece 2's end pinned to a
    bar; the class and its current draft (whose workout the pin edits fitted to the pinned
    mix)."""
    rider, class_id, _list, proposal = await designed(studio)
    base: object = proposal.current_revision_id
    for op in (
        {"op": "pin_set", "piece": 0, "edge": "end", "requested_sec": PINNED, "mode": "exact"},
        {"op": "pin_confirm", "piece": 0, "edge": "end"},
        {"op": "pin_set", "piece": 2, "edge": "end", "requested_sec": 200.7, "mode": "bar"},
    ):
        answer = await edit(rider, class_id, base, op)
        assert answer.status_code == 200, answer.text
        base = answer.json()["revision_id"]
    return rider, class_id, UUID(str(base))


def boundaries(content: dict[str, Any]) -> list[tuple[int, int]]:
    return [(b.start_ms, b.end_ms) for b in blocks(content)]


def stored_design(studio: StudioEnv, class_id: UUID) -> UUID:
    return studio.songs.sql(
        "SELECT id FROM class_revisions WHERE class_id = :c "
        "AND provenance ->> 'origin' = 'design_draft'",
        c=class_id,
    ).scalar_one()


def with_bad_submits(count: int) -> dict[str, Any]:
    script = design_script(CUT, note="Cut the last song.")
    submit = script["turns"][-1]["calls"][0]
    bad = {**submit, "input": {**submit["input"], "mix": BAD}}
    bad["expect"] = {"ok": False, "error": "invalid_mix"}
    for _ in range(count):
        script["turns"].insert(-1, {"calls": [copy.deepcopy(bad)]})
    return script


async def revised(studio: StudioEnv, rider: Any, class_id: UUID, base: UUID) -> Any:
    response = await revise(rider, class_id, base, "Cut the last song shorter")
    assert response.status_code == 201, response.text
    return await run_kind(studio, UUID(response.json()["id"]))


async def test_a_revise_keeps_each_pin_by_its_rule_and_passes_b9(studio: StudioEnv) -> None:
    rider, class_id, base = await pinned(studio)
    before = spec_of(studio, base)
    studio.songs.script = design_script(CUT, note="Cut the last song.")
    row = await revised(studio, rider, class_id, base)
    assert (row.state, row.error_category) == ("completed", None)
    new = current_id(studio, class_id)
    stored = studio.songs.revision(new)
    assert (stored.parent_id, stored.provenance["origin"]) == (base, "revise_draft")
    spec = stored.music["mix_spec"]
    first, third = spec["tracks"][0], spec["tracks"][2]
    assert first["end_sec"] == PINNED  # confirmed: never moved, the model's 240 s does not stand
    assert first["end_pin"]["confirmed_sec"] == PINNED
    assert third["end_pin"] == {"requested_sec": 200.7, "mode": "bar", "confirmed": False}
    assert third["end_sec"] == before["tracks"][2]["end_sec"] != 200.7  # snapped as before
    assert spec["tracks"][-1]["end_sec"] != LENGTH  # the model's own change kept
    overlay = overlay_of(studio, new)
    assert [r["type"] for r in overlay["records"]] == ["pin", "pin"]
    assert bytes(stored.content_hash) == revision_hash(
        stored.content, mix_spec=spec, overlay=overlay
    )
    assert "pinned by the instructor (confirmed after listening)" in _sent(studio)
    # The workout is the model's (the same draft as the design), moved onto the PINNED mix and
    # fitted on its map: exactly the base's, whose pin edits fitted it to that mix. The model's
    # own mix (its piece 0 whole, 15 s longer) would give the design's timing instead.
    assert stored.content == studio.songs.revision(base).content
    design = studio.songs.revision(stored_design(studio, class_id)).content
    assert boundaries(stored.content) != boundaries(design)
    body = choice(SimpleNamespace(current_revision_id=new), studio, *MIX)
    chosen = await post(rider, class_id, body)
    assert chosen.status_code == 201, chosen.text  # B9: applied_form, no pin to listen to again


async def test_a_spec_that_cannot_hold_a_pin_is_a_correction_round(studio: StudioEnv) -> None:
    rider, class_id, base = await pinned(studio)
    studio.songs.script = with_bad_submits(1)
    row = await revised(studio, rider, class_id, base)
    assert (row.state, row.error_category) == ("completed", None)  # the second submit kept it
    new = current_id(studio, class_id)
    assert spec_of(studio, new)["tracks"][0]["end_sec"] == PINNED
    sent = _sent(studio)
    assert "window_too_short" in sent
    assert "track 1's end cut at 225.00 s (confirmed)" in sent


async def test_a_revise_that_never_holds_the_pin_stores_nothing(studio: StudioEnv) -> None:
    rider, class_id, base = await pinned(studio)
    count = revision_count(studio, class_id)
    studio.songs.script = with_bad_submits(4)  # one more than the corrections allowed
    row = await revised(studio, rider, class_id, base)
    assert (row.state, row.error_category) == ("failed", "invalid_output")
    assert revision_count(studio, class_id) == count
    assert current_id(studio, class_id) == base


async def test_the_overlay_view_undoes_a_revise_in_one_step(studio: StudioEnv) -> None:
    rider, class_id, base = await pinned(studio)
    studio.songs.script = design_script(CUT, note="Cut the last song.")
    row = await revised(studio, rider, class_id, base)
    assert row.state == "completed"
    new = current_id(studio, class_id)
    path, pick = f"/api/classes/{class_id}/overlay", f"/api/classes/{class_id}/current"
    view = (await rider.browser.get(path)).json()
    assert view["revision_id"] == str(new)
    assert view["undo"]["revision_id"] == str(base)
    undone = await rider.browser.post(pick, {**view["undo"], "expected_current_id": str(new)})
    assert undone.status_code == 200, undone.text
    assert current_id(studio, class_id) == base
    view = (await rider.browser.get(path)).json()
    assert view["redo"]["revision_id"] == str(new)  # and back again in one step
    redone = await rider.browser.post(pick, {**view["redo"], "expected_current_id": str(base)})
    assert redone.status_code == 200, redone.text
    assert current_id(studio, class_id) == new
