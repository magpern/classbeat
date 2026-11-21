"""The child process that renders one SwedeBeat mix (ADR 0036 section 6, Mix; issue #1092).

``python -I .../classbeat/studio/renderchild.py PLAN_PATH MAX_RSS_BYTES`` runs in its own
interpreter with a minimal environment (``classbeat.studio.render`` starts it under the limits of
``design.mixanalysis``: 1 GiB resident memory, a wall-clock cap), so a crash, an out-of-memory
kill or a hostile file ends only this process. It holds no database connection, no key and no
network: it reads the plan and the tracks the plan names, and writes the one output file.

- **Run by path, not as a module** (as ``design/mixchild.py``): nothing named ``classbeat`` is
  imported. ``render_policy`` and ``render_curves`` are loaded by path from this directory,
  ``sandbox`` and ``rss`` from ``design/``.
- **Confined before a file is opened** (``design.sandbox``): the system call filter is required
  (its failure ends the child), Landlock is applied when the kernel has it: the interpreter's
  files read-only, the plan's directory (the job's private directory) read-write, nothing else.
  Every path in the plan must lie inside that directory.
- **Decoding is bounded**: each track is read in blocks of ``BLOCK_FRAMES`` frames (``soundfile``;
  rate and channel count bounded), brought to 44100 Hz stereo (``soxr``), cut, faded with equal
  power and added where the plan says. Reading stops at ``src_end``; a file that ends before it
  fails. Memory is the overlap or gap between tracks (at most 30 s, by the plan's rules), never a
  decoded track. A bass swap (#1330) is ``render_curves``'s.
- **Streamed output**: the finished samples before the next track starts go to a 320 kb/s CBR MP3
  (``soundfile``, libsndfile's encoder) at once. The render is deterministic: the same plan and
  files give the same bytes.
- **The answer** is one JSON line: ``{"sha256", "frames", "duration_sec", "peak_rss", "sandbox"}``,
  ``frames`` being what the written file decodes to. Every failure is exit status 3 with nothing on
  stdout; stderr is not read.
"""

import hashlib
import importlib.util
import json
import os
import sys
from types import ModuleType
from typing import Any

EXIT_FAILED = 3
BLOCK_FRAMES = 1 << 16
MAX_RATE = 768_000
MAX_CHANNELS = 8
FILE_LIMIT_BYTES = 410 * 1000 * 1000  # 400 MB (render_policy.MAX_MIX_BYTES) and a margin
SYSTEM_READ_ONLY = ("/usr", "/lib", "/lib64", "/bin", "/etc", "/proc", "/sys", "/dev")
HERE = os.path.dirname(os.path.abspath(__file__))
DESIGN = os.path.join(os.path.dirname(HERE), "design")


def _fail() -> int:
    return EXIT_FAILED


