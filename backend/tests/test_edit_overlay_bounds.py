"""#1391 (ADR 0042 section 3): ``design.edit_overlay_model``'s application bound, at most
``MAX_RECORDS`` records and ``MAX_BYTES`` of canonical JSON, at and past each limit; the user-edit
path's refusal past it is ``overlay_too_large``. Pure."""

from typing import Any

import pytest

from classbeat.design.edit_overlay_model import (
    MAX_BYTES,
    MAX_RECORDS,
    Lock,
    Override,
    Record,
    Target,
    of_records,
)
from classbeat.design.revision_hash import canonical
from classbeat.design.workout_edit_policy import EditRefused
from classbeat.swedebeat_studio.studio_edit_calc import overlay_of


def lock(n: int) -> Lock:
    return Lock(type="lock", target=Target(segment_kind="main", span=[n, n + 1]), scope="all")


def cue(n: int, text: str) -> Override:
    return Override(
        type="override",
        target=Target(segment_kind="main", span=[n, n + 1]),
        field="cue",
        value=text,
        ai_value_at_set="",
    )


def size(records: list[Any]) -> int:
    return len(canonical({"version": 1, "records": [r.model_dump(mode="json") for r in records]}))


def test_the_record_count_is_bounded_at_200() -> None:
    assert MAX_RECORDS == 200
    at = [lock(n) for n in range(MAX_RECORDS)]
    assert size(at) <= MAX_BYTES  # the count, not the size, is what the next one breaks
    assert len(of_records(at).records) == MAX_RECORDS
    with pytest.raises(ValueError, match="200"):
        of_records([*at, lock(MAX_RECORDS)])
    with pytest.raises(EditRefused) as refused:
        overlay_of([*at, lock(MAX_RECORDS)])
    assert refused.value.reason == "overlay_too_large"


def exactly(total: int) -> list[Record]:
    """Cue overrides of exactly ``total`` canonical bytes (ASCII: one byte a character)."""
    records = [cue(n, "x" * 100) for n in range(MAX_RECORDS)]
    n = max(k for k in range(MAX_RECORDS) if size(records[:k]) <= total)
    out: list[Record] = list(records[:n])
    slack = total - size(out)
    for at in range(n):
        add = min(120, slack)
        out[at] = out[at].model_copy(update={"ai_value_at_set": "y" * add})
        slack -= add
    assert slack == 0
    return out


def test_the_canonical_size_is_bounded_at_32_kb() -> None:
    assert MAX_BYTES == 32768
    at, over = exactly(MAX_BYTES), exactly(MAX_BYTES + 1)
    assert (size(at), size(over)) == (MAX_BYTES, MAX_BYTES + 1)
    assert len(at) < MAX_RECORDS  # the size, not the count, is what breaks
    assert len(of_records(at).records) == len(at)
    with pytest.raises(ValueError, match="32 KB"):
        of_records(over)
    with pytest.raises(EditRefused) as refused:
        overlay_of(over)
    assert refused.value.reason == "overlay_too_large"
