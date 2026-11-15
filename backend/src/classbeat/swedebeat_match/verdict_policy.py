"""What a match sends and classbeat's own verdict on the gateway's answer (ADR 0035 section 6).
Pure: no I/O.

- **Sent**: a song's title and artist exactly as the song list holds them, at most ``MAX_TEXT``
  (200) characters each (the list's own bound, under the gateway's 300). A song whose title or
  artist is empty after trimming is **not sent**: it is ``missing`` (one such item would make the
  gateway refuse the whole request with a 422).
- **The verdict** keeps the library's threshold, now classbeat's own constant ``SAME_SONG``
  (0.85): ``found`` only when the gateway says ``found`` **and** its score is at least 0.85, with
  a usable track; ``unsure`` when the gateway says ``found`` below that, or ``unsure``, with a
  usable track; ``missing`` when it says ``missing`` or the item has no usable track. An
  ``unsure`` is never counted as found. The gateway's own ``found`` cut (0.78) is looser. A
  ``found`` or ``unsure`` keeps the track's catalogue ``reference`` and SwedeBeat ``bpm`` (ADR 0036
  section 3.4; the parser already made a track without a valid reference unusable, so missing).
- **"All missing" is suspect** (a SwedeBeat outage looks like it): every sent track ``missing``
  with at least ``ALL_MISSING_MIN`` (3) distinct tracks sent. The job asks once more.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from classbeat.swedebeat_gateway.match_policy import MAX_TEXT, Item

SAME_SONG: Final = 0.85
ALL_MISSING_MIN: Final = 3

Status = Literal["found", "unsure", "missing"]


@dataclass(frozen=True, slots=True)
class Verdict:
    """One song's verdict and, for ``found`` and ``unsure``, the catalogue track as kept."""

    status: Status
    title: str | None = None
    artist: str | None = None
    duration: int | None = None  # whole seconds from 1, or None
    reference: str | None = None  # the catalogue's, for the download; never a model's input
    bpm: float | None = None
    # The gateway's score (the studio's swap record, ADR 0036 section 3.3); not compared.
    score: float | None = field(default=None, compare=False)


def sent_pair(title: str, artist: str) -> tuple[str, str] | None:
    """The ``(title, artist)`` a match sends for a song, or ``None`` when it is not sent."""
    sent_title, sent_artist = title[:MAX_TEXT], artist[:MAX_TEXT]
    if not sent_title.strip() or not sent_artist.strip():
        return None
    return sent_title, sent_artist


def verdict(item: Item | None) -> Verdict:
    """classbeat's verdict on one answer item (``None``: the song was not sent)."""
    if item is None or item.status == "missing" or item.track is None:
        return Verdict("missing")
    track = item.track
    # A length of 0 is no song's length: the revision keeps 1 to 3600 s, or nothing.
    duration = track.duration if track.duration else None
    sure = item.status == "found" and item.score is not None and item.score >= SAME_SONG
    status: Status = "found" if sure else "unsure"
    return Verdict(
        status, track.title, track.artist, duration, track.reference, track.bpm, item.score
    )


def all_missing(items: Sequence[Item]) -> bool:
    """The answer is suspect: every item ``missing`` with at least ``ALL_MISSING_MIN`` sent."""
    return len(items) >= ALL_MISSING_MIN and all(item.status == "missing" for item in items)