def load(directory: str, name: str) -> ModuleType:
    """``<directory>/<name>.py`` loaded by path, without importing the package."""
    spec = importlib.util.spec_from_file_location(
        f"_renderchild_{name}", os.path.join(directory, f"{name}.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_only_paths() -> list[str]:
    prefixes = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    return sorted(prefixes | {os.path.realpath(p) for p in prefixes} | set(SYSTEM_READ_ONLY))


def inside(work: str, path: str) -> str:
    """The real path of ``path``, which must lie in directory ``work`` (``ValueError`` if not)."""
    real = os.path.realpath(path)
    if os.path.commonpath([work, real]) != work:
        raise ValueError("outside the work directory")
    return real


def track_blocks(path: str, src_start: int, src_end: int, rate: int) -> Any:
    """The samples ``[src_start, src_end)`` of the file at ``rate`` Hz as float32 stereo blocks;
    ``ValueError`` when it is not audio or ends before ``src_end``."""
    import numpy as np
    import soundfile
    import soxr

    try:
        source = soundfile.SoundFile(path)
    except Exception as exc:  # the decoder decides what is audio
        raise ValueError("not audio") from exc
    with source:
        in_rate = source.samplerate
        if not 0 < in_rate <= MAX_RATE or not 0 < source.channels <= MAX_CHANNELS:
            raise ValueError("not audio")
        resampler = (
            None if in_rate == rate else soxr.ResampleStream(in_rate, rate, 2, dtype="float32")
        )
        position = 0  # output-rate samples produced so far

        def cut(chunk: Any) -> Any:
            nonlocal position
            lo, hi = max(src_start - position, 0), min(src_end - position, len(chunk))
            position += len(chunk)
            return chunk[lo:hi] if hi > lo else None

        for block in source.blocks(blocksize=BLOCK_FRAMES, dtype="float32", always_2d=True):
            stereo = np.repeat(block, 2, axis=1) if block.shape[1] == 1 else block[:, :2]
            piece = cut(stereo if resampler is None else resampler.resample_chunk(stereo))
            if piece is not None:
                yield piece
            if position >= src_end:
                return
        if resampler is not None:
            tail = resampler.resample_chunk(np.zeros((0, 2), dtype="float32"), last=True)
            piece = cut(tail)
            if piece is not None:
                yield piece
        if position < src_end:
            raise ValueError("the file ends before src_end")


def gains(track: dict[str, Any], start: int, count: int) -> Any:
    """Equal-power gains for ``count`` samples from ``start`` (relative to the cut) of ``track``."""
    import numpy as np

    length = track["src_end"] - track["src_start"]
    index = np.arange(start, start + count, dtype="float64")
    gain = np.ones(count, dtype="float64")
    fade_in, fade_out = track["fade_in"], track["fade_out"]
    if fade_in:
        part = index < fade_in
        gain[part] *= np.sin(0.5 * np.pi * index[part] / fade_in)
    if fade_out:
        part = index >= length - fade_out
        gain[part] *= np.cos(0.5 * np.pi * (index[part] - (length - fade_out)) / fade_out)
    return gain.astype("float32")[:, None]


class Mixer:
    """Output samples from ``base`` on that later tracks may still add to; ``flush`` hands out
    (and forgets) everything before a position nothing will write to again."""

    def __init__(self) -> None:
        import numpy as np

        self.np = np
        self.base = 0
        self.pending = np.zeros((0, 2), dtype="float32")

    def add(self, position: int, block: Any) -> None:
        offset = position - self.base
        end = offset + len(block)
        if end > len(self.pending):
            grown = self.np.zeros((end, 2), dtype="float32")
            grown[: len(self.pending)] = self.pending
            self.pending = grown
        self.pending[offset:end] += block

    def flush(self, upto: int) -> Any:
        count = upto - self.base
        if count <= 0:
            return None
        if count > len(self.pending):
            grown = self.np.zeros((count, 2), dtype="float32")
            grown[: len(self.pending)] = self.pending
            self.pending = grown
        out = self.np.clip(self.pending[:count], -1.0, 1.0)
        self.pending = self.pending[count:].copy()
        self.base = upto
        return out


def render(
    tracks: list[dict[str, Any]], writer: Any, policy: ModuleType, work: str, curves: ModuleType
) -> int:
    """Stream the mix into ``writer`` (a soundfile output); the number of frames written. Each
    track's blocks take their gains through ``render_curves.shaped`` (#1330's transition curves)."""
    mixer = Mixer()
    written = 0
    for i, track in enumerate(tracks):
        after = tracks[i + 1] if i + 1 < len(tracks) else None
        following = None if after is None else after["dst_start"]
        offset = 0
        blocks = track_blocks(
            inside(work, track["path"]), track["src_start"], track["src_end"], policy.RATE
        )
        for block in curves.shaped(blocks, track, after, gains, policy.SWAP_RAMP_FRAMES):
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


def digest(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            sha.update(chunk)
    return sha.hexdigest()


def decoded_frames(path: str) -> int:
    """What the finished file decodes to, counted block by block."""
    import soundfile

    with soundfile.SoundFile(path) as source:
        return sum(len(b) for b in source.blocks(blocksize=BLOCK_FRAMES, dtype="float32"))


def main(argv: list[str]) -> int:
    try:
        plan_path, max_rss = argv[1], int(argv[2])
        if max_rss <= 0:
            return _fail()
        policy, curves = load(HERE, "render_policy"), load(HERE, "render_curves")
        sandbox, rss = load(DESIGN, "sandbox"), load(DESIGN, "rss")
        work = os.path.dirname(os.path.realpath(plan_path))
        syscalls = bool(sandbox.deny_syscalls())
        files = bool(sandbox.restrict_files(read_only_paths(), [work, "/dev/null"]))
        _limits()
        _watch(rss, max_rss)
        with open(plan_path, encoding="utf-8") as handle:
            plan = json.load(handle)
        tracks = policy.validate_plan(plan)
        output = inside(work, plan["output"])
        if any(inside(work, t["path"]) == output for t in tracks):
            raise ValueError("a track is the output")
        import soundfile

        with soundfile.SoundFile(
            output,
            "w",
            samplerate=policy.RATE,
            channels=2,
            format="MP3",
            subtype="MPEG_LAYER_III",
            bitrate_mode="CONSTANT",
            compression_level=0.0,
        ) as writer:
            render(tracks, writer, policy, work, curves)
        frames = decoded_frames(output)
        out = json.dumps(
            {
                "sha256": digest(output),
                "frames": frames,
                "duration_sec": frames / policy.RATE,
                "peak_rss": rss.own_peak_rss(),
                "sandbox": {"syscalls": syscalls, "files": files},
            },
            allow_nan=False,
        )
    except BaseException:
        return _fail()
    sys.stdout.write(out + "\n")
    sys.stdout.flush()
    return 0


def _watch(rss: ModuleType, max_rss: int) -> None:
    """A thread that ends this process the moment its resident memory passes ``max_rss``."""
    import threading
    import time

    def watch() -> None:
        pid = os.getpid()
        while True:
            time.sleep(0.05)
            used = rss.process_rss(pid)
            if used is not None and used > max_rss:
                os._exit(EXIT_FAILED)

    threading.Thread(target=watch, daemon=True).start()


def _limits() -> None:
    """No core dump (it would hold the audio), a CPU ceiling over the parent's wall clock and a
    file size ceiling of the output cap plus a margin."""
    if sys.platform == "win32":
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (1500, 1510))
        size = FILE_LIMIT_BYTES  # a file never grows past the gateway's cap (and the plan)
        resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    except (ImportError, ValueError, OSError):
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv))
