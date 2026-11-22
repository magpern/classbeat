"""#1391 (ADR 0042 sections 3 to 5): a user edit of a studio draft computed without a database
(``swedebeat_studio.studio_edit_ops.edited``): the workout total under drag, move, split and merge,
locked blocks, Use AI value, the automatic overrides, the cut pins through amendment B's order and
their hints. Pure: no I/O; the composed map is the fake's (``studiomaps``)."""

import uuid
from typing import Any

import pytest

from classbeat.classes.user_edit_model import EditIn
from classbeat.design.edit_overlay_model import EMPTY, EditOverlay, Lock, Override
from classbeat.design.workout_edit_policy import EditRefused, blocks, total_sec
from classbeat.swedebeat_studio import mix_apply_calc, studio_edit_ops
from classbeat.swedebeat_studio import studio_edit_calc as calc
from classbeat.swedebeat_studio.studio_edit_calc import EditResult, EditState
from test_mix_apply_pins import DJ, REFS, case

SHA = {r: f"{n:x}" * 64 for n, r in enumerate(REFS, start=1)}


def base_state(overlay: EditOverlay = EMPTY) -> EditState:
    """The fake's draft fitted on eight whole 240 s exact tracks (``test_mix_apply_pins.case``)."""
    value, music, draft = case()
    spec = mix_apply_calc.derived(value, music, DJ, ())
    fitted = mix_apply_calc.fitted(draft, dict(value.spec), spec, music, None)
    content = calc._fitted(draft, fitted.result.spec)  # pyright: ignore[reportPrivateUsage]
    stored = mix_apply_calc.stored_spec(spec)
    return EditState(content, stored, overlay, music, SHA, value, DJ, 1)


def after(state: EditState, made: EditResult) -> EditState:
    return EditState(
        made.content,
        made.spec,
        made.overlay,
        state.music,
        state.analysed,
        state.proposal,
        state.choices,
        state.map_version,
    )


def op(raw: dict[str, Any]) -> Any:
    return EditIn.model_validate({"base_revision_id": str(uuid.uuid4()), "edit": raw}).edit


def run(state: EditState, raw: dict[str, Any]) -> EditResult:
    return studio_edit_ops.edited(state, op(raw), None)


def reason(state: EditState, raw: dict[str, Any]) -> str:
    with pytest.raises(EditRefused) as caught:
        run(state, raw)
    return caught.value.reason


STATE = base_state()
COUNT = len(blocks(STATE.content))


def spans(content: dict[str, Any]) -> list[tuple[int, int]]:
    return [(b.start_ms, b.end_ms) for b in blocks(content)]


# --- the total is invariant ---------------------------------------------------------------------


def outcome(state: EditState, raw: dict[str, Any]) -> EditResult | str:
    try:
        return run(state, raw)
    except EditRefused as refused:
        return refused.reason


# ``invalid_edit``: ``agent.design.check`` refuses the layout (a 300 s drag into the last block)
ALLOWED_REFUSALS = {
    "invalid_input",
    "invalid_edit",
    "split_out_of_range",
    "block_too_long",
    "nothing_changed",
}


def test_drag_keeps_the_total() -> None:
    kept = 0
    for at in range(COUNT - 1):
        for delta in (-90, -7, 3, 25, 400):
            made = outcome(STATE, {"op": "drag", "block": at, "delta_sec": delta})
            if isinstance(made, str):
                assert made in ALLOWED_REFUSALS, (at, delta, made)
                continue
            kept += 1
            assert total_sec(made.content) == total_sec(STATE.content)
            assert made.spec == STATE.spec  # a workout edit never changes the music
    assert kept > 3 * (COUNT - 1)  # nearly every drag goes through


@pytest.mark.parametrize("raw", [{"op": "move", "direction": 1}, {"op": "move", "direction": -1},
                                 {"op": "split", "offset_sec": 20}, {"op": "merge"}])  # fmt: skip
