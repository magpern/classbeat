"""``classbeat.design.track_map_policy`` (ADR 0036 section 4.1, SR C4): which stored maps are a
track map (the cache validates what spotdown's mapping stored), and which references are analysed.
Pure; no database."""

import copy
from typing import Any

import pytest
from mowl import track

from classbeat.design.track_map_policy import MAX_TRACKS, valid_track_map, wanted

V = track.TRACK_VERSION


def good() -> dict[str, Any]:
    return {
        "duration": 200.0,
        "songs": [
            {"start": 0, "end": 200.0, "bpm": 128.0, "first_downbeat": 0.4, "beat1_margin": 3.1}
        ],
        "events": [{"t": 64.0, "kind": "drop", "song": 0}],
        "sections": [{"start": 0.0, "end": 64.0, "energy": 0.0}, {"start": 64.0, "end": 200.0,
                                                                    "energy": None}],
        "track_version": V,
    }  # fmt: skip


def test_the_librarys_own_answer_is_a_track_map() -> None:
    assert valid_track_map(good(), V)


def _set(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    m = copy.deepcopy(good())
    target: Any = m
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return m


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("track_version",), V + 1),
        (("track_version",), True),
        (("track_version",), KeyError),
        (("duration",), 0),
        (("duration",), -1.0),
        (("duration",), 3600.5),
        (("duration",), float("nan")),
        (("duration",), "200"),
        (("songs",), []),
        (("songs",), [good()["songs"][0]] * 2),
        (("songs", 0, "bpm"), 0),
        (("songs", 0, "bpm"), None),
        (("songs", 0, "first_downbeat"), KeyError),
        (("songs", 0, "start"), float("inf")),
        (("events",), None),
        (("events",), [{"t": 1.0}] * 2001),
        (("events", 0, "t"), "1"),
        (("events", 0), 5),
        (("sections",), KeyError),
        (("sections", 0, "energy"), "loud"),
        (("sections", 0, "end"), None),
        (("sections",), [{"start": 0.0, "end": 1.0, "energy": None}] * 501),
    ],
)
def test_anything_else_is_not(path: tuple[Any, ...], value: Any) -> None:
    assert not valid_track_map(_set(path, value), V)


def test_a_map_over_the_tables_bound_or_not_json_is_not() -> None:
    big = good()
    big["note"] = "x" * (1 << 20)
    assert not valid_track_map(big, V)
    odd = good()
    odd["note"] = {1, 2}
    assert not valid_track_map(odd, V)
    assert not valid_track_map([good()], V)
    assert not valid_track_map(None, V)


def test_the_references_are_kept_in_order_once_each() -> None:
    assert wanted(["b", "a", "b", "c-1_2"]) == ["b", "a", "c-1_2"]
    assert wanted([]) == []
    assert len(wanted([f"t{n}" for n in range(MAX_TRACKS)] * 2)) == MAX_TRACKS


@pytest.mark.parametrize(
    "references",
    [["ok", "bad/one"], [""], ["x" * 65], ["a b"], ["../x"], [f"t{n}" for n in range(41)]],
)
def test_a_bad_reference_or_too_many_is_refused(references: list[str]) -> None:
    with pytest.raises(ValueError, match=r"reference|too many"):
        wanted(references)
