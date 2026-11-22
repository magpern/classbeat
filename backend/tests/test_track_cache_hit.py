"""``track_cache._hit``, the one place a stored map is read back (``cached_in`` and ``cached`` both
go through it; ADR 0036 section 5, #1437 review N1). A row is a hit only when it is a spotdown row
whose map still passes ``valid_track_map`` and the caller's ``Accept`` rule takes it; anything else
is no hit, so the page answers ``music_changed`` from the missing analysis, not from the ``except``
that stands behind it. Pure: rows are stand-ins, no database, no network.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from mowl import track

from classbeat.design import track_cache

SHA = bytes(range(32))


def good_map() -> dict[str, Any]:
    return {
        "duration": 240.0,
        "songs": [{"start": 0, "end": 240.0, "bpm": 120.0, "first_downbeat": 0.5}],
        "events": [],
        "sections": [],
        "track_version": track.TRACK_VERSION,
    }


def row(music_map: Any = None, analyser: str = "spotdown") -> SimpleNamespace:
    return SimpleNamespace(
        reference="trk-1",
        analyser=analyser,
        provenance={},
        sha256=SHA,
        map=good_map() if music_map is None else music_map,
    )


def take_all(_: Any) -> bool:
    return True


def test_a_readable_spotdown_row_the_rule_takes_is_a_hit() -> None:
    hit = track_cache._hit(row(), take_all)  # pyright: ignore[reportPrivateUsage]
    assert hit is not None
    assert (hit.reference, hit.sha256, hit.cached) == ("trk-1", SHA.hex(), True)


def test_a_stored_map_without_its_bpm_is_no_hit() -> None:
    broken = good_map()
    del broken["songs"][0]["bpm"]
    assert track_cache._hit(row(broken), take_all) is None  # pyright: ignore[reportPrivateUsage]


def test_a_map_of_another_track_version_is_no_hit() -> None:
    old = {**good_map(), "track_version": track.TRACK_VERSION - 1}
    assert track_cache._hit(row(old), take_all) is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("other", ["librosa", "swedebeat"])
def test_a_row_of_another_analyser_is_no_hit(other: str) -> None:
    assert track_cache._hit(row(analyser=other), take_all) is None  # pyright: ignore[reportPrivateUsage]


def test_a_row_the_rule_refuses_is_no_hit() -> None:
    assert track_cache._hit(row(), lambda _: False) is None  # pyright: ignore[reportPrivateUsage]
