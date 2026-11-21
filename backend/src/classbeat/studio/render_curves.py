"""The transition curves of the mix render (#1330; ADR 0036 section 6, Mix). Stdlib and numpy only,
no I/O: ``renderchild`` loads this file by path, as ``render_policy`` (it never imports the
``classbeat`` package). ``render_policy`` decides which curves a plan may name.

``shaped`` turns one track's decoded blocks into the blocks the mixer adds:

- **No bass swap at either end** (``equal_power``, today's render): each block times its
  equal-power gains, the very arithmetic the child did before this module, so the output is the
  same sample for sample.
- **A bass swap** into the track (its ``curve``) or out of it (the next track's): the part of the
  track around that fade (the fade plus ``HALF`` samples of context on each side, bounded by the
  cut) is held, split into a low band (``lowpass``) and the rest, and shaped. The rest takes the
  equal-power fade, as today. The low band is held back on the incoming track and kept on the
  outgoing one until the swap point (a downbeat, ``swap`` samples into the overlap), then handed
  over in the ``ramp`` samples before it with equal-power gains. Outside the fades every sample is
  the source times its gain, and where both band gains are 1 the two bands add up to the source
  again, so nothing jumps where a fade ends. Memory is one fade (at most 30 s) and its context.

The low band is a linear-phase FIR (a Blackman-windowed sinc, ``TAPS`` taps, ``CROSSOVER_HZ``),
applied centred (no delay) through numpy's FFT in blocks; outside the held part it reads zeros,
where the gains are 0 anyway (the cut's own start and end). Deterministic: the same plan and files
give the same samples on one machine; numpy's FFT is not promised bit-identical across CPU types, so
a retry that lands on another one may differ and end as ``render_not_reproducible`` (nothing sent).
"""

from collections.abc import Callable, Iterable, Iterator
from typing import Any, Final

import numpy as np

RATE: Final = 44100  # ``render_policy.RATE``
CROSSOVER_HZ: Final = 150.0  # spotdown's low band is under 150 Hz
TAPS: Final = 4097
HALF: Final = TAPS // 2
CHUNK: Final = 1 << 16
NFFT: Final = 1 << 17  # at least CHUNK + TAPS - 1

Gains = Callable[[dict[str, Any], int, int], Any]
Region = tuple[int, int, int, bool]  # [lo, hi) of the cut, the swap point in it, incoming


def kernel() -> Any:
    """The low-pass taps, unit gain at 0 Hz."""
    n = np.arange(TAPS, dtype="float64") - HALF
    cutoff = CROSSOVER_HZ / RATE
    taps = 2.0 * cutoff * np.sinc(2.0 * cutoff * n) * np.blackman(TAPS)
    return taps / taps.sum()


def lowpass(x: Any) -> Any:
    """The low band of ``x`` (frames by channels), centred, as float64; zeros outside ``x``."""
    response = np.fft.rfft(kernel(), NFFT)[:, None]
    count = len(x)
    full = np.zeros((count + TAPS - 1, x.shape[1]), dtype="float64")
    for at in range(0, count, CHUNK):
        part = x[at : at + CHUNK].astype("float64")
        size = len(part) + TAPS - 1
        full[at : at + size] += np.fft.irfft(
            np.fft.rfft(part, NFFT, axis=0) * response, NFFT, axis=0
        )[:size]
    return full[HALF : HALF + count]


def low_gain(index: Any, swap: int, ramp: int, incoming: bool) -> Any:
    """The low band's gain at ``index`` (samples into the fade): the handover ends on ``swap``."""
    t = np.clip((index - (swap - ramp)) / ramp, 0.0, 1.0)
    return np.sin(0.5 * np.pi * t) if incoming else np.cos(0.5 * np.pi * t)


def regions(track: dict[str, Any], following: dict[str, Any] | None) -> list[Region]:
    length = track["src_end"] - track["src_start"]
    found: list[Region] = []
    if track.get("curve") == "bass_swap":
        found.append((0, track["fade_in"], track["swap"], True))
    if following is not None and following.get("curve") == "bass_swap":
        found.append((length - track["fade_out"], length, following["swap"], False))
    if len(found) == 2 and found[0][1] > found[1][0]:
        # ``_band`` writes each region over the last: they must not meet (``validate_plan``'s cut
        # check, fade_in + fade_out <= length, already guarantees it for a checked plan)
        raise ValueError("swap regions overlap")
    return found


def _spans(found: list[Region], length: int) -> list[tuple[int, int]]:
    """The held parts: each region and its context, joined where they meet."""
    spans: list[tuple[int, int]] = []
    for lo, hi, _, _ in sorted(found):
        a, b = max(0, lo - HALF), min(length, hi + HALF)
        if spans and a <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(b, spans[-1][1]))
        else:
            spans.append((a, b))
    return spans


def _band(
    x: Any, start: int, track: dict[str, Any], found: list[Region], gains: Gains, ramp: int
) -> Any:
    """Held samples ``x`` from ``start`` (in the cut), shaped."""
    low = lowpass(x)
    gain = gains(track, start, len(x)).astype("float64")
    out = x.astype("float64") * gain
    for lo, hi, swap, incoming in found:
        a, b = max(lo, start), min(hi, start + len(x))
        if a >= b:
            continue
        part = slice(a - start, b - start)
        held = low_gain(np.arange(a - lo, b - lo, dtype="float64"), swap, ramp, incoming)
        rest = x[part].astype("float64") - low[part]
        out[part] = held[:, None] * low[part] + gain[part] * rest
    return out.astype("float32")


def shaped(
    blocks: Iterable[Any],
    track: dict[str, Any],
    following: dict[str, Any] | None,
    gains: Gains,
    ramp: int,
) -> Iterator[Any]:
    """The track's blocks with their gains (see the module docstring), in order."""
    found = regions(track, following)
    offset = 0
    if not found:
        for block in blocks:
            yield block * gains(track, offset, len(block))
            offset += len(block)
        return
    spans = _spans(found, track["src_end"] - track["src_start"])
    held: list[Any] = []
    for block in blocks:
        at = 0
        while at < len(block):
            pos = offset + at
            while spans and spans[0][1] <= pos:
                spans.pop(0)
            if spans and spans[0][0] <= pos:
                take = min(len(block) - at, spans[0][1] - pos)
                held.append(block[at : at + take])
                if pos + take == spans[0][1]:
                    start = spans[0][1] - sum(len(h) for h in held)
                    yield _band(np.concatenate(held), start, track, found, gains, ramp)
                    held = []
            else:
                stop = spans[0][0] if spans else pos + len(block) - at
                take = min(len(block) - at, stop - pos)
                yield block[at : at + take] * gains(track, pos, take)
            at += take
        offset += len(block)
    if held:  # the stream ended inside a held part (the reader fails a short file first)
        start = offset - sum(len(h) for h in held)
        yield _band(np.concatenate(held), start, track, found, gains, ramp)
