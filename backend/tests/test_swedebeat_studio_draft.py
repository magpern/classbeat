"""SR C5b: the backend's own check of the stage's plan by reference (``StudioMusic.spec_of``) and
the named compose failure (``studio_mix.compose``). Pure: no database, no model."""

from typing import Any

import pytest
from mowl import compose as mix

from classbeat.agent import studio_mix
from classbeat.agent.stage import ToolError
from classbeat.agent.studio_schemas import MixInput
from classbeat.jobs import JobFailure
from classbeat.studio.mix_spec_model import MixSpec
from classbeat.swedebeat_studio.draft import StudioMusic
from studiomaps import tracks, whole_mix

REFS = tuple(f"ref-{n:016x}" for n in range(8))
MUSIC = StudioMusic(REFS, tracks())


def test_a_snapped_plan_maps_to_references_and_back() -> None:
    plan = studio_mix.compose(MUSIC.tracks, MixInput.model_validate(whole_mix())).plan
    spec = MUSIC.spec_of(plan)
    assert [t["reference"] for t in spec["tracks"]] == list(REFS)
    assert MUSIC.plan_of(MixSpec.model_validate(spec)) == plan


@pytest.mark.parametrize(
    "change",
    [
        {"crossfades": [8] * 6 + [31]},  # a rule of the policy
        {"tracks": [{"track": 0, "start_sec": 10.3, "end_sec": 300.0}]},  # not the stage's snap
        {"tracks": [{"track": 8, "start_sec": 0, "end_sec": 300.0}]},  # an index off the list
        {"tracks": [{"track": -8, "start_sec": 0, "end_sec": 300.0}]},  # never from the end
    ],
)
def test_a_plan_the_backend_would_not_make_is_invalid_output(change: dict[str, Any]) -> None:
    plan = studio_mix.compose(MUSIC.tracks, MixInput.model_validate(whole_mix())).plan
    bad = {**plan, **({"crossfades": change["crossfades"]} if "crossfades" in change else {})}
    if "tracks" in change:
        bad = {**plan, "tracks": change["tracks"] + plan["tracks"][1:]}
    with pytest.raises(JobFailure) as caught:
        MUSIC.spec_of(bad)
    assert caught.value.category == "invalid_output"


def test_a_stored_reference_off_the_list_is_internal() -> None:
    plan = studio_mix.compose(MUSIC.tracks, MixInput.model_validate(whole_mix())).plan
    spec = MUSIC.spec_of(plan)
    spec["tracks"][0]["reference"] = "ref-not-on-the-list"
    with pytest.raises(JobFailure) as caught:
        MUSIC.plan_of(MixSpec.model_validate(spec))
    assert caught.value.category == "internal"


def test_a_compose_failure_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("a rule of the library")

    monkeypatch.setattr(mix, "compose_map", refuse)
    with pytest.raises(ToolError) as caught:
        studio_mix.compose(MUSIC.tracks, MixInput.model_validate(whole_mix()))
    assert caught.value.code == "invalid_mix"
    assert caught.value.messages[0].startswith("compose_failed: ")
