"""ADR 0042 sections 6.1 and 6.2 (#1392): the windowed preview render. ``render_policy`` takes a
``window`` only from a ``purpose: "preview"`` plan; the render child decodes every track that meets
the window exactly as the full render does (from the file's first sample, no seek) and writes only
the window, so a clean window's PCM equals the same span of a full render (checked through a
test-only float WAV output of the child's own ``render``), and an MP3 window's decoded length is the
window's within 100 ms. Synthetic audio only, no network."""

import json
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import soundfile

from classbeat.studio import render, render_policy
from test_studio_render import entry, load_child, run_child

RATE = render_policy.RATE
S = RATE
TOLERANCE = 1e-6
START, MIX_END = 0, 40 * S


def policy_and_curves() -> tuple[ModuleType, ModuleType]:
    child = load_child()
    here = str(render.CHILD.parent)
    return child.load(here, "render_policy"), child.load(here, "render_curves")


def noise(path: Path, seconds: float, seed: int, rate: int = RATE, **fmt: Any) -> Path:
    """White noise (so a misplaced or mis-resampled sample shows) as stereo, or mono when asked."""
    rng = np.random.default_rng(seed)
    channels = fmt.pop("channels", 2)
    data = rng.uniform(-0.5, 0.5, (int(seconds * rate), channels)).astype("float32")
    soundfile.write(str(path), data, rate, **fmt)
    return path


def wav_writer(path: Path) -> soundfile.SoundFile:
    """The test-only float WAV output: no encoder, so the PCM is compared as it is."""
    return soundfile.SoundFile(
        str(path), "w", samplerate=RATE, channels=2, format="WAV", subtype="FLOAT"
    )


def pcm(
    work: Path, tracks: list[dict[str, Any]], window: tuple[int, int] | None = None
) -> np.ndarray[Any, Any]:
    """What ``renderchild.render`` writes for the plan (the window's span, or the whole mix)."""
    child = load_child()
    policy, curves = policy_and_curves()
    path = work / ("full.wav" if window is None else f"w{window[0]}-{window[1]}.wav")
    with wav_writer(path) as writer:
        written = child.render(tracks, writer, policy, str(work.resolve()), curves, window)
    data, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
    assert rate == RATE
    assert (
        written
        == len(data)
        == (policy.composed_frames(tracks) if window is None else window[1] - window[0])
    )
    return data


def mix(work: Path) -> list[dict[str, Any]]:
    """Five tracks, 40 s composed, one situation for each case of ADR 0042 section 6.2:

    - t0 (MP3): 0 to 10 s, fading out over 7 to 10;
    - t1 (48 kHz WAV, cases 11): 7 to 17 s, crossfades in over 7 to 10 and out over 14 to 17;
    - t2 (MP3): 14 to 24 s, a bass swap into it (the swap 2 s into the fade, case 5 and 6);
    - a gap of silence from 24 to 28 s (case 10);
    - t3 and t4 (22.05 kHz mono WAV): the same file in two pieces that meet at 34 s (a loop join,
      case 9; t4 starts back in the file); t4 ends the mix at 40 s (case 8)."""
    t0 = noise(work / "t0.mp3", 12.0, 1, format="MP3")
    t1 = noise(work / "t1.wav", 10.5, 2, rate=48000, format="WAV", subtype="PCM_16")
    t2 = noise(work / "t2.mp3", 10.5, 3, format="MP3")
    t3 = noise(work / "t3.wav", 12.0, 4, rate=22050, format="WAV", subtype="FLOAT", channels=1)
    swap = {"curve": "bass_swap", "swap": 2 * S}
    return [
        entry(t0, 1 * S, 11 * S, 0, fade_out=3 * S),
        entry(t1, 0, 10 * S, 7 * S, fade_in=3 * S, fade_out=3 * S),
        {**entry(t2, 0, 10 * S, 14 * S, fade_in=3 * S), **swap},
        entry(t3, 0, 6 * S, 28 * S),
        entry(t3, 2 * S, 8 * S, 34 * S),
    ]