def test_move_split_and_merge_keep_the_total(raw: dict[str, Any]) -> None:
    kept = 0
    for at in range(COUNT):
        made = outcome(STATE, {**raw, "block": at})
        if isinstance(made, str):
            assert made in ALLOWED_REFUSALS, (at, made)
            continue
        kept += 1
        assert total_sec(made.content) == total_sec(STATE.content)
    assert kept > COUNT // 3


def test_set_changes_one_field_and_no_timing() -> None:
    zone = blocks(STATE.content)[1].interval["zone"]
    new = 2 if zone != 2 else 3
    made = run(STATE, {"op": "set", "block": 1, "field": "zone", "value": new})
    assert spans(made.content) == spans(STATE.content)
    assert blocks(made.content)[1].interval["zone"] == new
    (record,) = made.overlay.records
    assert isinstance(record, Override)
    assert (record.field, record.value, record.ai_value_at_set) == ("zone", new, zone)
    assert made.spec == STATE.spec


def test_setting_the_ai_value_again_drops_the_override() -> None:
    zone = blocks(STATE.content)[1].interval["zone"]
    first = run(STATE, {"op": "set", "block": 1, "field": "zone", "value": 2 if zone != 2 else 3})
    back = run(after(STATE, first), {"op": "set", "block": 1, "field": "zone", "value": zone})
    assert back.overlay.records == []


# --- locks --------------------------------------------------------------------------------------


def locked_at(at: int, scope: str = "all") -> EditState:
    return after(STATE, run(STATE, {"op": "lock", "block": at, "scope": scope}))


def test_a_timing_locked_block_is_not_dragged_split_merged_or_moved() -> None:
    state = locked_at(2)
    assert reason(state, {"op": "drag", "block": 2, "delta_sec": 5}) == "block_locked"
    assert reason(state, {"op": "drag", "block": 1, "delta_sec": 5}) == "block_locked"
    assert reason(state, {"op": "split", "block": 2, "offset_sec": 30}) == "block_locked"
    assert reason(state, {"op": "merge", "block": 2}) == "block_locked"
    assert reason(state, {"op": "move", "block": 3, "direction": -1}) == "block_locked"


def test_edits_elsewhere_leave_a_locked_block_unchanged() -> None:
    state = locked_at(2)
    locked = blocks(state.content)[2]
    for raw in ({"op": "drag", "block": 4, "delta_sec": 10},
                {"op": "set", "block": 5, "field": "zone", "value": 1},
                {"op": "split", "block": 6, "offset_sec": 20}):  # fmt: skip
        made = run(state, raw)
        now = blocks(made.content)[2]
        assert (now.start_ms, now.end_ms, now.interval) == (
            locked.start_ms,
            locked.end_ms,
            locked.interval,
        )
        assert any(isinstance(r, Lock) for r in made.overlay.records)


def test_an_attribute_locked_block_is_not_set() -> None:
    state = locked_at(1, "attributes")
    assert reason(state, {"op": "set", "block": 1, "field": "zone", "value": 1}) == "block_locked"
    run(state, {"op": "drag", "block": 1, "delta_sec": 10})  # its timing is free


def test_an_audio_edit_that_moves_a_timing_locked_block_is_a_conflict() -> None:
    late = len(blocks(STATE.content)) - 2  # the remap moves every block after a shortened piece
    state = locked_at(late, "timing")
    raw = {"op": "pin_set", "piece": 0, "edge": "end", "requested_sec": 200.0, "mode": "exact"}
    assert reason(state, raw) == "locked_block_conflict"


# --- Use AI value and the clears ---------------------------------------------------------------


def test_use_ai_value_restores_the_models_value() -> None:
    zone = blocks(STATE.content)[1].interval["zone"]
    edited = after(STATE, run(STATE, {"op": "set", "block": 1, "field": "zone", "value": 1}))
    made = run(edited, {"op": "use_ai_value", "block": 1, "field": "zone"})
    assert blocks(made.content)[1].interval["zone"] == zone
    assert made.overlay.records == []


