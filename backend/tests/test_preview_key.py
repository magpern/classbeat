"""The audition's pure rules (#1394, ADR 0042 sections 6.3 and 6.4): the clip key, the clip window
in milliseconds and the listening session's limits. No I/O."""

import ast
import dataclasses
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from classbeat.preview import identity, session_policy
from classbeat.preview.clip_window_calc import (
    MAX_WINDOW_MS,
    ClipWindow,
    clip_window,
    frames_of,
    render_window,
)
from classbeat.preview.key_calc import Identity, KeyInputs, clip_key, plan_sha256
from classbeat.studio import preview_window_calc, render, renderchild
from test_studio_preview_window_calc import S, chain

PLAN = [{"reference": "trk_a", "gain": 1.0}, {"reference": "trk_b", "gain": 0.5}]
IDENTITY = Identity(dict(identity.ENCODER), "libsndfile-1.2.2", "1.1.0/0.1.3", 7)
BASE = KeyInputs(plan_sha256(PLAN), {"trk_a": "a" * 64, "trk_b": "b" * 64}, (0, 60_000), IDENTITY)


def _with(**over: Any) -> KeyInputs:
    values = {
        "plan_sha256": BASE.plan_sha256,
        "sources": BASE.sources,
        "window": BASE.window,
        "identity": BASE.identity,
    }
    return KeyInputs(**(values | over))


def _id(**over: Any) -> Identity:
    return dataclasses.replace(IDENTITY, **over)


def test_every_key_input_changes_the_key() -> None:
    key = clip_key(BASE)
    changed = [
        _with(plan_sha256=plan_sha256([*PLAN, {"reference": "trk_c"}])),
        _with(sources={**BASE.sources, "trk_b": "c" * 64}),
        _with(window=(0, 60_001)),
        _with(window=(1, 60_000)),
        _with(identity=_id(encoder={**IDENTITY.encoder, "compression_level": 0.5})),
        _with(identity=_id(libsndfile="libsndfile-1.2.3")),
        _with(identity=_id(soxr="1.1.1/0.1.3")),
        _with(identity=_id(renderer=8)),
    ]
    keys = [clip_key(inputs) for inputs in changed]
    assert key not in keys
    assert len(set(keys)) == len(keys)


def test_the_key_is_canonical() -> None:
    reordered = _with(sources=dict(reversed(list(BASE.sources.items()))))
    assert clip_key(reordered) == clip_key(BASE)
    assert plan_sha256([dict(reversed(list(p.items()))) for p in PLAN]) == plan_sha256(PLAN)
    assert len(clip_key(BASE)) == 64


def test_the_encoder_in_the_key_is_what_the_child_writes_with() -> None:
    """``identity.ENCODER`` mirrors the child's ``soundfile.SoundFile`` arguments: read them from
    its source, so a change there that is not made here fails."""
    tree = ast.parse(inspect.getsource(renderchild))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "SoundFile"
        and any(k.arg == "format" for k in node.keywords)
    ]
    (writer,) = calls
    written: dict[str, Any] = {}
    for keyword in writer.keywords:
        if keyword.arg == "samplerate":
            written["samplerate"] = identity.ENCODER["samplerate"]  # ``policy.RATE``
            assert ast.unparse(keyword.value) == "policy.RATE"
        elif keyword.arg is not None:
            written[keyword.arg] = ast.literal_eval(keyword.value)
    assert written == identity.ENCODER
    assert identity.current().renderer == render.RENDERER_VERSION


def _plan(seconds: float) -> list[dict[str, Any]]:
    """Two tracks of ``seconds / 2`` with an 8 s crossfade (the placement's own fixtures)."""
    half = int(seconds * S / 2)
    return chain([half + 4 * S, half + 4 * S], [8 * S])


def test_a_window_is_whole_ms_and_joins_the_next_on_the_same_sample() -> None:
    tracks = _plan(1200.0)
    first = clip_window(tracks, 0)
    assert 0 < first.length_ms <= MAX_WINDOW_MS
    second = clip_window(tracks, first.end_ms)
    assert render_window(first, tracks)[1] == render_window(second, tracks)[0]
    assert render_window(first, tracks)[0] == frames_of(0)
    for bad in (-1, True, 10**9):
        with pytest.raises(ValueError, match="start"):
            clip_window(tracks, bad)  # pyright: ignore[reportArgumentType]


def test_the_last_window_ends_on_the_mixs_last_sample() -> None:
    tracks = _plan(100.5)
    window = clip_window(tracks, 0)
    total = preview_window_calc.total_frames(tracks)
    assert render_window(window, tracks)[1] == total
    assert window == ClipWindow(0, window.end_ms)


NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
LATER = NOW + timedelta(minutes=10)


def test_a_clip_is_counted_once_per_session_and_never_past_the_cap() -> None:
    first = session_policy.delivery(NOW, LATER, 0, (), "k1", 60_000)
    assert first == session_policy.Delivery(True, counts=True)
    again = session_policy.delivery(NOW, LATER, 60_000, ("k1",), "k1", 60_000)
    assert again == session_policy.Delivery(True, counts=False)  # a Range re-request is free
    cap = session_policy.DELIVERY_CAP_MS
    assert session_policy.delivery(NOW, LATER, cap - 60_000, (), "k2", 60_000).allowed
    assert not session_policy.delivery(NOW, LATER, cap - 59_999, (), "k2", 60_000).allowed
    assert session_policy.delivery(NOW, LATER, cap, ("k1",), "k1", 60_000).allowed
    assert session_policy.would_pass(cap - 59_999, (), "k2", 60_000)
    assert not session_policy.would_pass(cap, ("k2",), "k2", 60_000)


def test_an_expired_session_serves_nothing() -> None:
    assert session_policy.delivery(LATER, LATER, 0, ("k1",), "k1", 60_000) == session_policy.ENDED
    assert session_policy.delivery(NOW, LATER, 0, (), "k1", 0) == session_policy.ENDED
    many = tuple(f"k{n}" for n in range(session_policy.MAX_COUNTED))
    assert not session_policy.delivery(NOW, LATER, 0, many, "new", 1).allowed
