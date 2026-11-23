"""#1451: ``design.spotify_overlay_calc``, the rules a Spotify draft's edits follow through an AI
revise and a refit. A base with no record carries nothing (the message and the stored draft are as
before); a cut pin or an anchor, which the Spotify edit never writes, is refused so the caller
fails closed; a refit whose new fit moves a timing-locked block is refused (the job's
``invalid_output``), one that moves another block keeps the lock. The checked draft is a stand-in
(the fit is the code under test's input); pure."""

import copy
from typing import Any

import pytest

from classbeat.agent.stage import ToolError
from classbeat.design import spotify_overlay_calc
from classbeat.design.edit_overlay_model import EMPTY, Anchor, Event, Lock, Target, of_records
from classbeat.design.workout_edit_policy import blocks
from classbeat.swedebeat_studio import revise_overlay
from test_revise_overlay import BASE, lock, result


def anchor() -> Anchor:
    b = blocks(BASE)[1]
    event = Event(
        reference="r0", occurrence=0, sha256="a" * 64, source_ms=1000, kind="drop", map_version=1
    )
    target = Target(segment_kind=b.kind, span=[b.start_ms, b.end_ms])  # pyright: ignore[reportArgumentType]
    return Anchor(type="anchor", target=target, edge="end", event=event)


def test_a_base_with_no_record_carries_nothing() -> None:
    assert spotify_overlay_calc.kept(BASE, EMPTY, None) is None
    done = result(copy.deepcopy(BASE))
    assert spotify_overlay_calc.refitted(done, BASE, EMPTY, {}) == (done, None)


def test_a_record_the_spotify_edit_never_writes_is_refused() -> None:
    overlay = of_records([lock(0), anchor()])
    with pytest.raises(ValueError, match="no cut pin and no anchor"):
        spotify_overlay_calc.kept(BASE, overlay, None)
    with pytest.raises(ValueError, match="no cut pin and no anchor"):
        spotify_overlay_calc.refitted(result(copy.deepcopy(BASE)), BASE, overlay, {})


def test_the_message_states_the_lock_as_fixed() -> None:
    kept = spotify_overlay_calc.kept(BASE, of_records([lock(0)]), None)
    assert kept is not None
    assert kept.fixed.startswith("The instructor's own edits are fixed")
    assert "locked (timing)" in kept.fixed


def test_a_refit_that_moves_a_timing_locked_block_is_refused() -> None:
    with pytest.raises(ToolError) as refused:
        spotify_overlay_calc.refitted(result(BASE, 0), BASE, of_records([lock(0)]), {})
    assert refused.value.code == "overlay_violated"


def test_a_refit_that_moves_another_block_keeps_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(revise_overlay, "check", lambda draft, _map: result(BASE, 2))
    done, overlay = spotify_overlay_calc.refitted(result(BASE, 2), BASE, of_records([lock(0)]), {})
    assert done.spec == result(BASE, 2).spec
    assert overlay is not None
    (record,) = overlay.records
    assert isinstance(record, Lock)
    assert record.target.span == [0, blocks(BASE)[0].end_ms]


def test_a_revise_reconciles_on_the_runs_map(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def check(draft: Any, music_map: Any) -> Any:
        seen.append(music_map)
        return result(BASE)

    monkeypatch.setattr(revise_overlay, "check", check)
    kept = spotify_overlay_calc.kept(BASE, of_records([lock(0)]), None)
    assert kept is not None
    _done, overlay = kept.reconcile(result(copy.deepcopy(BASE)), {"version": 7})
    assert seen == [{"version": 7}]
    assert [r.type for r in overlay.records] == ["lock"]