@pytest.fixture(scope="module")
def full(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[dict[str, Any]], Any]:
    work = tmp_path_factory.mktemp("mix")
    tracks = mix(work)
    assert render_policy.validate_plan({"output": str(work / "o.mp3"), "tracks": tracks})
    return work, tracks, pcm(work, tracks)


def seconds(a: float, b: float) -> tuple[int, int]:
    return round(a * S), round(b * S)


def assert_window_is_the_full_span(full: Any, window: tuple[int, int], span: Any) -> None:
    expected = full[window[0] : window[1]]
    assert span.shape == expected.shape
    assert float(np.abs(span - expected).max()) <= TOLERANCE


# -- the 12 cases of ADR 0042 section 6.2 --------------------------------------------------------

CASES = {
    "1 inside one piece": (4.0, 6.0),
    "2 starting mid-crossfade": (8.5, 12.0),
    "3 ending mid-crossfade": (3.0, 8.5),
    "4 a whole crossfade": (6.5, 10.5),
    "5 a bass swap with its swap point and ramp": (13.0, 18.0),
    "6 starting inside a bass-swap fade": (15.2, 20.0),
    "7 mix start": (0.0, 5.0),
    "8 mix end": (35.0, 40.0),
    "9 a loop join": (31.0, 37.0),
    "10 a gap": (23.0, 29.0),
    "11 a source not at 44.1 kHz": (11.123456, 13.5),
}


@pytest.mark.parametrize("case", list(CASES))
def test_a_window_is_the_same_span_of_the_full_render(
    case: str, full: tuple[Path, list[dict[str, Any]], Any]
) -> None:
    work, tracks, whole = full
    window = seconds(*CASES[case])
    assert_window_is_the_full_span(whole, window, pcm(work, tracks, window))


def test_the_cases_cover_the_situations_they_name(
    full: tuple[Path, list[dict[str, Any]], Any],
) -> None:
    _, tracks, whole = full
    assert len(whole) == MIX_END == render_policy.composed_frames(tracks)
    assert CASES["8 mix end"][1] * S == MIX_END
    gap = whole[24 * S : 28 * S]
    assert float(np.abs(gap).max()) == 0.0  # the gap is silence
    assert tracks[2]["curve"] == "bass_swap"
    assert tracks[3]["path"] == tracks[4]["path"]
    assert tracks[4]["src_start"] < tracks[3]["src_end"]
    assert soundfile.info(tracks[1]["path"]).samplerate == 48000


def test_windows_at_unaligned_positions_and_lengths_equal_the_full_render(
    full: tuple[Path, list[dict[str, Any]], Any],
) -> None:
    work, tracks, whole = full
    rng = np.random.default_rng(7)
    for _ in range(8):
        start = int(rng.integers(0, MIX_END - 1))
        end = min(start + int(rng.integers(1, 12 * S)), MIX_END)
        assert_window_is_the_full_span(whole, (start, end), pcm(work, tracks, (start, end)))


def test_windows_at_the_edges_of_a_piece_take_its_first_and_last_sample(
    full: tuple[Path, list[dict[str, Any]], Any],
) -> None:
    """A window that ends one sample into a piece, or starts on its last one, has that sample."""
    work, tracks, whole = full
    t3_start, t2_end = 28 * S, 24 * S
    for window in [
        (t3_start - S, t3_start + 1),
        (t3_start - 1, t3_start + 1),
        (t2_end - 1, t2_end + 5),
        (MIX_END - 1, MIX_END),
        (0, 1),
    ]:
        assert_window_is_the_full_span(whole, window, pcm(work, tracks, window))
    assert float(np.abs(whole[t3_start]).max()) > 0.0  # the edge sample is not silence


