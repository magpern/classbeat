"""The normal form of a workout (``classbeat.mowl_state``, ADR 0007 sections 6 to 8): what the
library writes for a spec equals what the fake MOWL reads back, and any difference in a compared
field, a public program or an unreadable one is no match. Pure; no database, no network.
"""

import copy
import time
from typing import Any

import pytest
from mowl import workout

from classbeat import egress, mowl_state
from classbeat.connections import Credentials
from classbeat.saving import kind as save_kind
from optools import TOKEN_A
from savetools import DRAFT, FakeMowlPrograms, spec_of

SPEC = spec_of(DRAFT)
PLAYLIST = 1001


def read_back(spec: dict[str, Any], playlist_id: int = PLAYLIST, **kwargs: Any) -> dict[str, Any]:
    fake = FakeMowlPrograms()
    program_id = fake.plant(TOKEN_A, spec, playlist_id=playlist_id, **kwargs)
    return fake.read_program(fake.programs[program_id])


def test_the_library_ids_are_the_ones_compared() -> None:
    assert (mowl_state.STANDING_ID, mowl_state.ACC_ID) == (2, 4)
    assert workout.POSITION == {False: 1, True: 2}
    assert workout.CYCLE == {False: 1, True: 4}


def test_a_read_back_of_what_was_written_matches() -> None:
    program = read_back(SPEC)
    assert mowl_state.matches(SPEC, program, playlist_id=PLAYLIST)
    assert mowl_state.normalized_program(program) == mowl_state.normalized_spec(SPEC, PLAYLIST)
    digest = mowl_state.state_hash(mowl_state.normalized_program(program))
    assert len(digest) == 32
    assert digest == mowl_state.state_hash(mowl_state.normalized_spec(SPEC, PLAYLIST))


def test_ftp_tests_games_and_names_without_ids_are_compared() -> None:
    spec = copy.deepcopy(SPEC)
    spec["segments"][1]["intervals"].append({"sec": 300, "zone": 4, "rpm": [80, 90]})
    spec["segments"][1]["intervals"].append({"sec": 180, "zone": 4, "rpm": [80, 90], "ftp_test": 3})
    spec["segments"][1]["intervals"].append({"sec": 60, "zone": 1, "rpm": [80, 90]})
    workout.validate(spec)
    program = read_back(spec)
    assert mowl_state.matches(spec, program, playlist_id=PLAYLIST)
    by_name = copy.deepcopy(program)
    for segment in by_name["Segments"]:
        for interval in segment["Intervals"]:
            del interval["PositionTypeID"], interval["CycleID"]
    assert mowl_state.matches(spec, by_name, playlist_id=PLAYLIST)


def _changed(path: str, value: Any) -> dict[str, Any]:
    program = read_back(SPEC)
    target: Any = program
    keys: list[Any] = [int(k) if k.isdigit() else k for k in path.split(".")]
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    return program


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("Name", "Another name"),
        ("Description", "Changed"),
        ("PlaylistID", PLAYLIST + 1),
        ("IsPublic", True),
        ("Segments.0.Name", "Renamed"),
        ("Segments.0.IsWarmup", False),
        ("Segments.0.TotalDuration", 301),
        ("Segments.1.Intervals.0.Duration", 241),
        ("Segments.1.Intervals.0.ScaleCoggan", 3),
        ("Segments.1.Intervals.0.PositionTypeID", 1),
        ("Segments.1.Intervals.0.CycleID", 4),
        ("Segments.1.Intervals.0.RPMFrom", "61"),
        ("Segments.1.Intervals.0.RPMTo", "71"),
        ("Segments.1.Intervals.2.IntervalGame", None),
        ("Segments.1.Intervals.1.IntervalGame", {"IntervalGameID": 4}),
        ("Segments.1.Intervals.1.FtpTest", {"FtpTestID": 1}),
        ("Segments", []),
    ],
)
def test_any_compared_difference_is_no_match(path: str, value: Any) -> None:
    assert not mowl_state.matches(SPEC, _changed(path, value), playlist_id=PLAYLIST)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("IsPublic", "false"),
        ("Name", None),
        ("Segments.0.Intervals.0.Duration", "abc"),
        ("Segments.0.Intervals.0.Duration", True),
        ("Segments.0.Intervals", None),
        ("Segments.0.IsCooldown", True),  # a segment of two kinds
        ("Segments.0.Intervals.0.IntervalGame", {"Name": "x"}),
    ],
)
def test_an_unreadable_program_never_matches(path: str, value: Any) -> None:
    program = _changed(path, value)
    with pytest.raises(ValueError, match=r"not|segment"):
        mowl_state.normalized_program(program)
    assert not mowl_state.matches(SPEC, program, playlist_id=PLAYLIST)


def test_what_is_not_compared_does_not_matter() -> None:
    program = read_back(SPEC)
    program["ProgramID"] = 9
    program["TSS"] = 999
    program["ShortDescription"] = "anything"
    program["Segments"][0]["SegmentID"] = 5
    assert mowl_state.matches(SPEC, program, playlist_id=PLAYLIST)
    assert not mowl_state.matches(SPEC, None, playlist_id=PLAYLIST)
    assert not mowl_state.matches(SPEC, [], playlist_id=PLAYLIST)


def test_listing_ids() -> None:
    assert mowl_state.program_id_of({"ProgramID": 5}) == 5
    for bad in ({"ProgramID": "5"}, {"ProgramID": True}, {"ProgramID": 0}, {}, [5], None):
        assert mowl_state.program_id_of(bad) is None


def test_the_write_runs_inside_its_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """``write_program`` sets the thread's call deadline, so each request the library makes
    ends within the bound (``test_egress_deadline`` shows the cut-off itself)."""
    seen: list[float] = []

    def save(spec: Any, creds: Any, playlist_id: int) -> dict[str, Any]:
        seen.append(egress.request_deadline(1000) - time.monotonic())
        return {"program_id": 1}

    monkeypatch.setattr(save_kind.workout, "save", save)
    assert save_kind.write_program(SPEC, Credentials(mowl_token="x" * 36), 7, 2.0) == {
        "program_id": 1
    }
    assert seen == [pytest.approx(2.0, abs=0.5)]
    assert egress.request_deadline(1000) - time.monotonic() == pytest.approx(1000, abs=0.5)
