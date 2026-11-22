"""SR C5 (the C4 review's carry-over): the map size check measures what the table's check measures,
``octet_length(map::text)`` of the jsonb, not compact JSON (``classbeat.jsonb_text_calc``)."""

import json
from typing import Any

import pytest
from mowl import track
from sqlalchemy import create_engine, text

from classbeat.design import mix_map_shape_policy, track_map_policy
from classbeat.jsonb_text_calc import jsonb_text_size

VALUES: list[Any] = [
    None,
    True,
    False,
    0,
    -12,
    1.5,
    0.1,
    1e-07,
    2.5e-05,
    1.5e20,
    123.456,
    -0.25,
    "",
    "plain",
    'quote " and \\ slash /',
    "tab\tnew\nline\r\x01\x1f",
    "ä ö 🎵",
    [],
    {},
    [1, [2, 3], {"a": None}],
    {"b": 1, "a": [1.0, 2e-06], "ü": {"x": "y"}},
]


@pytest.mark.parametrize("value", VALUES)
def test_the_measure_is_postgres_own(value: Any, database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as db:
            measured = db.execute(
                text("SELECT octet_length(CAST(:v AS jsonb)::text)"), {"v": json.dumps(value)}
            ).scalar_one()
    finally:
        engine.dispose()
    assert jsonb_text_size(value) == measured


def test_a_small_exponent_counts_as_jsonb_prints_it() -> None:
    assert len(json.dumps(1e-07)) == 5
    assert jsonb_text_size(1e-07) == len("0.0000001")


def test_nan_and_non_json_values_are_refused() -> None:
    with pytest.raises(ValueError, match="NaN"):
        jsonb_text_size(float("nan"))
    with pytest.raises(TypeError):
        jsonb_text_size({1: "a"})
    with pytest.raises(TypeError):
        jsonb_text_size(object())


def _track_map(sections: int) -> dict[str, Any]:
    return {
        "duration": 240.0,
        "songs": [{"start": 0, "end": 240.0, "bpm": 120.0, "first_downbeat": 0.5}],
        "events": [],
        "sections": [{"start": 0.0, "end": 1.0, "energy": 1e-07} for _ in range(sections)],
        "track_version": track.TRACK_VERSION,
    }


def test_a_map_that_fits_compactly_but_not_as_jsonb_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _track_map(20)
    compact = len(json.dumps(m, separators=(",", ":")))
    as_table = jsonb_text_size(m)
    assert compact < as_table
    monkeypatch.setattr(track_map_policy, "MAX_MAP_BYTES", compact)
    assert not track_map_policy.valid_track_map(m, track.TRACK_VERSION)
    monkeypatch.setattr(track_map_policy, "MAX_MAP_BYTES", as_table)
    assert track_map_policy.valid_track_map(m, track.TRACK_VERSION)
    mix = {"version": 13, "duration": 240.0, "songs": m["songs"], "events": [], "x": m["sections"]}
    monkeypatch.setattr(mix_map_shape_policy, "MAX_MAP_BYTES", jsonb_text_size(mix) - 1)
    assert not mix_map_shape_policy.valid_map(mix, 13)
    monkeypatch.setattr(mix_map_shape_policy, "MAX_MAP_BYTES", jsonb_text_size(mix))
    assert mix_map_shape_policy.valid_map(mix, 13)