@pytest.mark.parametrize(
    ("rate", "fmt"),
    [
        (22050, {"format": "WAV", "subtype": "FLOAT"}),
        (48000, {"format": "WAV"}),
        (44100, {"format": "MP3"}),
    ],
)
def test_a_piece_that_starts_mid_file_is_the_span_of_the_whole_file_decode(
    tmp_path: Path, rate: int, fmt: dict[str, str]
) -> None:
    """The child does not seek: a piece from ``src_start`` is the whole-file decode from the first
    sample (one resampler stream, one decoder), dropped until ``src_start``. A seek would start the
    resampler (and an MP3 decoder) cold and change those samples."""
    path = noise(tmp_path / "src", 6.0, 21, rate=rate, **fmt)
    child = load_child()
    start, end = 2 * S + 123, 5 * S + 7
    whole = np.concatenate(list(child.track_blocks(str(path), 0, 5 * S + 7, S)))
    piece = np.concatenate(list(child.track_blocks(str(path), start, end, S)))
    assert piece.shape == whole[start:end].shape
    assert float(np.abs(piece - whole[start:end]).max()) == 0.0


def test_contiguous_windows_tile_the_full_render(
    full: tuple[Path, list[dict[str, Any]], Any],
) -> None:
    work, tracks, whole = full
    edges = [0, 9 * S + 17, 15 * S, 15 * S + 441, 26 * S, MIX_END]
    spans = [pcm(work, tracks, (a, b)) for a, b in pairwise(edges)]
    joined = np.concatenate(spans)
    assert joined.shape == whole.shape
    assert float(np.abs(joined - whole).max()) <= TOLERANCE


def test_the_whole_mix_as_a_window_is_the_full_render(
    full: tuple[Path, list[dict[str, Any]], Any],
) -> None:
    work, tracks, whole = full
    assert_window_is_the_full_span(whole, (0, MIX_END), pcm(work, tracks, (0, MIX_END)))


def test_a_track_that_does_not_meet_the_window_is_not_opened(tmp_path: Path) -> None:
    tracks = mix(tmp_path)
    (tmp_path / "t0.mp3").unlink()  # the first track is over before the window starts
    child = load_child()
    policy, curves = policy_and_curves()
    with wav_writer(tmp_path / "w.wav") as writer:
        window = seconds(11.0, 13.0)
        assert child.render(tracks, writer, policy, str(tmp_path.resolve()), curves, window) == (
            window[1] - window[0]
        )
    with pytest.raises(ValueError, match="not audio"), wav_writer(tmp_path / "x.wav") as writer:
        child.render(tracks, writer, policy, str(tmp_path.resolve()), curves, seconds(2.0, 3.0))


# -- case 12: the earliest overlapping piece starts at least 5 minutes before the window ----------


def test_a_window_over_a_piece_that_started_five_minutes_earlier(tmp_path: Path) -> None:
    """The piece is decoded from the file's first sample (no seek) through one soxr stream, from
    8 kHz (a long, cheap source): the window's span is still the full render's."""
    a = noise(tmp_path / "a.wav", 6 * 60 + 5, 11, rate=8000, format="WAV", subtype="PCM_16")
    b = noise(tmp_path / "b.wav", 60.0, 12, rate=11025, format="WAV", subtype="PCM_16", channels=1)
    tracks = [
        entry(a, 0, 6 * 60 * S, 0, fade_out=20 * S),
        entry(b, 0, 40 * S, 6 * 60 * S - 20 * S, fade_in=20 * S),
    ]
    whole = pcm(tmp_path, tracks)
    window = seconds(5 * 60 + 10, 6 * 60 + 5)  # the first piece started 5:10 earlier
    assert window[0] - tracks[0]["dst_start"] >= 5 * 60 * S
    assert_window_is_the_full_span(whole, window, pcm(tmp_path, tracks, window))


# -- the output path is unchanged ------------------------------------------------------------------


