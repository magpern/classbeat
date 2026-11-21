"""#1330: the render plan's transition curves. ``render_policy`` enforces the allowlist
(``equal_power``, ``bass_swap``) and where a bass swap may go; ``render_curves`` (loaded by path by
the child) shapes it with numpy filters only. ``equal_power`` renders sample for sample as before,
and a bass swap is deterministic, keeps the overlap's level near equal power and the length.
Synthetic audio only, no network."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile

from classbeat.studio import render, render_policy
from test_studio_render import entry, load_child

RATE = render_policy.RATE
S = RATE


def curves() -> Any:
    return load_child().load(str(render.CHILD.parent), "render_curves")


def pair(**incoming: Any) -> list[dict[str, Any]]:
    """Two 6 s tracks overlapping by 4 s from 2 s on: 8 s composed."""
    return [
        entry(Path("a.mp3"), 0, 6 * S, 0, fade_out=4 * S),
        {**entry(Path("b.mp3"), 0, 6 * S, 2 * S, fade_in=4 * S), **incoming},
    ]


def valid(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return render_policy.validate_plan({"output": "o.mp3", "tracks": tracks})


# -- the allowlist (render_policy) -------------------------------------------------------------


def test_the_curves_are_an_allowlist() -> None:
    assert render_policy.CURVES == ("equal_power", "bass_swap")
    assert valid(pair())
    assert valid(pair(curve="equal_power"))
    assert valid(pair(curve="bass_swap", swap=2 * S))


@pytest.mark.parametrize(
    "incoming",
    [
        {"curve": "echo_out"},  # not on the allowlist
        {"curve": "BASS_SWAP", "swap": 2 * S},
        {"curve": None},
        {"curve": 1},
        {"curve": "equal_power", "swap": 2 * S},  # a swap is a bass swap's only
        {"curve": "bass_swap"},  # no swap point
        {"curve": "bass_swap", "swap": render_policy.SWAP_RAMP_FRAMES - 1},  # no room to hand over
        {"curve": "bass_swap", "swap": 4 * S},  # at the end of the fade-in: not inside it
        {"curve": "bass_swap", "swap": True},
        {"curve": "bass_swap", "swap": 2.5 * S},
        {"curve": "bass_swap", "swap": 2 * S, "fade_in": 3 * S},  # not the previous fade-out
        {"curve": "bass_swap", "swap": 2 * S, "dst_start": 2 * S + 1},  # not one overlap
        {"gain": 2},  # not a field of a track
    ],
)
def test_a_curve_off_the_allowlist_or_not_on_one_overlap_is_refused(incoming: Any) -> None:
    with pytest.raises(ValueError, match=r"curve|track|cut|order|gap"):
        valid(pair(**incoming))


def test_a_bass_swap_into_the_first_track_is_refused() -> None:
    first = {**entry(Path("a.mp3"), 0, 6 * S, 0, fade_in=S), "curve": "bass_swap", "swap": S // 2}
    with pytest.raises(ValueError, match="curve"):
        valid([first])


def test_a_curve_never_changes_the_composed_length() -> None:
    plain, swapped = valid(pair()), valid(pair(curve="bass_swap", swap=2 * S))
    assert render_policy.composed_frames(swapped) == render_policy.composed_frames(plain) == 8 * S


# -- render_curves, unit -----------------------------------------------------------------------


def test_without_a_bass_swap_the_blocks_are_the_equal_power_gains_exactly() -> None:
    child, rc = load_child(), curves()
    rng = np.random.default_rng(3)
    blocks = [rng.uniform(-1, 1, (n, 2)).astype("float32") for n in (1000, 4096, 7)]
    track = pair()[1]
    out = list(rc.shaped(iter(blocks), track, None, child.gains, render_policy.SWAP_RAMP_FRAMES))
    offset, want = 0, []
    for b in blocks:
        want.append(b * child.gains(track, offset, len(b)))
        offset += len(b)
    assert len(out) == len(want)
    assert all(np.array_equal(o, w) for o, w in zip(out, want, strict=True))


def test_the_low_band_keeps_the_bass_and_drops_the_highs_without_delay() -> None:
    rc = curves()
    t = np.arange(4 * S) / S
    bass, high = np.sin(2 * np.pi * 60 * t), np.sin(2 * np.pi * 2000 * t)
    low = rc.lowpass(np.stack([bass + high, bass + high], axis=1).astype("float32"))[:, 0]
    middle = slice(S, 3 * S)  # away from the zero padding at the ends
    assert np.max(np.abs(low[middle] - bass[middle])) < 0.02  # in phase: no delay
    assert rc.kernel().sum() == pytest.approx(1.0)


def test_the_low_bands_handover_ends_on_the_swap_point_at_equal_power() -> None:
    rc = curves()
    ramp, swap = render_policy.SWAP_RAMP_FRAMES, 10_000
    i = np.arange(swap - 2 * ramp, swap + ramp, dtype="float64")
    up, down = rc.low_gain(i, swap, ramp, True), rc.low_gain(i, swap, ramp, False)
    assert up[0] == 0.0
    assert down[0] == 1.0
    assert up[2 * ramp] == pytest.approx(1.0)
    assert down[2 * ramp] == pytest.approx(0.0, abs=1e-12)
    assert np.allclose(up**2 + down**2, 1.0)


def test_where_both_band_gains_are_one_the_bands_add_up_to_the_source() -> None:
    child, rc = load_child(), curves()
    rng = np.random.default_rng(5)
    source = rng.uniform(-0.5, 0.5, (6 * S, 2)).astype("float32")
    track = pair(curve="bass_swap", swap=2 * S)[1]
    blocks = [source[n : n + 65536] for n in range(0, len(source), 65536)]
    out = np.concatenate(list(rc.shaped(blocks, track, None, child.gains, 441)))
    assert out.shape == source.shape
    assert np.allclose(out[4 * S :], source[4 * S :], atol=1e-6)  # after the fade-in: the source
    assert np.allclose(out[:100], 0.0, atol=1e-3)  # it starts silent, as the fade does


# -- the render --------------------------------------------------------------------------------


def bassy(path: Path, hz: float, seed: int) -> Path:
    """A bass tone over quiet noise (a kick and bass line, crudely)."""
    t = np.arange(6 * S) / S
    rng = np.random.default_rng(seed)
    wave = 0.3 * np.sin(2 * np.pi * hz * t)[:, None] + rng.uniform(-0.15, 0.15, (6 * S, 2))
    soundfile.write(str(path), wave.astype("float32"), RATE, format="MP3")
    return path


def tracks_of(work: Path, **incoming: Any) -> list[dict[str, Any]]:
    one, two = bassy(work / "a.mp3", 60.0, 1), bassy(work / "b.mp3", 90.0, 2)
    return [
        entry(one, 0, 6 * S, 0, fade_out=4 * S),
        {**entry(two, 0, 6 * S, 2 * S, fade_in=4 * S), **incoming},
    ]


async def test_equal_power_renders_sample_for_sample_as_before(tmp_path: Path) -> None:
    before = await render.render_files(tmp_path, tracks_of(tmp_path))
    kept = before.path.read_bytes()
    named = await render.render_files(tmp_path, tracks_of(tmp_path, curve="equal_power"))
    assert named.sha256 == before.sha256
    assert named.path.read_bytes() == kept


async def test_a_bass_swap_is_deterministic_and_keeps_the_length(tmp_path: Path) -> None:
    plain = await render.render_files(tmp_path, tracks_of(tmp_path))
    swap = tracks_of(tmp_path, curve="bass_swap", swap=2 * S)
    first = await render.render_files(tmp_path, swap)
    kept = first.path.read_bytes()
    second = await render.render_files(tmp_path, swap)
    assert first.sha256 == second.sha256 != plain.sha256
    assert second.path.read_bytes() == kept
    assert first.frames == plain.frames
    assert render_policy.length_ok(first.duration_sec, 8.0)


def level(mono: Any, a: float, b: float) -> float:
    return float(np.sqrt(np.mean(mono[int(a * S) : int(b * S)] ** 2)))


def magnitude(mono: Any, a: float, b: float, hz: float) -> float:
    part = mono[int(a * S) : int(b * S)] * np.hanning(int(b * S) - int(a * S))
    spectrum = np.abs(np.fft.rfft(part))
    return float(spectrum[round(hz * len(part) / S)])


async def test_a_bass_swap_hands_the_bass_over_at_the_swap_at_equal_power(
    tmp_path: Path,
) -> None:
    """The overlap runs from 2 s to 6 s of the mix and the swap point is at 4 s."""
    plain = await render.render_files(tmp_path, tracks_of(tmp_path))
    base = soundfile.read(str(plain.path), dtype="float32")[0][:, 0].astype("float64")
    swap = tracks_of(tmp_path, curve="bass_swap", swap=2 * S)
    done = await render.render_files(tmp_path, swap)
    mono = soundfile.read(str(done.path), dtype="float32")[0][:, 0].astype("float64")
    before, after = (2.3, 3.9), (4.1, 5.7)
    assert magnitude(mono, *before, 90.0) < 0.1 * magnitude(base, *before, 90.0)  # held back
    assert magnitude(mono, *before, 60.0) > magnitude(base, *before, 60.0)  # kept whole
    assert magnitude(mono, *after, 60.0) < 0.1 * magnitude(base, *after, 60.0)  # handed over
    assert magnitude(mono, *after, 90.0) > magnitude(base, *after, 90.0)
    for start in (2.2, 2.7, 3.2, 3.7, 4.2, 4.7, 5.2):  # the level stays near equal power
        ratio = level(mono, start, start + 0.5) / level(base, start, start + 0.5)
        assert 20 * np.log10(ratio) == pytest.approx(0.0, abs=1.5)
    assert np.allclose(mono[: int(1.9 * S)], base[: int(1.9 * S)], atol=0.02)  # before: as today
