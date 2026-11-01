"""The child process that decodes and analyses one Swedebeat mix (ADR 0016 section 3 step 2).

``python -I -m classbeat.design.mixchild AUDIO_PATH MAX_SECONDS MAX_RSS_BYTES`` runs in its own
interpreter with a
minimal environment (``classbeat.design.mixanalysis`` starts it and limits it to 1.5 GiB of resident
memory and 180 s), so a crash, an out-of-memory kill or a hostile file ends only this process. It
holds no database connection, no key and no network: it reads the one file it is given.

- **Decoding is bounded.** The file is read block by block (``soundfile``), mixed down to mono and
  resampled to the analysis rate on the fly (``soxr``, the resampler ``librosa.load`` uses), so
  memory is the analysis signal and never the decoded file at its own rate. The decoded length is
  counted as it goes: past ``MAX_SECONDS`` (the listed length plus five minutes, never over 120
  minutes) the child stops and fails. A header that already says so fails before any block.
- **The analysis is the library's** (``mowl.music.analyze_signal``, ``librosa`` underneath).
- **The answer** is one JSON line on stdout: ``{"map": ..., "peak_rss": bytes or null}``. Every
  failure is exit status 3 with nothing on stdout; stderr is not read (a path or a file's own
  words never reach a log).
"""

import json
import sys
from typing import Any

EXIT_FAILED = 3
BLOCK_SECONDS = 10


def _fail() -> int:
    return EXIT_FAILED


def _jsonable(value: Any) -> Any:
    item = getattr(value, "item", None)  # a numpy scalar
    if callable(item):
        return item()
    raise TypeError("not serialisable")


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
        if in_rate <= 0 or source.channels <= 0:
            raise ValueError("not audio")
        if source.frames > 0 and source.frames / in_rate > max_seconds:
            raise ValueError("too long")
        resampler = (
            None if in_rate == rate else soxr.ResampleStream(in_rate, rate, 1, dtype="float32")
        )
        parts: list[Any] = []
        frames = 0
        for block in source.blocks(
            blocksize=in_rate * BLOCK_SECONDS, dtype="float32", always_2d=True
        ):
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
        _limits()
        _watch(max_rss)
        from mowl import music

        from classbeat.design.rss import own_peak_rss

        signal = decode(path, max_seconds, music.SR)
        analysis = music.analyze_signal(signal, music.SR)
        out = json.dumps(
            {"map": analysis, "peak_rss": own_peak_rss()}, default=_jsonable, allow_nan=False
        )
    except BaseException:
        return _fail()
    sys.stdout.write(out + "\n")
    sys.stdout.flush()
    return 0


def _watch(max_rss: int) -> None:
    """A thread that ends this process the moment its resident memory passes ``max_rss``: the
    parent watches too, but the child can see itself where the parent cannot (a launcher process
    in front of the interpreter on Windows) and quicker than the parent's poll."""
    import os
    import threading
    import time

    from classbeat.design.rss import process_rss

    def watch() -> None:
        pid = os.getpid()
        while True:
            time.sleep(0.05)
            used = process_rss(pid)
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
