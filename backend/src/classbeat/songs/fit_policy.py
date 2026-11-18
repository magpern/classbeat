"""Whether phase 2 keeps the approved design (ADR 0038 section 7, #1189). Pure: no I/O.

The fit compares like with like: the approved list's ``preview.music`` (the track-id fingerprint
and each song's heard ``start`` and ``end``, from the analysis of exactly these songs in this
order) and the same two things built from the final playlist's analysis by the same function
(``preview_model.song_times``). The preview is kept, and only re-timed with no model, when

- the fingerprints are equal: **the same tracks in the same order** (track ids only, so
  ``refit_draft``'s ``music_changed`` guard does not apply);
- **every song's heard length** (``end - start``) is within ``tolerance`` seconds
  (``CLASSBEAT_FIT_TOLERANCE_SECONDS``, default 2) of the approved one;
- **the last song's ``end``** (the music's total length) is within ``TOTAL_DRIFT_SECONDS`` (5).

Both bounds are inclusive, to the millisecond the spans are stored with. BPM and beat one are not
compared: the same recording analyses the same way. Anything else is a re-design on the approved
preview (``composer_phase2``).
"""

from collections.abc import Sequence
from typing import Final

from classbeat.songs.preview_model import PreviewMusic, PreviewSong

DEFAULT_TOLERANCE_SECONDS: Final = 2.0
TOTAL_DRIFT_SECONDS: Final = 5.0


def keeps(
    approved: PreviewMusic,
    fingerprint: str,
    final: Sequence[PreviewSong],
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """The module docstring's rule: True when the approved preview is only re-timed."""
    if fingerprint != approved.fingerprint or len(final) != len(approved.songs) or not final:
        return False
    for was, now in zip(approved.songs, final, strict=True):
        if _ms(abs((now.end - now.start) - (was.end - was.start))) > tolerance:
            return False
    return _ms(abs(final[-1].end - approved.songs[-1].end)) <= TOTAL_DRIFT_SECONDS


def _ms(seconds: float) -> float:
    return round(seconds, 3)  # the spans' own precision: no float noise at a bound
