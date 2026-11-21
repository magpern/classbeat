"""ADR 0036 amendment B4 (#1367): ``MixChoices``, its validation and its canonical JSON."""

from typing import Any

import pytest

from classbeat.studio.mix_choice_model import (
    MixChoiceRefused,
    MixChoices,
    canonical,
    parsed,
    positions_refusal,
)

DJ = {"dj_mix": True, "blend": True, "trim": True}


def refused(raw: object) -> str:
    with pytest.raises(MixChoiceRefused) as caught:
        parsed(raw)
    return caught.value.reason


@pytest.mark.parametrize(
    "raw",
    [
        {"dj_mix": False, "blend": True, "trim": False},  # whole songs: blend off
        {"dj_mix": False, "blend": False, "trim": True},  # whole songs: trim off
        {"dj_mix": False, "blend": False, "trim": False, "keep_whole": [0]},
        {"dj_mix": False, "blend": False, "trim": False, "hard_cut_in": [1]},
        {"dj_mix": False, "blend": False, "trim": False, "hard_cut_out": [2]},
        {**DJ, "keep_whole": [1, 1]},  # a position twice
        {**DJ, "keep_whole": [-1]},
        {**DJ, "keep_whole": [True]},
        {**DJ, "keep_whole": ["1"]},
        {**DJ, "keep_whole": 1},
        {**DJ, "loop": True},  # unknown field
        {"dj_mix": True, "blend": True},  # trim missing
        {"dj_mix": 1, "blend": True, "trim": True},  # not a bool
        [],
        None,
    ],
)
def test_invalid_choices_are_invalid_input(raw: Any) -> None:
    assert refused(raw) == "invalid_input"


def test_sets_are_sorted_and_canonical_json_is_one_form() -> None:
    one = parsed({**DJ, "keep_whole": [3, 0], "hard_cut_in": [2]})
    two = parsed({"hard_cut_in": [2], "trim": True, "keep_whole": [0, 3], **DJ})
    assert one == two
    assert one.keep_whole == (0, 3)
    assert (
        canonical(one)
        == canonical(two)
        == (
            '{"blend":true,"dj_mix":true,"hard_cut_in":[2],"hard_cut_out":[],"keep_whole":[0,3],'
            '"trim":true}'
        )
    )
    assert parsed({**DJ}) == MixChoices.dj_defaults()
    assert parsed({"dj_mix": False, "blend": False, "trim": False}) == MixChoices.whole_songs()


def test_a_position_outside_the_list_is_invalid_input() -> None:
    positions_refusal(parsed({**DJ, "hard_cut_out": [3]}), 4)
    for key in ("keep_whole", "hard_cut_in", "hard_cut_out"):
        with pytest.raises(MixChoiceRefused) as caught:
            positions_refusal(parsed({**DJ, key: [4]}), 4)
        assert caught.value.reason == "invalid_input"
