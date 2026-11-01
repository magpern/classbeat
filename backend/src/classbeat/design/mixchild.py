"""The child process that decodes and analyses one Swedebeat mix (ADR 0016 section 3 step 2, T109).

``python -I .../classbeat/design/mixchild.py AUDIO_PATH MAX_SECONDS MAX_RSS_BYTES`` runs in its own
interpreter with a minimal environment (``classbeat.design.mixanalysis`` starts it and limits it to
1.5 GiB of resident memory and 180 s), so a crash, an out-of-memory kill or a hostile file ends only
this process. It holds no database connection, no key and no network: it reads the one file it is
given.

- **Run by path, not as a module**: ``-m classbeat.design.mixchild`` would import the whole
  ``classbeat.design`` package (the job kinds, the database, the HTTP clients) into a process that
  only needs numpy and the library. Its two helpers, ``sandbox`` and ``rss`` (stdlib only), are
  loaded by path from this directory; nothing named ``classbeat`` is imported.
- **Confined before the file is opened** (``classbeat.design.sandbox``): the system call filter (no
  socket, no access to another process) is required and its failure ends the child; the Landlock
  file confinement (the interpreter's files read-only, the job's private directory read-write,
  nothing else: no ``/run/secrets``, no ``/app``) is applied when the kernel has it. The answer says
  which held, and the parent refuses a Linux answer without the filter.
- **Decoding is bounded.** The file is read in blocks of a fixed number of frames (``soundfile``;
  never sized from the header, whose rate and channel count are bounded too), mixed down to mono
  and resampled to the analysis rate on the fly (``soxr``, the resampler ``librosa.load`` uses), so
  memory is the analysis signal and never the decoded file at its own rate. The decoded length is
  counted as it goes: past ``MAX_SECONDS`` (the listed length plus five minutes, never over 120
  minutes) the child stops and fails. A header that already says so fails before any block. MP3
  (what MOWL serves) is decoded by the libsndfile the ``soundfile`` wheel bundles.
- **The analysis is the library's** (``mowl.music.analyze_signal``, ``librosa`` underneath).
- **The answer** is one JSON line on stdout: ``{"map": ..., "peak_rss": bytes or null,
  "sandbox": {"syscalls": bool, "files": bool}}``. Every failure is exit status 3 with nothing on
  stdout; stderr is not read (a path or a file's own words never reach a log).
"""

import importlib.util
import json
import os
import sys
from types import ModuleType
from typing import Any

EXIT_FAILED = 3
BLOCK_FRAMES = 1 << 16  # frames per read, whatever rate the header claims
MAX_RATE = 768_000
MAX_CHANNELS = 8
SYSTEM_READ_ONLY = ("/usr", "/lib", "/lib64", "/bin", "/etc", "/proc", "/sys", "/dev")


def _fail() -> int:
    return EXIT_FAILED


def _jsonable(value: Any) -> Any:
    item = getattr(value, "item", None)  # a numpy scalar
    if callable(item):
        return item()
    raise TypeError("not serialisable")


def sibling(name: str) -> ModuleType:
    """``classbeat/design/<name>.py`` loaded by path, without importing the package."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_mixchild_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_only_paths() -> list[str]:
    """What the confined child may read and execute: the interpreter's prefixes (the virtual
    environment and the base installation, also by their real paths) and the system's own
    directories. Not ``/run`` (the secrets), not ``/app`` beyond the virtual environment."""
    prefixes = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    return sorted(prefixes | {os.path.realpath(p) for p in prefixes} | set(SYSTEM_READ_ONLY))


def confine(sandbox: ModuleType, audio: str) -> dict[str, bool]:
    """Put the sandbox on this process; which parts held. Raises when the filter cannot be put
    on a Linux machine, or when Landlock is there and fails."""
    syscalls = bool(sandbox.deny_syscalls())
    work = os.path.dirname(os.path.realpath(audio))
    files = bool(sandbox.restrict_files(read_only_paths(), [work, "/dev/null"]))
    return {"syscalls": syscalls, "files": files}


def decode(path: str, max_seconds: float, rate: int) -> Any:
    """The file as a mono float32 signal at ``rate``; ``ValueError`` when it is not audio the
    decoder opens, is empty or is longer than ``max_seconds``."""
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
        if source.frames > 0 and source.frames / in_rate > max_seconds:
            raise ValueError("too long")
        resampler = (
            None if in_rate == rate else soxr.ResampleStream(in_rate, rate, 1, dtype="float32")
        )
        parts: list[Any] = []
        frames = 0
        for block in source.blocks(blocksize=BLOCK_FRAMES, dtype="float32", always_2d=True):
            frames += len(block)
            if frames / in_rate > max_seconds:
                raise ValueError("too long")
            mono = block.mean(axis=1, dtype="float32")
            parts.append(mono if resampler is None else resampler.resample_chunk(mono))
        if resampler is not None:
            parts.append(resampler.resample_chunk(np.zeros(0, dtype="float32"), last=True))
    if frames == 0:
        raise ValueError("empty")
    return np.concatenate(parts).astype("float32")


def main(argv: list[str]) -> int:
    try:
        path, max_seconds, max_rss = argv[1], float(argv[2]), int(argv[3])
        if not 0 < max_seconds <= 7200 or max_rss <= 0:
            return _fail()
        sandbox, rss = sibling("sandbox"), sibling("rss")
        confined = confine(sandbox, path)
        _limits()
        _watch(rss, max_rss)
        from mowl import music

        signal = decode(path, max_seconds, music.SR)
        analysis = music.analyze_signal(signal, music.SR)
        out = json.dumps(
            {"map": analysis, "peak_rss": rss.own_peak_rss(), "sandbox": confined},
            default=_jsonable,
            allow_nan=False,
        )
    except BaseException:
        return _fail()
    sys.stdout.write(out + "\n")
    sys.stdout.flush()
    return 0


def _watch(rss: ModuleType, max_rss: int) -> None:
    """A thread that ends this process the moment its resident memory passes ``max_rss``: the
    parent watches too, but the child can see itself where the parent cannot (a launcher process
    in front of the interpreter on Windows) and quicker than the parent's poll."""
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
    """Defence in depth on Linux: no core dump (it would hold the audio), a CPU ceiling a little
    over the parent's 180 s wall clock. The parent enforces the memory and time limits."""
    if sys.platform == "win32":
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (200, 210))
    except (ImportError, ValueError, OSError):
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv))
