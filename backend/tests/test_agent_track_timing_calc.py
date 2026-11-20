"""``track_timing_calc``: each track's class as the map carries it; a YouTube analysis is never
``exact``; a map without classes reads as before (ADR 0040 section 3). Pure."""

from typing import Any

import pytest

from classbeat.agent import track_timing_calc as tt


def a_map() -> dict[str, Any]:
    return {
        "tracks": [{"track": 1}, {"track": 2}, {"track": 3}],
        "songs": [
            {"track": 1, "song": 1, "start": 0.0, "end": 60.0},
            {"track": 1, "song": 2, "start": 60.0, "end": 240.0},
            {"track": 2, "song": 3, "start": 231.0, "end": 471.0},
            {"track": 3, "song": 4, "start": 462.0, "end": 702.0},
        ],
        "events": [
            {"t": 120.0, "kind": "drop", "song": 2},
            {"t": 300.0, "kind": "breakdown", "song": 3},
            {"t": 310.0, "kind": "bar", "song": 3},
        ],
    }


@pytest.mark.parametrize(
    ("timing", "source", "cls"),
    [
        (None, None, "exact"),  # every map before SD-P6
        ("exact", "upload", "exact"),
        ("approximate", "upload", "approximate"),
        ("unusable", "upload", "unusable"),
        ("exact", "youtube", "approximate"),  # a YouTube analysis is never exact
        ("approximate", "youtube", "approximate"),
        ("unusable", "youtube", "unusable"),
        ("exact", None, "approximate"),  # an exact class without its source: not trusted
        ("exact", 7, "approximate"),
        ("bogus", "upload", "unusable"),  # an unknown class is the strictest
        (None, "youtube", "unusable"),
        (3, "upload", "unusable"),
    ],
)
def test_capped(timing: object, source: object, cls: str) -> None:
    assert tt.capped(timing, source) == cls


def test_a_map_without_classes_has_none_and_a_mix_neither() -> None:
    assert tt.classes(a_map()) == {}
    assert tt.classes({"songs": [], "events": []}) == {}


def test_stamp_never_writes_exact_for_youtube_and_leaves_the_input_alone() -> None:
    m = a_map()
    stamped = tt.stamp(m, 1, "exact", "youtube")
    assert stamped["tracks"][1] == {"track": 2, "timing": "approximate", "timing_source": "youtube"}
    assert m == a_map()
    assert tt.classes(stamped) == {1: "approximate"}
    assert tt.classes(tt.stamp(m, 0, "exact", "upload")) == {}


def test_a_hand_edited_exact_youtube_track_reads_approximate() -> None:
    m = a_map()
    m["tracks"][2] |= {"timing": "exact", "timing_source": "youtube"}
    assert tt.classes(m) == {2: "approximate"}


def test_anchors_per_class() -> None:
    m = a_map()
    assert tt.anchors(m, 0, "approximate") == (0.0, 60.0, 120.0, 240.0)  # with its drop
    assert tt.anchors(m, 0, "unusable") == (0.0, 60.0, 240.0)  # sections only
    assert tt.anchors(m, 1, "approximate") == (231.0, 300.0, 471.0)  # not a bar


def test_playing_at_holds_both_tracks_inside_a_crossfade() -> None:
    m = a_map()
    assert tt.playing_at(m, 235.0) == {0, 1}
    assert tt.playing_at(m, 100.0) == {0}
