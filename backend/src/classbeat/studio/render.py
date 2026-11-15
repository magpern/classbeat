"""Run the render of one SwedeBeat mix in a limited child process (ADR 0036 section 6, Mix).

The tracks are audio of external origin, so the decode, the cut and the encode never run in the
worker's own process: ``renderchild.py`` does, started by path in isolated mode, and a crash, an
out-of-memory kill or a decoder bug ends only the child (``RenderFailed``).

- **Limits** are those of ``design.mixanalysis``: the child confines itself before it opens a file
  (system call filter required on Linux, Landlock when the kernel has it), the parent watches its
  resident memory (``MAX_RSS_BYTES``, 1 GiB) and a wall clock (``RENDER_SECONDS``), starts it in a
  process group it kills whole, makes itself non-dumpable first, and refuses a Linux answer that
  does not say the filter held. The child gets a minimal environment, no stdin, and its stderr is
  discarded.
- **The directory** is private (``design.mixfetch.private_directory``): ``rendered_mix`` makes it,
  lets the caller put the downloaded tracks in it and name the plan, and removes it in a
  ``finally`` once the caller is done with the output file.
- **The answer** is checked: the output exists, is at most the gateway's 400 MB, has the reported
  sha256, and its decoded length is the composed one within 100 ms (``render_policy.length_ok``).
"""

import asyncio
import contextlib
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from classbeat.design.mixfetch import private_directory
from classbeat.design.rss import process_rss
from classbeat.design.sandbox import LINUX, SandboxError, make_undumpable
from classbeat.studio import render_policy

MAX_RSS_BYTES: Final = 1024**3
RENDER_SECONDS: Final = 1200.0
POLL_SECONDS: Final = 0.1
MAX_OUTPUT_BYTES: Final = 4096  # the child's one line
MAX_MIX_BYTES: Final = 400 * 1000 * 1000  # the gateway's cap
CHILD: Final = Path(__file__).with_name("renderchild.py")
PLAN_NAME: Final = "plan.json"
OUTPUT_NAME: Final = "mix.mp3"

Prepare = Callable[[Path], Awaitable[Sequence[dict[str, Any]]]]


class RenderFailed(Exception):
    """The render did not produce a file that matches its plan. Nothing is published."""


@dataclass(frozen=True, slots=True)
class Rendered:
    path: Path
    sha256: str
    frames: int
    duration_sec: float
    peak_rss: int | None


def child_argv(plan: Path, max_rss: int) -> list[str]:
    """The child's command line: by path in isolated mode, never ``-m``."""
    return [sys.executable, "-I", str(CHILD), str(plan), str(max_rss)]


def child_environment(work: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": str(work),
        "TEMP": str(work),
        "TMP": str(work),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    if os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def _kill(process: "subprocess.Popen[bytes]") -> None:
    with contextlib.suppress(OSError):
        if sys.platform != "win32":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


def _read(process: "subprocess.Popen[bytes]", out: list[bytes]) -> None:
    if process.stdout is None:  # pragma: no cover - the pipe was asked for
        return
    out.append(process.stdout.read(MAX_OUTPUT_BYTES + 1))
    process.wait()


def _sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            sha.update(chunk)
    return sha.hexdigest()


async def render_files(
    work: Path,
    tracks: Sequence[dict[str, Any]],
    *,
    timeout_seconds: float = RENDER_SECONDS,
    max_rss: int = MAX_RSS_BYTES,
    argv: Callable[[Path, int], Sequence[str]] | None = None,
) -> Rendered:
    """Render ``tracks`` (plan dicts whose ``path`` lies in ``work``) to ``work/mix.mp3`` in a
    limited child. Raises ``RenderFailed`` for anything the plan, the child or its limits end."""
    try:
        checked = render_policy.validate_plan(
            {"output": str(work / OUTPUT_NAME), "tracks": [dict(t) for t in tracks]}
        )
        plan = work / PLAN_NAME
        plan.write_text(json.dumps({"output": str(work / OUTPUT_NAME), "tracks": checked}))
        make_undumpable()
        process = subprocess.Popen(  # noqa: S603, ASYNC220 - ours; the call returns at once
            list((argv or child_argv)(plan, max_rss)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_environment(work),
            cwd=work,
            close_fds=True,
            start_new_session=sys.platform != "win32",
        )
    except (OSError, ValueError, SandboxError):
        raise RenderFailed("not started") from None
    output: list[bytes] = []
    reader = threading.Thread(target=_read, args=(process, output), daemon=True)
    reader.start()
    started, peak = time.monotonic(), 0
    try:
        while reader.is_alive():
            await asyncio.sleep(POLL_SECONDS)
            used = process_rss(process.pid)
            if used is not None:
                peak = max(peak, used)
                if used > max_rss:
                    raise RenderFailed("memory")
            if time.monotonic() - started > timeout_seconds:
                raise RenderFailed("time")
    finally:
        if process.poll() is None or sys.platform != "win32":
            _kill(process)
        reader.join(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
    data = output[0] if output else b""
    if process.returncode != 0 or len(data) > MAX_OUTPUT_BYTES:
        raise RenderFailed("child failed")
    return _checked(work / OUTPUT_NAME, data, peak, render_policy.composed_seconds(checked))


def _checked(path: Path, data: bytes, peak: int, composed_sec: float) -> Rendered:
    try:
        answer: Any = json.loads(data)
        sandbox, sha256 = answer["sandbox"], answer["sha256"]
        frames, duration, reported = answer["frames"], answer["duration_sec"], answer["peak_rss"]
        if LINUX and sandbox["syscalls"] is not True:
            raise RenderFailed("not confined")
        sane = (
            isinstance(frames, int)
            and not isinstance(frames, bool)
            and isinstance(duration, int | float)
            and not isinstance(duration, bool)
            and math.isfinite(duration)
            and isinstance(sha256, str)
            and 0 < path.stat().st_size <= MAX_MIX_BYTES
        )
        if not sane or not render_policy.length_ok(duration, composed_sec):
            raise RenderFailed("length")
        if _sha256(path) != sha256:
            raise RenderFailed("the file is not the one reported")
    except (ValueError, KeyError, TypeError, OSError):
        raise RenderFailed("bad answer") from None
    own = reported if isinstance(reported, int) and not isinstance(reported, bool) else 0
    return Rendered(path, sha256, frames, float(duration), max(peak, own) or None)


@contextlib.asynccontextmanager
async def rendered_mix(
    prepare: Prepare,
    *,
    root: Path | None = None,
    **limits: Any,
) -> AsyncIterator[Rendered]:
    """A private directory in which ``prepare(directory)`` puts the tracks (downloads them) and
    returns the plan's tracks; the render's result while the caller uses ``Rendered.path``. The
    directory, with the tracks and the output, is removed on the way out, whatever happened."""
    with private_directory(root) as work:
        yield await render_files(work, await prepare(work), **limits)
