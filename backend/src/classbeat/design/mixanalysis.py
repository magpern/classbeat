"""Run the analysis of one Swedebeat mix in a limited child process (ADR 0016 section 3 step 2).

The file is hostile until the decoder has opened it and the analysis has finished, and the
analysis is heavy (numpy, numba, librosa), so it never runs in the worker's own process: a crash,
an out-of-memory kill or a decoder bug ends the child and the job ends ``analysis_failed`` while
the worker lives on.

- **Limits.** ``MAX_RSS_BYTES`` (1.5 GiB of resident memory, watched every ``POLL_SECONDS`` by the
  parent and by a thread in the child itself, and the child ended past it), ``ANALYSIS_SECONDS``
  (180 s wall clock, then killed) and the decoded length
  (``decode_limit_seconds``: the listed length plus five minutes, never over 120 minutes; the child
  counts it as it decodes). The child's output is read up to ``MAX_OUTPUT_BYTES``.
- **Checkpoints.** The parent checkpoints the job every ``CHECKPOINT_SECONDS`` while it waits (the
  fence: lease and cancel); a cancel, a lost lease or a shutdown kills the child at once.
- **What the child gets**: the path of one file and a number, a minimal environment (no setting, no
  secret, no proxy) and nothing on stdin; its working directory is the private temp directory.
  Its stderr is discarded: a path or a file's own words never reach a log.
- **The answer** is checked here (``valid_map``): a map is stored and used only when it has the
  library's current map version and the shape every later step reads.
"""

import asyncio
import contextlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from classbeat.design.mixfetch import work_root
from classbeat.design.rss import process_rss
from classbeat.jobs import JobFailure, JobRun

MAX_RSS_BYTES: Final = int(1.5 * 1024**3)
ANALYSIS_SECONDS: Final = 180.0
POLL_SECONDS: Final = 0.1
CHECKPOINT_SECONDS: Final = 5.0
MAX_OUTPUT_BYTES: Final = 4 * 1024 * 1024
EXTRA_SECONDS: Final = 300.0  # the decoded length may exceed the listed one by this much
MAX_MIX_SECONDS: Final = 7200.0  # and never passes 120 minutes
MAX_MAP_BYTES: Final = 1 << 20  # the table's check on ``swedebeat_mix_maps.map``
MAX_SONGS: Final = 200
MAX_EVENTS: Final = 2000


def decode_limit_seconds(listed_seconds: float) -> float:
    """The most audio a mix may decode to: its listed length plus five minutes, never over 120
    minutes (ADR 0016 section 3 step 2)."""
    if not math.isfinite(listed_seconds) or listed_seconds < 0:
        raise ValueError("a length is a finite number of seconds")
    return min(listed_seconds + EXTRA_SECONDS, MAX_MIX_SECONDS)


def child_argv(audio: Path, max_seconds: float, max_rss: int) -> list[str]:
    """The child's command line. A function, so a test can run another program in its place."""
    return [
        sys.executable,
        "-I",
        "-m",
        "classbeat.design.mixchild",
        str(audio),
        repr(max_seconds),
        str(max_rss),
    ]


def numba_cache_dir() -> Path:
    """Where the child's compiled numba functions are kept between analyses (the image's user has
    no writable home): ``<work root>/numba-cache``, which the start-up sweep leaves alone. It holds
    compiled code and no audio."""
    cache = work_root() / "numba-cache"
    cache.mkdir(mode=0o700, exist_ok=True)
    return cache