def reference_render(
    tracks: list[dict[str, Any]], writer: Any, policy: ModuleType, work: str, curves: ModuleType
) -> int:
    """The full render as it was before windows existed (``renderchild.render`` of #1330), kept
    here as the oracle that the output path still writes the same samples."""
    child = load_child()
    mixer = child.Mixer()
    written = 0
    for i, track in enumerate(tracks):
        after = tracks[i + 1] if i + 1 < len(tracks) else None
        following = None if after is None else after["dst_start"]
        offset = 0
        blocks = child.track_blocks(
            child.inside(work, track["path"]), track["src_start"], track["src_end"], policy.RATE
        )
        for block in curves.shaped(blocks, track, after, child.gains, policy.SWAP_RAMP_FRAMES):
            mixer.add(track["dst_start"] + offset, block)
            offset += len(block)
            done = track["dst_start"] + offset
            ready = mixer.flush(done if following is None else min(done, following))
            if ready is not None:
                writer.write(ready)
                written += len(ready)
    ready = mixer.flush(policy.composed_frames(tracks))
    if ready is not None:
        writer.write(ready)
        written += len(ready)
    return written


def test_the_output_path_writes_the_samples_it_did_before_windows(
    full: tuple[Path, list[dict[str, Any]], Any],
) -> None:
    work, tracks, whole = full
    policy, curves = policy_and_curves()
    with wav_writer(work / "reference.wav") as writer:
        reference_render(tracks, writer, policy, str(work.resolve()), curves)
    data, _ = soundfile.read(str(work / "reference.wav"), dtype="float32", always_2d=True)
    assert np.array_equal(data, whole)


def mp3_plan(work: Path, tracks: list[dict[str, Any]], **more: Any) -> dict[str, Any]:
    return {"output": str(work / "mix.mp3"), "tracks": tracks, **more}


def sha256_of_the_child(work: Path, plan: dict[str, Any]) -> str:
    done = run_child(work, plan)
    assert done.returncode == 0, done.stderr
    return str(json.loads(done.stdout)["sha256"])


def test_the_published_sha256_is_the_same_with_and_without_the_purpose_key(tmp_path: Path) -> None:
    """``output`` is the default purpose: a plan that says it renders the very same bytes."""
    tracks = mix(tmp_path)
    base = sha256_of_the_child(tmp_path, mp3_plan(tmp_path, tracks))
    named = sha256_of_the_child(tmp_path, mp3_plan(tmp_path, tracks, purpose="output"))
    assert base == named


# -- an MP3 window ---------------------------------------------------------------------------------


@pytest.mark.parametrize("window", [(4.0, 6.0), (8.5, 12.0), (13.0, 18.0), (35.0, 40.0)])
def test_an_mp3_window_decodes_to_its_length_within_100_ms(
    tmp_path: Path, window: tuple[float, float]
) -> None:
    tracks = mix(tmp_path)
    a, b = seconds(*window)
    plan = mp3_plan(tmp_path, tracks, purpose="preview", window={"start": a, "end": b})
    done = run_child(tmp_path, plan)
    assert done.returncode == 0
    answer = json.loads(done.stdout)
    assert render_policy.length_ok(answer["duration_sec"], (b - a) / RATE)
    info = soundfile.info(str(tmp_path / "mix.mp3"))
    assert info.format == "MP3"
    assert render_policy.length_ok(info.frames / info.samplerate, (b - a) / RATE)
    assert answer["sandbox"]["syscalls"] is True or sys.platform != "linux"


def test_an_mp3_window_is_320_kbit_cbr(tmp_path: Path) -> None:
    tracks = mix(tmp_path)
    plan = mp3_plan(tmp_path, tracks, purpose="preview", window={"start": S, "end": 21 * S})
    assert run_child(tmp_path, plan).returncode == 0
    size = (tmp_path / "mix.mp3").stat().st_size
    assert 0.97 * 20 * 320_000 / 8 <= size <= 1.03 * 20 * 320_000 / 8


# -- the policy ------------------------------------------------------------------------------------


def plan_of(**more: Any) -> dict[str, Any]:
    return {"output": "o.mp3", "tracks": [entry(Path("a.mp3"), 0, 10 * S, 0)], **more}


