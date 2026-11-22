"""#1391 (ADR 0042 section 3): ``design.edit_overlay_policy``, the overlay re-applied to a workout
automation made. Reconciliation at the 50 % boundary; the fixed order after a regeneration (an
override survives with no lock, its ``ai_value_at_set`` the model's new value; a lock breach is
``overlay_violated``) and after a B4a remap (a boundary override follows the remap; a moved
timing lock is ``locked_block_conflict``); unapplied and orphaned records have no effect. Pure."""

import copy
from typing import Any

import pytest

from agentfake import good_draft
from classbeat.design.edit_overlay_model import Anchor, Event, Lock, Override, Target
from classbeat.design.edit_overlay_policy import (
    LOCKED_BLOCK_CONFLICT,
    OVERLAY_VIOLATED,
    OverlayViolated,
    match,
    reapplied,
)
from classbeat.design.workout_edit_policy import blocks

BASE = good_draft()  # warmup 180 s + 120 s, main 240 s ...
FOUND = blocks(BASE)


def target(kind: str, start_s: int, end_s: int) -> Target:
    return Target(segment_kind=kind, span=[start_s * 1000, end_s * 1000])  # pyright: ignore[reportArgumentType]


def none(_: Event) -> int | None:
    return None


def override(at: int, field: str, value: Any, ai: Any) -> Override:
    b = FOUND[at]
    return Override(
        type="override",
        target=Target(segment_kind=b.kind, span=[b.start_ms, b.end_ms]),  # pyright: ignore[reportArgumentType]
        field=field,  # pyright: ignore[reportArgumentType]
        value=value,
        ai_value_at_set=ai,
    )


def lock(at: int, scope: str) -> Lock:
    b = FOUND[at]
    return Lock(
        type="lock",
        target=Target(segment_kind=b.kind, span=[b.start_ms, b.end_ms]),  # pyright: ignore[reportArgumentType]
        scope=scope,  # pyright: ignore[reportArgumentType]
    )


# --- reconciliation at the 50 % boundary ---------------------------------------------------------


def test_match_takes_an_overlap_of_exactly_half() -> None:
    # Block 0 is warmup [0, 180 s): a 360 s span over [0, 360) overlaps it by 180 s, half the span,
    # and the second warmup block [180, 300) by only 120 s.
    assert match(target("warmup", 0, 360), FOUND) == 0


def test_match_below_half_is_unapplied() -> None:
    assert match(target("warmup", 0, 362), FOUND) is None  # 180 of 362 s: under half


def test_match_takes_the_greatest_overlap_of_the_same_kind_only() -> None:
    assert match(target("warmup", 100, 290), FOUND) == 1  # 80 s of block 0, 110 s of block 1
    assert match(target("cooldown", 0, 180), FOUND) is None  # no cooldown block there


# --- after a regeneration ------------------------------------------------------------------------


def test_an_override_survives_a_regeneration_with_no_lock() -> None:
    model = copy.deepcopy(BASE)
    model["segments"][0]["intervals"][1]["zone"] = 2  # the model's new value
    record = override(1, "zone", 4, BASE["segments"][0]["intervals"][1]["zone"])
    applied = reapplied(model, BASE, [record], event_ms=none)
    assert applied.content["segments"][0]["intervals"][1]["zone"] == 4
    ((kept, at),) = applied.placed
    assert at == 1
    assert isinstance(kept, Override)
    assert kept.ai_value_at_set == 2  # "Your edit kept (AI suggested ...)"


@pytest.mark.parametrize(("scope", "change"), [("attributes", "zone"), ("timing", "sec"),
                                               ("all", "zone"), ("all", "sec")])  # fmt: skip
def test_a_lock_breach_is_overlay_violated(scope: str, change: str) -> None:
    model = copy.deepcopy(BASE)
    interval = model["segments"][1]["intervals"][0]  # block 2
    if change == "zone":
        interval["zone"] = 6
    else:
        interval["sec"] += 10
        model["segments"][1]["intervals"][1]["sec"] -= 10
    with pytest.raises(OverlayViolated) as caught:
        reapplied(model, BASE, [lock(2, scope)], event_ms=none)
    assert caught.value.reason == OVERLAY_VIOLATED


def test_a_lock_outside_its_scope_is_kept() -> None:
    model = copy.deepcopy(BASE)
    model["segments"][1]["intervals"][0]["zone"] = 6
    applied = reapplied(model, BASE, [lock(2, "timing")], event_ms=none)
    assert [at for _, at in applied.placed] == [2]


# --- after a B4a remap ---------------------------------------------------------------------------


def stretched(content: dict[str, Any], by: int) -> dict[str, Any]:
    """Every interval ``by`` seconds longer: the remap of a longer mix."""
    out = copy.deepcopy(content)
    for segment in out["segments"]:
        for interval in segment["intervals"]:
            interval["sec"] += by
    return out


def test_after_a_remap_a_boundary_override_follows_the_remap() -> None:
    end = FOUND[0].end_ms
    record = override(0, "boundary", end, end)
    applied = reapplied(stretched(BASE, 2), BASE, [record], event_ms=none, remap=True)
    assert blocks(applied.content)[0].end_ms == end + 2000  # moved with the music, not set back
    ((kept, _),) = applied.placed
    assert kept == record


def test_after_a_remap_a_moved_timing_lock_is_locked_block_conflict() -> None:
    with pytest.raises(OverlayViolated) as caught:
        reapplied(stretched(BASE, 2), BASE, [lock(3, "timing")], event_ms=none, remap=True)
    assert caught.value.reason == LOCKED_BLOCK_CONFLICT


def test_after_a_remap_an_attribute_lock_is_kept() -> None:
    applied = reapplied(stretched(BASE, 2), BASE, [lock(3, "attributes")], event_ms=none,
                        remap=True)  # fmt: skip
    assert [at for _, at in applied.placed] == [3]


# --- unapplied and orphaned records have no effect -----------------------------------------------


def test_unapplied_and_orphaned_records_have_no_effect_and_are_listed() -> None:
    gone = Override(
        type="override",
        target=target("cooldown", 0, 100),
        field="zone",
        value=6,
        ai_value_at_set=1,
    )
    event = Event(
        reference="r0",
        occurrence=0,
        sha256="a" * 64,
        source_ms=1000,
        kind="drop",
        map_version=1,
    )
    b = FOUND[1]
    anchor = Anchor(
        type="anchor",
        target=Target(segment_kind=b.kind, span=[b.start_ms, b.end_ms]),  # pyright: ignore[reportArgumentType]
        edge="end",
        event=event,
    )
    model = copy.deepcopy(BASE)
    applied = reapplied(model, BASE, [gone, anchor], event_ms=none)
    assert applied.content == BASE
    assert applied.unapplied == (gone,)
    assert applied.orphaned == (anchor,)
    assert applied.placed == []