def child_environment(work: Path) -> dict[str, str]:
    """The child's whole environment: the program search path (and the system root on Windows),
    one thread per library, a temporary and a numba cache directory. Nothing else of ours."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "NUMBA_CACHE_DIR": str(numba_cache_dir()),
        "NUMBA_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "TMPDIR": str(work),
        "TEMP": str(work),
        "TMP": str(work),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    if os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def valid_map(candidate: object, version: int) -> bool:
    """Whether ``candidate`` is a music map of the library's current ``version`` that fits the
    table and has the fields the design reads (songs with a BPM and a beat grid, events, a
    duration)."""
    if not isinstance(candidate, dict):
        return False
    m: Mapping[str, Any] = candidate  # pyright: ignore[reportUnknownVariableType]
    songs, events = m.get("songs"), m.get("events")
    if m.get("version") != version or not _number(m.get("duration")) or m["duration"] <= 0:
        return False
    if not isinstance(songs, list) or not 0 < len(songs) <= MAX_SONGS:
        return False
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        return False
    for song in songs:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(song, dict) or not all(
            _number(song.get(k))  # pyright: ignore[reportUnknownMemberType]
            for k in ("start", "end", "bpm", "first_downbeat")
        ):
            return False
    for event in events:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(event, dict) or not _number(event.get("t")):  # pyright: ignore[reportUnknownMemberType]
            return False
    try:
        return len(json.dumps(m, separators=(",", ":"), allow_nan=False)) <= MAX_MAP_BYTES
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class Analysis:
    music_map: dict[str, Any]
    peak_rss: int | None  # the highest resident memory seen of the child, in bytes


def _read_output(process: "subprocess.Popen[bytes]", limit: int) -> tuple[bytes, bool]:
    """In a thread: the child's stdout up to ``limit`` bytes; (data, True) past it."""
    if process.stdout is None:  # pragma: no cover - the pipe was asked for
        return b"", True
    chunks: list[bytes] = []
    size = 0
    while chunk := process.stdout.read(65536):
        size += len(chunk)
        if size > limit:
            process.kill()
            return b"", True
        chunks.append(chunk)
    return b"".join(chunks), False


def _kill(process: "subprocess.Popen[bytes]") -> None:
    with contextlib.suppress(OSError):
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):  # a kill that did not take
        process.wait(timeout=10)


async def analyse_audio(
    run: JobRun,
    audio: Path,
    *,
    max_seconds: float,
    map_version: int,
    timeout_seconds: float = ANALYSIS_SECONDS,
    max_rss: int = MAX_RSS_BYTES,
    clock: Callable[[], float] = time.monotonic,
    argv: Callable[[Path, float, int], Sequence[str]] | None = None,
) -> Analysis:
    """Analyse ``audio`` (a file in the job's private directory) in a limited child. Raises
    ``JobFailure('analysis_failed')`` for anything the child or its limits end, and what
    ``JobRun.checkpoint`` raises (``RunStopped``) after killing the child."""
    command = list((argv or child_argv)(audio, max_seconds, max_rss))
    work = audio.parent
    try:
        process = subprocess.Popen(  # noqa: S603, ASYNC220 - ours; the call returns at once
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_environment(work),
            cwd=work,
            close_fds=True,
        )
    except OSError:
        raise JobFailure("analysis_failed") from None
    output: list[tuple[bytes, bool]] = []

    def drain() -> None:
        output.append(_read_output(process, MAX_OUTPUT_BYTES))
        process.wait()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    started = clock()
    next_checkpoint = started + CHECKPOINT_SECONDS
    peak = 0
    try:
        while reader.is_alive():
            await asyncio.sleep(POLL_SECONDS)
            now = clock()
            rss = process_rss(process.pid)
            if rss is not None:
                peak = max(peak, rss)
                if rss > max_rss:
                    raise JobFailure("analysis_failed")
            if now - started > timeout_seconds:
                raise JobFailure("analysis_failed")
            if now >= next_checkpoint:
                next_checkpoint = now + CHECKPOINT_SECONDS
                await run.checkpoint()  # a cancel, a lost lease or a shutdown: RunStopped
    finally:
        if process.poll() is None:
            _kill(process)
        reader.join(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
    data, too_big = output[0] if output else (b"", False)
    if process.returncode != 0 or too_big:
        raise JobFailure("analysis_failed")
    try:
        answer = json.loads(data)
    except ValueError:
        raise JobFailure("analysis_failed") from None
    if not isinstance(answer, dict):
        raise JobFailure("analysis_failed")
    music_map, reported = answer.get("map"), answer.get("peak_rss")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not valid_map(music_map, map_version):
        raise JobFailure("analysis_failed")
    own = reported if isinstance(reported, int) and not isinstance(reported, bool) else None
    seen = max(peak, own or 0)
    return Analysis(music_map, seen or None)  # pyright: ignore[reportArgumentType]