def test_a_preview_plan_takes_a_window_and_an_output_plan_refuses_one() -> None:
    window = {"start": 2 * S, "end": 5 * S}
    preview = plan_of(purpose="preview", window=window)
    assert render_policy.validate_plan(preview)
    assert render_policy.plan_window(preview) == (2 * S, 5 * S)
    assert render_policy.plan_window(plan_of()) is None
    assert (
        render_policy.output_frames(render_policy.validate_plan(preview), (2 * S, 5 * S)) == 3 * S
    )
    assert render_policy.output_frames(render_policy.validate_plan(plan_of()), None) == 10 * S
    for refused in (plan_of(window=window), plan_of(purpose="output", window=window)):
        with pytest.raises(ValueError, match="no window"):
            render_policy.validate_plan(refused)
    assert render_policy.validate_plan(plan_of(purpose="output"))


@pytest.mark.parametrize(
    "more",
    [
        {"purpose": "preview"},  # a preview is a window
        {"purpose": "preview", "window": None},
        {"purpose": "preview", "window": []},
        {"purpose": "preview", "window": {"start": 0}},
        {"purpose": "preview", "window": {"start": 0, "end": S, "x": 1}},
        {"purpose": "preview", "window": {"start": S, "end": S}},  # empty
        {"purpose": "preview", "window": {"start": 2 * S, "end": S}},
        {"purpose": "preview", "window": {"start": -1, "end": S}},
        {"purpose": "preview", "window": {"start": 0, "end": 10 * S + 1}},  # past the mix
        {"purpose": "preview", "window": {"start": 0.0, "end": S}},
        {"purpose": "preview", "window": {"start": True, "end": S}},
        {"purpose": "preview", "window": {"start": "0", "end": S}},
        {"purpose": "Preview", "window": {"start": 0, "end": S}},
        {"purpose": None},
        {"purpose": ["output"]},
        {"purpose": "preview", "window": {"start": 0, "end": S}, "extra": 1},
    ],
)
def test_a_purpose_or_window_that_is_not_well_formed_is_refused(more: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        render_policy.validate_plan(plan_of(**more))


def test_no_window_is_over_6_minutes() -> None:
    six = 6 * 60 * S
    assert six == render_policy.MAX_WINDOW_FRAMES
    track = entry(Path("a.mp3"), 0, 7 * 60 * S, 0)
    ok = {"output": "o.mp3", "purpose": "preview", "tracks": [track]}
    assert render_policy.validate_plan({**ok, "window": {"start": 0, "end": six}})
    assert render_policy.validate_plan({**ok, "window": {"start": S, "end": six + S}})
    with pytest.raises(ValueError, match="window"):
        render_policy.validate_plan({**ok, "window": {"start": 0, "end": six + 1}})


def test_the_child_refuses_an_output_plan_with_a_window_and_a_bad_preview(tmp_path: Path) -> None:
    tracks = mix(tmp_path)
    window = {"start": 0, "end": S}
    plans = [
        mp3_plan(tmp_path, tracks, window=window),
        mp3_plan(tmp_path, tracks, purpose="output", window=window),
        mp3_plan(tmp_path, tracks, purpose="preview"),
        mp3_plan(tmp_path, tracks, purpose="preview", window={"start": 0, "end": 41 * S}),
    ]
    for plan in plans:
        done: subprocess.CompletedProcess[bytes] = run_child(tmp_path, plan)
        assert done.returncode == 3
        assert done.stdout == b""
        assert not (tmp_path / "mix.mp3").exists()


async def test_the_published_path_builds_an_output_plan(tmp_path: Path) -> None:
    tracks = mix(tmp_path)
    rendered = await render.render_files(tmp_path, tracks)
    plan = json.loads((tmp_path / render.PLAN_NAME).read_text())
    assert plan["purpose"] == "output"
    assert "window" not in plan
    assert render_policy.length_ok(rendered.duration_sec, MIX_END / RATE)
