"""``songs.fit_policy.keeps`` (ADR 0038 section 7, #1189), table-driven. Pure: no database.

The approved preview is kept (re-timed, no model) only for the same tracks in the same order, each
song's heard length within the tolerance and the total within 5 seconds, both bounds inclusive.
A different order cannot reach the fit end to end (phase 2 ends ``playlist_mismatch`` first when
the analysed ids are not the approved ones in order), so it is checked here."""

import pytest

from classbeat.songs import fit_policy
from classbeat.songs.preview_model import PreviewMusic, PreviewSong
from classbeat.songs.song_list import fingerprint

IDS = ("A" * 22, "B" * 22, "C" * 22)
APPROVED = PreviewMusic(
    fingerprint=fingerprint(IDS),
    songs=[
        PreviewSong(start=0.0, end=240.0),
        PreviewSong(start=231.0, end=471.0),
        PreviewSong(start=462.0, end=702.0),
    ],
)


def spans(*pairs: tuple[float, float]) -> list[PreviewSong]:
    return [PreviewSong(start=a, end=b) for a, b in pairs]


SAME = spans((0.0, 240.0), (231.0, 471.0), (462.0, 702.0))

CASES = [
    ("the same spans", IDS, SAME, True),
    ("one song 2 s longer, at the bound", IDS, spans((0, 242), (233, 473), (464, 704)), True),
    ("one song 2.001 s longer", IDS, spans((0, 242.001), (233, 473), (464, 704)), False),
    ("one song 2 s shorter", IDS, spans((0, 238), (229, 469), (460, 700)), True),
    ("a song out of tolerance in the middle", IDS, spans((0, 240), (231, 474), (465, 705)), False),
    (
        "each within 2 s, the total 5 s later, at the bound",
        IDS,
        spans((0, 241.5), (232.5, 474), (465, 707)),
        True,
    ),
    (
        "each within 2 s, the total 6 s later",
        IDS,
        spans((0, 242), (233, 475), (466, 708)),
        False,
    ),
    ("shifted later as a whole by 6 s", IDS, spans((6, 246), (237, 477), (468, 708)), False),
    ("a different order", (IDS[1], IDS[0], IDS[2]), SAME, False),
    ("another track", (IDS[0], IDS[1], "D" * 22), SAME, False),
    ("one song fewer", IDS[:2], SAME[:2], False),
]


@pytest.mark.parametrize(("case", "ids", "final", "kept"), CASES, ids=[c[0] for c in CASES])
def test_the_fit_keeps_the_preview_only_within_both_bounds(
    case: str, ids: tuple[str, ...], final: list[PreviewSong], kept: bool
) -> None:
    assert fit_policy.keeps(APPROVED, fingerprint(ids), final) is kept, case


def test_the_tolerance_is_the_setting() -> None:
    final = spans((0, 243), (234, 474), (465, 705))  # the first song 3 s longer
    assert not fit_policy.keeps(APPROVED, fingerprint(IDS), final)
    assert fit_policy.keeps(APPROVED, fingerprint(IDS), final, tolerance=3.0)
    assert fit_policy.DEFAULT_TOLERANCE_SECONDS == 2.0
    assert fit_policy.TOTAL_DRIFT_SECONDS == 5.0
