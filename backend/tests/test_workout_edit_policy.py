"""#1391 (ADR 0042 section 3): ``design.workout_edit_policy``'s refusals, one case each, on the
operations themselves (the studio path reaches them through ``studio_edit_ops``). Pure."""

import copy
from collections.abc import Callable
from typing import Any

import pytest

from classbeat.design import workout_edit_policy as policy
from classbeat.design.workout_edit_policy import EditRefused, total_sec


def interval(sec: int, **extra: Any) -> dict[str, Any]:
    return {"sec": sec, "zone": 2, "rpm": [80, 90], **extra}


# blocks: 0 warmup 60, 1 warmup 60, 2 main 100 (an FTP test), 3 main 50, 4 main 3580, 5 cooldown 60
CONTENT: dict[str, Any] = {
    "segments": [
        {"kind": "warmup", "intervals": [interval(60), interval(60)]},
        {
            "kind": "main",
            "intervals": [interval(100, ftp_test="ramp"), interval(50), interval(3580)],
        },
        {"kind": "cooldown", "intervals": [interval(60)]},
    ]
}
TOTAL = total_sec(CONTENT)
FULL = {"segments": [{"kind": "main", "intervals": [interval(60) for _ in range(60)]}]}

REFUSALS: list[tuple[str, Callable[[], Any]]] = [
    ("invalid_input", lambda: policy.split(CONTENT, 6, 10)),  # no such block
    ("invalid_input", lambda: policy.split(CONTENT, -1, 10)),
    ("block_locked", lambda: policy.drag(CONTENT, 0, 5, timing_locked={1})),
    ("ftp_test_fixed", lambda: policy.drag(CONTENT, 1, 5)),
    ("ftp_test_fixed", lambda: policy.split(CONTENT, 2, 50)),
    ("invalid_input", lambda: policy.drag(CONTENT, 5, 5)),  # the last block has no next one
    ("nothing_changed", lambda: policy.drag(CONTENT, 0, 0)),
    ("invalid_input", lambda: policy.move(CONTENT, 0, -1)),  # out of its segment
    ("invalid_input", lambda: policy.move(CONTENT, 3, 2)),  # not a neighbour
    ("block_locked", lambda: policy.move(CONTENT, 0, 1, timing_locked={1})),
    ("split_out_of_range", lambda: policy.split(CONTENT, 0, 0)),
    ("split_out_of_range", lambda: policy.split(CONTENT, 0, 60)),
    ("too_many_blocks", lambda: policy.split(FULL, 0, 30)),
    ("invalid_input", lambda: policy.merge(CONTENT, 1)),  # the last of its segment
    ("block_locked", lambda: policy.merge(CONTENT, 0, attribute_locked={1})),
    ("block_too_long", lambda: policy.merge(CONTENT, 3)),  # 50 + 3580 > 3600
    ("invalid_input", lambda: policy.set_attribute(CONTENT, 0, "sec", 5)),  # never the timing
    ("block_locked", lambda: policy.set_attribute(CONTENT, 0, "zone", 3, attribute_locked={0})),
    ("nothing_changed", lambda: policy.set_attribute(CONTENT, 0, "zone", 2)),
    ("nothing_changed", lambda: policy.duration(CONTENT, TOTAL)),
    ("duration_out_of_range", lambda: policy.duration(CONTENT, TOTAL - 60)),
    ("block_locked", lambda: policy.duration(CONTENT, TOTAL + 5, timing_locked={0, 1, 3, 4, 5})),
]


@pytest.mark.parametrize(("reason", "call"), REFUSALS)
def test_each_refusal(reason: str, call: Callable[[], Any]) -> None:
    before = copy.deepcopy(CONTENT)
    with pytest.raises(EditRefused) as refused:
        call()
    assert refused.value.reason == reason
    assert before == CONTENT  # a refusal changes nothing


ALLOWED: list[Callable[[], policy.Edited]] = [
    lambda: policy.drag(CONTENT, 0, 5),
    lambda: policy.move(CONTENT, 3, 1),
    lambda: policy.split(CONTENT, 0, 30),
    lambda: policy.split(CONTENT, 3, 25),
    lambda: policy.merge(CONTENT, 0),
    lambda: policy.set_attribute(CONTENT, 0, "zone", 3),
    lambda: policy.duration(CONTENT, TOTAL + 5),
]


@pytest.mark.parametrize("call", ALLOWED)
def test_the_same_operations_on_a_valid_input_are_made(call: Callable[[], policy.Edited]) -> None:
    made = call()
    assert made.content != CONTENT
    assert len(made.origin) == len(policy.blocks(made.content))
