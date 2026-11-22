"""#1391 (ADR 0042 sections 3 and 8): ``swedebeat_studio.revise_overlay.reconciler``'s checks after
the model's draft is re-fitted. A timing-locked block that the re-fit (``agent.design.check``)
moves is ``overlay_violated``, a correction round; a re-fit that moves an unlocked block keeps the
lock; an overlay pushed past its bound is a correction round too, never the job's end. The checked
draft and the composed map are stand-ins (the re-fit is the code under test's input); pure."""

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from agentfake import good_draft
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.design.edit_overlay_model import Lock, Target, of_records
from classbeat.design.workout_edit_policy import EditRefused, blocks
from classbeat.swedebeat_studio import revise_overlay

BASE = WorkoutDraft.model_validate(good_draft()).model_dump(mode="json", exclude_none=True)


def lock(at: int) -> Lock:
    b = blocks(BASE)[at]
    return Lock(
        type="lock",
        target=Target(segment_kind=b.kind, span=[b.start_ms, b.end_ms]),  # pyright: ignore[reportArgumentType]
        scope="timing",
    )


def spec(content: dict[str, Any], moved: int | None = None) -> dict[str, Any]:
    """A check's fitted spec of ``content``: its lengths, one second moved from block
    ``moved + 1`` to block ``moved`` (the re-fit's snap) when given."""
    out = [
        {"intervals": [{"sec": i["sec"], "rpm": [80, 90]} for i in s["intervals"]]}
        for s in content["segments"]
    ]
    flat = [i for s in out for i in s["intervals"]]
    if moved is not None:
        flat[moved]["sec"] += 1
        flat[moved + 1]["sec"] -= 1
    return {"segments": out}


def result(content: dict[str, Any], moved: int | None = None) -> Any:
    draft = WorkoutDraft.model_validate(content)
    duration = sum(i["sec"] for s in content["segments"] for i in s["intervals"])
    return SimpleNamespace(draft=draft, spec=spec(content, moved), duration_sec=duration)


COMPOSED: Any = SimpleNamespace(plan={"tracks": [], "crossfades": []}, map={})
MUSIC: Any = SimpleNamespace(references=(), tracks=(), facts=dict)  # no pin: never composed


def reconcile(monkeypatch: pytest.MonkeyPatch, moved: int | None) -> Any:
    """The reconciler of a timing lock on block 0, its re-fit moving block ``moved``."""
    monkeypatch.setattr(revise_overlay, "check", lambda draft, _map: result(BASE, moved))
    overlay = of_records([lock(0)])
    run = revise_overlay.reconciler(BASE, overlay, MUSIC, 2700, {}, None)
    return run(result(copy.deepcopy(BASE)), COMPOSED)


def test_a_refit_that_moves_a_timing_locked_block_is_overlay_violated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ToolError) as refused:
        reconcile(monkeypatch, 0)
    assert refused.value.code == "overlay_violated"


def test_a_refit_that_moves_an_unlocked_block_keeps_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = reconcile(monkeypatch, 2)
    assert done.mix_spec is None  # no pin: the model's spec is stored as it is
    (record,) = done.overlay.records
    assert isinstance(record, Lock)
    assert record.target.span == [0, blocks(BASE)[0].end_ms]


def test_an_overlay_past_its_bound_is_a_correction_round(monkeypatch: pytest.MonkeyPatch) -> None:
    def too_large(_records: Any) -> Any:
        raise EditRefused("overlay_too_large")

    monkeypatch.setattr(revise_overlay, "overlay_of", too_large)
    with pytest.raises(ToolError) as refused:
        reconcile(monkeypatch, None)
    assert refused.value.code == "overlay_too_large"