def test_use_ai_value_restores_a_dragged_boundary() -> None:
    edited = after(STATE, run(STATE, {"op": "drag", "block": 0, "delta_sec": 30}))
    made = run(edited, {"op": "use_ai_value", "block": 0, "field": "boundary"})
    assert spans(made.content)[0] == spans(STATE.content)[0]
    assert made.overlay.records == []


def test_clear_all_restores_every_override_and_drops_every_record() -> None:
    one = after(STATE, run(STATE, {"op": "set", "block": 1, "field": "zone", "value": 1}))
    two = after(one, run(one, {"op": "lock", "block": 3, "scope": "all"}))
    made = run(two, {"op": "clear_all"})
    assert made.overlay.stored() is None
    assert blocks(made.content)[1].interval == blocks(STATE.content)[1].interval


@pytest.mark.parametrize(
    "raw",
    [
        {"op": "unlock", "block": 0},
        {"op": "remove_anchor", "block": 0, "edge": "end"},
        {"op": "use_ai_value", "block": 0, "field": "zone"},
        {"op": "clear_all"},
        {"op": "pin_clear", "piece": 0, "edge": "end"},
    ],
)
def test_a_clear_of_nothing_is_nothing_changed(raw: dict[str, Any]) -> None:
    assert reason(STATE, raw) == "nothing_changed"


# --- the cut pins: amendment B's one order ------------------------------------------------------


def pin(state: EditState, at: float, piece: int = 0) -> EditResult:
    return run(
        state,
        {"op": "pin_set", "piece": piece, "edge": "end", "requested_sec": at, "mode": "exact"},
    )


def test_a_pin_shortens_the_mix_and_the_workout_follows() -> None:
    made = pin(STATE, 200.0)
    assert made.spec["tracks"][0]["end_pin"]["requested_sec"] == 200.0
    assert total_sec(made.content) < total_sec(STATE.content)
    (record,) = made.overlay.pins()
    assert record.set_against == SHA["r0"]
    assert record.confirmed is False


def test_a_moved_pin_is_named_with_the_spec_before_the_edit() -> None:
    first = after(STATE, pin(STATE, 200.0))
    assert not any(h.startswith("pin_moved") for h in pin(STATE, 200.0).hints)
    moved = pin(first, 190.0)
    assert any(h.startswith("pin_moved") for h in moved.hints), moved.hints


def test_confirm_records_the_cut_and_the_file() -> None:
    first = after(STATE, pin(STATE, 200.0))
    made = run(first, {"op": "pin_confirm", "piece": 0, "edge": "end"})
    (record,) = made.overlay.pins()
    assert record.confirmed
    assert record.confirmed_sec == 200.0
    assert record.confirmed_against == SHA["r0"] == record.set_against


def test_a_pin_whose_file_is_unknown_is_music_changed() -> None:
    state = EditState(**{**STATE.__dict__, "analysed": {}})
    assert reason(state, {"op": "pin_set", "piece": 0, "edge": "end", "requested_sec": 200.0,
                          "mode": "exact"}) == "music_changed"  # fmt: skip


def test_without_choices_an_audio_edit_is_mix_not_applied() -> None:
    state = EditState(**{**STATE.__dict__, "choices": None})
    with pytest.raises(EditRefused) as caught:
        pin(state, 200.0)
    assert caught.value.reason == "mix_not_applied"
    run(state, {"op": "lock", "block": 0, "scope": "all"})  # a workout edit needs none


def test_pin_clear_and_clear_all_restore_the_spec() -> None:
    first = after(STATE, pin(STATE, 200.0))
    for raw in ({"op": "pin_clear", "piece": 0, "edge": "end"}, {"op": "clear_all"}):
        made = run(first, raw)
        assert made.spec == STATE.spec
        assert made.overlay.stored() is None
