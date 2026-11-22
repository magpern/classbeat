"""#1423: the backend's own check of a studio revise's pinned spec before it is stored
(``StudioMusic.pinned_of``): the spec must be a fixed point of ``mix_plan_policy.checked`` against
the stored analyses, and exactly the mix of the plan the workout was fitted on; anything else is
``invalid_output``. Pure: no database, no model."""

import copy
from typing import Any

import pytest

from classbeat.agent import studio_mix
from classbeat.agent.studio_schemas import MixInput
from classbeat.jobs import JobFailure
from classbeat.studio.mix_plan_policy import checked
from classbeat.swedebeat_studio.draft import StudioMusic
from classbeat.swedebeat_studio.mix_apply_calc import keyed, plan_of
from studiomaps import tracks, whole_mix

REFS = tuple(f"ref-{n:016x}" for n in range(8))
MUSIC = StudioMusic(REFS, tracks())
PIN = {"requested_sec": 250.37, "mode": "exact", "confirmed": False}


def pinned_spec() -> dict[str, Any]:
    """The whole mix with track 0's end pinned exact at 250.37 s, as ``revise_pins`` makes it."""
    plan = studio_mix.compose(MUSIC.tracks, MixInput.model_validate(whole_mix())).plan
    spec = MUSIC.spec_of(plan)
    spec["tracks"][0] |= {"end_sec": PIN["requested_sec"], "end_pin": dict(PIN)}
    return checked(spec, REFS, MUSIC.facts())


def composed_plan(spec: dict[str, Any]) -> dict[str, Any]:
    return studio_mix.compose_spec(MUSIC.tracks, keyed(REFS, spec)).plan


def test_the_pinned_spec_of_its_own_plan_is_stored() -> None:
    spec = pinned_spec()
    stored = MUSIC.pinned_of(spec, composed_plan(spec))
    assert stored == spec
    assert stored["tracks"][0]["end_pin"] == PIN
    assert stored["tracks"][0]["end_sec"] == 250.37


def test_a_spec_checked_would_move_is_invalid_output() -> None:
    """An unpinned cut off the bar grid: ``checked`` snaps it, so the spec is not what is kept.
    The plan is the spec's own, so only the fixed-point check can refuse it."""
    spec = pinned_spec()
    spec["tracks"][1]["end_sec"] = 280.3
    assert checked(spec, REFS, MUSIC.facts()) != spec
    with pytest.raises(JobFailure) as caught:
        MUSIC.pinned_of(spec, plan_of(REFS, spec))
    assert caught.value.category == "invalid_output"


def test_a_spec_that_is_not_the_fitted_plans_mix_is_invalid_output() -> None:
    """A checked spec, but the workout was fitted on another mix (the model's unpinned one)."""
    spec = pinned_spec()
    unpinned = copy.deepcopy(spec)
    del unpinned["tracks"][0]["end_pin"]
    unpinned["tracks"][0]["end_sec"] = 300.0
    other = composed_plan(unpinned)
    assert other != composed_plan(spec)
    with pytest.raises(JobFailure) as caught:
        MUSIC.pinned_of(spec, other)
    assert caught.value.category == "invalid_output"
