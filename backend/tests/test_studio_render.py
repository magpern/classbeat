"""Issue #1092 (SR C1): the sandboxed mix render child (``classbeat.studio.renderchild``), its
parent (``studio.render``) and the pure policy (``studio.render_policy``).
Synthetic audio only, no network; the Linux-only checks run in CI.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import soundfile

from classbeat.design import mixfetch, sandbox
from classbeat.studio import render, render_policy
from classbeat.studio.render import RenderFailed

# ruff: noqa: S603

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")
RATE = render_policy.RATE


def load_child() -> ModuleType:
    spec = importlib.util.spec_from_file_location("renderchild_under_test", render.CHILD)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tone(path: Path, seconds: float, hz: float, rate: int = RATE, fmt: str = "MP3") -> Path:
    t = np.arange(int(seconds * rate)) / rate
    wave = (0.5 * np.sin(2 * np.pi * hz * t)).astype("float32")
    soundfile.write(str(path), np.stack([wave, wave], axis=1), rate, format=fmt)
    return path


def entry(path: Path, src_start: int, src_end: int, dst_start: int, **fades: int) -> dict[str, Any]:
    return {
        "path": str(path),
        "src_start": src_start,
        "src_end": src_end,
        "dst_start": dst_start,
        "fade_in": fades.get("fade_in", 0),
        "fade_out": fades.get("fade_out", 0),
    }


def three_tracks(work: Path) -> list[dict[str, Any]]:
    """Three 6 s files cut to 4 s each, overlapping by 1 s: 10 s composed."""
    files = [tone(work / f"t{i}.mp3", 6.0, 220.0 * (i + 1)) for i in range(3)]
    s = RATE
    return [
        entry(files[0], 1 * s, 5 * s, 0, fade_out=s),
        entry(files[1], 0, 4 * s, 3 * s, fade_in=s, fade_out=s),
        entry(files[2], 2 * s, 6 * s, 6 * s, fade_in=s),
    ]


async def run(work: Path, tracks: list[dict[str, Any]], **kw: Any) -> render.Rendered:
    return await render.render_files(work, tracks, **kw)


# --- the render ------------------------------------------------------------------------------


async def test_a_three_track_plan_renders_to_the_composed_length(tmp_path: Path) -> None:
    tracks = three_tracks(tmp_path)
    result = await run(tmp_path, tracks)
    assert render_policy.length_ok(result.duration_sec, 10.0)
    assert result.duration_sec == pytest.approx(10.0, abs=0.1)
    data, rate = soundfile.read(str(result.path), dtype="float32")
    assert rate == RATE
    assert data.shape[1] == 2
    assert abs(len(data) - result.frames) < 5
    assert result.sha256
    assert result.peak_rss is None or result.peak_rss < render.MAX_RSS_BYTES


async def test_the_output_is_320_kbit_cbr(tmp_path: Path) -> None:
    result = await run(tmp_path, three_tracks(tmp_path))
    kbit = result.path.stat().st_size * 8 / result.duration_sec / 1000
    assert kbit == pytest.approx(320.0, rel=0.03)


async def test_the_same_input_gives_the_same_sha256(tmp_path: Path) -> None:
    tracks = three_tracks(tmp_path)
    first = await run(tmp_path, tracks)
    kept = first.path.read_bytes()
    second = await run(tmp_path, tracks)
    assert first.sha256 == second.sha256
    assert second.path.read_bytes() == kept


async def test_the_overlap_is_an_equal_power_crossfade(tmp_path: Path) -> None:
    s = RATE
    one = tone(tmp_path / "a.mp3", 4.0, 440.0)
    two = tone(tmp_path / "b.mp3", 4.0, 440.0)
    tracks = [entry(one, 0, 4 * s, 0, fade_out=2 * s), entry(two, 0, 4 * s, 2 * s, fade_in=2 * s)]
    result = await run(tmp_path, tracks)
    data, _ = soundfile.read(str(result.path), dtype="float32")
    assert len(data) == pytest.approx(6 * s, abs=0.1 * s)
    mono = data[:, 0]
    # the same tone in phase on both sides: the level of an equal-power fade rises by at most
    # sqrt(2) in the middle; it never dips and the start and end are the full level
    peak = lambda a, b: float(np.abs(mono[int(a * s) : int(b * s)]).max())  # noqa: E731
    assert peak(0.2, 1.0) == pytest.approx(0.5, abs=0.05)
    assert peak(5.0, 5.8) == pytest.approx(0.5, abs=0.05)
    assert 0.5 <= peak(2.8, 3.2) <= 0.5 * 2**0.5 + 0.05
    assert peak(1.9, 2.1) > 0.45  # no hole where the first track fades out


async def test_a_gap_between_tracks_is_silence_and_another_rate_is_resampled(
    tmp_path: Path,
) -> None:
    s = RATE
    odd = tone(tmp_path / "odd.wav", 3.0, 330.0, rate=48000, fmt="WAV")
    other = tone(tmp_path / "other.mp3", 3.0, 330.0)
    tracks = [entry(odd, 0, 3 * s, 0), entry(other, 0, 3 * s, 4 * s)]
    result = await run(tmp_path, tracks)
    assert result.duration_sec == pytest.approx(7.0, abs=0.1)
    data, _ = soundfile.read(str(result.path), dtype="float32")
    assert np.abs(data[int(3.3 * s) : int(3.8 * s)]).max() < 0.01
    assert np.abs(data[int(1.0 * s) : int(2.0 * s)]).max() > 0.3


# --- failures: exit 3, nothing on stdout -------------------------------------------------------


async def test_a_plan_whose_src_end_exceeds_the_file_fails(tmp_path: Path) -> None:
    short = tone(tmp_path / "short.mp3", 2.0, 440.0)
    with pytest.raises(RenderFailed):
        await run(tmp_path, [entry(short, 0, 5 * RATE, 0)])


def run_child(
    work: Path, plan: object, *, argv0: str | None = None
) -> subprocess.CompletedProcess[bytes]:
    plan_path = work / "plan.json"
    plan_path.write_text(json.dumps(plan))
    return subprocess.run(
        [sys.executable, "-I", argv0 or str(render.CHILD), str(plan_path), str(2**31)],
        env=render.child_environment(work),
        cwd=work,
        capture_output=True,
        timeout=120,
        check=False,
    )


def test_the_child_exits_3_with_nothing_on_stdout_for_a_short_file_a_bad_plan_and_a_stray_path(
    tmp_path: Path,
) -> None:
    short = tone(tmp_path / "short.mp3", 2.0, 440.0)
    out = str(tmp_path / "mix.mp3")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    stray = tone(outside / "x.mp3", 2.0, 440.0)
    plans: list[object] = [
        {"output": out, "tracks": [entry(short, 0, 5 * RATE, 0)]},
        {"output": out, "tracks": []},
        {"output": out, "tracks": [entry(short, 0, RATE, 0)], "extra": 1},
        {"output": out, "tracks": [entry(stray, 0, RATE, 0)]},
        {"output": str(outside / "mix.mp3"), "tracks": [entry(short, 0, RATE, 0)]},
        [],
    ]
    for plan in plans:
        done = run_child(tmp_path, plan)
        assert done.returncode == 3
        assert done.stdout == b""


async def test_a_child_that_fails_or_hangs_is_a_render_failure(tmp_path: Path) -> None:
    tracks = three_tracks(tmp_path)
    with pytest.raises(RenderFailed):
        await run(tmp_path, tracks, argv=lambda *_: [sys.executable, "-c", "raise SystemExit(3)"])
    with pytest.raises(RenderFailed):
        await run(
            tmp_path,
            tracks,
            timeout_seconds=1.0,
            argv=lambda *_: [sys.executable, "-c", "import time; time.sleep(60)"],
        )


async def test_an_answer_that_does_not_match_the_file_is_refused(tmp_path: Path) -> None:
    tracks = three_tracks(tmp_path)
    good = await run(tmp_path, tracks)
    base = {
        "sha256": good.sha256,
        "frames": good.frames,
        "duration_sec": good.duration_sec,
        "peak_rss": None,
        "sandbox": {"syscalls": True, "files": False},
    }
    cases: list[dict[str, Any]] = [
        {**base, "sha256": "0" * 64},
        {**base, "duration_sec": 12.0},
        {**base, "duration_sec": "10"},
        {k: v for k, v in base.items() if k != "frames"},
    ]
    for answer in cases:
        code = f"print({json.dumps(json.dumps(answer))})"
        with pytest.raises(RenderFailed):
            await run(tmp_path, tracks, argv=lambda *_, c=code: [sys.executable, "-c", c])
    code = f"print({json.dumps(json.dumps(base))})"
    ok = await run(tmp_path, tracks, argv=lambda *_: [sys.executable, "-c", code])
    assert ok.sha256 == good.sha256  # the fake answers with the real file's facts


# --- the sandbox -------------------------------------------------------------------------------


def test_the_child_refuses_to_run_without_the_syscall_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    child = load_child()
    tracks = three_tracks(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"output": str(tmp_path / "mix.mp3"), "tracks": tracks}))
    real_load = child.load

    class NoFilter:
        @staticmethod
        def deny_syscalls() -> bool:
            raise sandbox.SandboxError("no seccomp")

    monkeypatch.setattr(child, "load", lambda d, n: NoFilter if n == "sandbox" else real_load(d, n))
    assert child.main(["renderchild", str(plan), str(2**31)]) == child.EXIT_FAILED
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "mix.mp3").exists()  # nothing was opened or written


@pytest.mark.parametrize(
    ("confined", "accepted"),
    [
        ({"syscalls": True, "files": False}, True),
        ({"syscalls": False, "files": True}, False),
        ({"syscalls": 1, "files": True}, False),
        ({}, False),
        ("yes", False),
    ],
)
async def test_on_linux_an_answer_without_the_syscall_filter_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confined: object, accepted: bool
) -> None:
    tracks = three_tracks(tmp_path)
    good = await run(tmp_path, tracks)
    monkeypatch.setattr(render, "LINUX", True)
    answer = {
        "sha256": good.sha256,
        "frames": good.frames,
        "duration_sec": good.duration_sec,
        "peak_rss": None,
        "sandbox": confined,
    }
    code = f"print({json.dumps(json.dumps(answer))})"
    again = run(tmp_path, tracks, argv=lambda *_: [sys.executable, "-c", code])
    if accepted:
        assert (await again).sha256 == good.sha256
    else:
        with pytest.raises(RenderFailed):
            await again


@pytest.mark.skipif(not sandbox.LINUX, reason="the sandbox is Linux's (the image)")
async def test_the_real_child_reports_the_filter_on_linux(tmp_path: Path) -> None:
    done = run_child(
        tmp_path, {"output": str(tmp_path / "mix.mp3"), "tracks": three_tracks(tmp_path)}
    )
    assert done.returncode == 0
    answer = json.loads(done.stdout)
    assert answer["sandbox"]["syscalls"] is True
    assert answer["sandbox"]["files"] is (sandbox.landlock_abi() > 0)
    assert set(answer) == {"sha256", "frames", "duration_sec", "peak_rss", "sandbox"}
    assert len(done.stdout.splitlines()) == 1


def test_the_child_is_run_by_path_and_imports_nothing_of_classbeat(tmp_path: Path) -> None:
    argv = render.child_argv(tmp_path / "plan.json", 1000)
    assert argv[:3] == [sys.executable, "-I", str(render.CHILD)]
    assert "-m" not in argv
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"output": str(tmp_path / "mix.mp3"), "tracks": three_tracks(tmp_path)})
    )
    done = subprocess.run(
        [sys.executable, "-I", "-X", "importtime", str(render.CHILD), str(plan), str(2**31)],
        env=render.child_environment(tmp_path),
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0
    imported = [line.rsplit("|", 1)[-1].strip() for line in done.stderr.decode().splitlines()]
    assert "soundfile" in imported
    assert not [n for n in imported if n.startswith(("classbeat", "sqlalchemy", "mowl"))]


# --- memory ------------------------------------------------------------------------------------


async def test_peak_rss_stays_under_the_cap_for_a_long_plan(tmp_path: Path) -> None:
    """Twenty minutes of output from twenty one-minute cuts: memory is the overlap, not the mix."""
    source = tone(tmp_path / "long.mp3", 70.0, 261.6)
    s = RATE
    fade = 5 * s
    tracks = [
        entry(source, 5 * s, 65 * s, i * 55 * s, fade_in=fade if i else 0, fade_out=fade)
        for i in range(20)
    ]
    result = await run(tmp_path, tracks)
    assert result.duration_sec == pytest.approx(19 * 55 + 60, abs=0.1)
    assert result.peak_rss is not None
    assert result.peak_rss < render.MAX_RSS_BYTES


# --- the private directory ---------------------------------------------------------------------


async def test_the_work_directory_is_removed_in_a_finally(tmp_path: Path) -> None:
    seen: list[Path] = []

    async def prepare(work: Path) -> list[dict[str, Any]]:
        seen.append(work)
        return three_tracks(work)

    async with render.rendered_mix(prepare, root=tmp_path) as result:
        assert result.path.is_file()
        assert result.path.parent == seen[0]
        assert seen[0].parent == tmp_path
    assert not seen[0].exists()

    async def broken(work: Path) -> list[dict[str, Any]]:
        seen.append(work)
        short = tone(work / "s.mp3", 1.0, 440.0)
        return [entry(short, 0, 9 * RATE, 0)]

    with pytest.raises(RenderFailed):
        async with render.rendered_mix(broken, root=tmp_path):
            pytest.fail("a failed render yields nothing")
    assert not seen[1].exists()

    async def raising(work: Path) -> list[dict[str, Any]]:
        seen.append(work)
        raise RuntimeError("download failed")

    with pytest.raises(RuntimeError):
        async with render.rendered_mix(raising, root=tmp_path):
            pytest.fail("nothing to render")
    assert not seen[2].exists()
    assert not [p for p in seen if p.exists()]
    assert all(p.name.startswith(mixfetch.PREFIX) for p in seen)  # the fetch's private directories


# --- the pure policy ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duration", "composed", "ok"),
    [
        (600.0, 600.0, True),
        (600.09, 600.0, True),
        (599.91, 600.0, True),
        (600.11, 600.0, False),
        (599.8, 600.0, False),
        (0.0, 0.0, False),
        (float("nan"), 600.0, False),
        (float("inf"), float("inf"), False),
        (-1.0, 600.0, False),
    ],
)
def test_length_ok_has_a_100_ms_tolerance(duration: float, composed: float, ok: bool) -> None:
    assert render_policy.length_ok(duration, composed) is ok


def good_track(**changes: Any) -> dict[str, Any]:
    return {**entry(Path("a.mp3"), 0, 1000, 0), **changes}


@pytest.mark.parametrize(
    "plan",
    [
        None,
        {"output": "o", "tracks": []},
        {"output": "", "tracks": [good_track()]},
        {"output": "o", "tracks": [good_track(src_end=0)]},
        {"output": "o", "tracks": [good_track(src_start=-1)]},
        {"output": "o", "tracks": [good_track(fade_in=600, fade_out=600)]},
        {"output": "o", "tracks": [good_track(fade_in=1.5)]},
        {"output": "o", "tracks": [good_track(fade_in=True)]},
        {"output": "o", "tracks": [good_track(dst_start=500), good_track(dst_start=100)]},
        {"output": "o", "tracks": [good_track(dst_start=render_policy.MAX_FRAMES)]},
        {"output": "o", "tracks": [{**good_track(), "extra": 1}]},
        {"output": "o", "tracks": [good_track()] * (render_policy.MAX_TRACKS + 1)},
    ],
)
def test_a_plan_that_is_not_well_formed_is_refused(plan: object) -> None:
    with pytest.raises(ValueError, match=r".+"):
        render_policy.validate_plan(plan)


def test_a_good_plan_is_accepted_and_its_length_is_where_the_last_track_ends() -> None:
    tracks = render_policy.validate_plan(
        {"output": "o", "tracks": [good_track(), good_track(dst_start=800, src_end=2000)]}
    )
    assert render_policy.composed_frames(tracks) == 800 + 2000
