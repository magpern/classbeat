"""SD-P6 stage E (#1303): the pure parts. ``revision_spotdown_model`` (which tracks a draft had on
spotdown's sections) and ``song_carry_calc.with_kept`` (a kept song's entry carried into a "Change
songs" list); and the ``kept_timing`` adapter (it swallows nothing)."""

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent import track_timing_calc as tt
from classbeat.agent.choose_songs import ChosenSong
from classbeat.agent.songs import KeptSong
from classbeat.classes.duplicate_music import music_copy
from classbeat.design.revision_spotdown_model import (
    MAX_RECORD_BYTES,
    MAX_TRACKS,
    SpotdownSections,
    sections_of,
    stamped,
)
from classbeat.songs import pick_analyse
from classbeat.songs.seed import Seeded
from classbeat.spotdown import song_carry_calc
from classbeat.spotdown.provenance_read_calc import AudioIdentity
from classbeat.spotdown.song_analysis_record_model import (
    KEY,
    Analysed,
    SongOutcome,
    analysis_record,
)
from classbeat.spotdown.song_carry_calc import with_kept
from classbeat.spotdown.song_match_policy import TrackMatch
from classbeat.spotdown.song_match_record_model import match_record
from classbeat.spotdown.timing_class_policy import TimingDecision
from classbeat.spotdown.track_fit_policy import Fit

LIST = uuid.uuid4()


def a_map(n: int = 4) -> dict[str, Any]:
    tracks = [{"track": i + 1, "spotify_id": f"s{i}", "sec": 200.0} for i in range(n)]
    return {"tracks": tracks, "songs": [], "events": []}


# --- revision_spotdown_model ---------------------------------------------------------------------


def test_a_map_without_stamps_records_nothing() -> None:
    assert sections_of(a_map(), LIST) is None
    assert sections_of(a_map(), None) is None  # today's path never needs a list
    assert sections_of({"songs": []}, None) is None  # a mix: no tracks


def test_the_approximate_tracks_are_recorded_sorted() -> None:
    m = tt.stamp(tt.stamp(a_map(), 3, "approximate", "youtube"), 1, "approximate", "youtube")
    assert stamped(m) == [1, 3]
    assert sections_of(m, LIST) == SpotdownSections(song_list_id=LIST, tracks=[1, 3])


def test_an_unusable_stamp_is_not_a_track_on_spotdowns_sections() -> None:
    m = tt.stamp(a_map(), 0, "unusable", "youtube")
    assert stamped(m) == []


def test_a_stamped_map_without_a_list_fails_closed() -> None:
    m = tt.stamp(a_map(), 0, "approximate", "youtube")
    with pytest.raises(ValueError, match="without its song list"):
        sections_of(m, None)


@pytest.mark.parametrize("tracks", [[], [2, 1], [1, 1], [-1], [200], [True]])
def test_the_model_refuses_a_bad_track_list(tracks: list[Any]) -> None:
    with pytest.raises(ValidationError):
        SpotdownSections.model_validate({"song_list_id": LIST, "tracks": tracks})


def test_the_model_refuses_an_unknown_key() -> None:
    with pytest.raises(ValidationError):
        SpotdownSections.model_validate({"song_list_id": LIST, "tracks": [0], "x": 1})


RECORD: dict[str, Any] = {"spotdown_analysis": {"songs": []}, "spotdown_match": {"songs": []}}


def test_a_playlist_designs_own_record_is_its_origin() -> None:
    """#1407: a ``design_draft`` on the user's MOWL playlist keeps the record itself; a revise of
    it records the stamped tracks with the same record (``origin``)."""
    m = tt.stamp(tt.stamp(a_map(), 0, "approximate", "youtube"), 1, "approximate", "youtube")
    origin = SpotdownSections(record=RECORD, tracks=[0, 1, 2, 3])
    assert sections_of(m, None, origin) == SpotdownSections(record=RECORD, tracks=[0, 1])
    listed = SpotdownSections(song_list_id=LIST, tracks=[0])
    assert sections_of(m, None, listed) == SpotdownSections(song_list_id=LIST, tracks=[0, 1])
    assert sections_of(a_map(), None, origin) is None  # nothing stamped: nothing recorded


@pytest.mark.parametrize(
    "value",
    [
        {"tracks": [0]},  # neither
        {"song_list_id": LIST, "record": RECORD, "tracks": [0]},  # both
        {"record": {"x": "y" * 270_000}, "tracks": [0]},  # over the bound
        {"record": [], "tracks": [0]},  # not an object
    ],
    ids=["neither", "both", "too_large", "not_an_object"],
)
def test_the_model_needs_exactly_one_bounded_origin(value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SpotdownSections.model_validate(value)


def test_a_class_copy_keeps_a_record_that_names_no_list() -> None:
    """#1407: the record names no other row of the source class, so the copy's revise reads it."""
    music: dict[str, Any] = {"source": "mowl_spotify_playlist", "fingerprint": "f"}
    music["spotdown"] = {"record": RECORD, "tracks": [0]}
    assert music_copy(music) == music
    broken = {**music, "spotdown": {"tracks": [0]}}  # neither: dropped, never copied as is
    assert music_copy(broken) == {"source": "mowl_spotify_playlist", "fingerprint": "f"}


# --- with_kept -----------------------------------------------------------------------------------


def entry(position: int, use: str = "spotdown", cls: str = "approximate") -> dict[str, Any]:
    return {
        "position": position,
        "use": use,
        "analysis_id": f"an{position}",
        "timing": {"class": cls, "reason": "r", "evidence": {}, "policy_version": "1"},
    }


def record(*entries: dict[str, Any]) -> dict[str, Any]:
    return {KEY: {"v": 1, "rounds": 0, "songs": list(entries), "swaps": []}}


PARENT = record(entry(0), entry(1), entry(2), entry(3, use="sections"), entry(4, cls="unusable"))
LISTED = [("k0", False), ("k1", False), ("new", True), ("k3", False), ("k4", False)]
KEPT = [(0, "k0"), (1, "k1"), (3, "k3"), (4, "k4")]


def test_each_marked_kept_song_gets_its_parents_entry() -> None:
    out = with_kept(record(entry(2)), PARENT, KEPT, LISTED)
    songs = out[KEY]["songs"]
    assert [e["position"] for e in songs] == [0, 1, 2]
    assert songs[0] == {**entry(0), "kept": True}
    assert songs[2] == entry(2)  # the new song's own entry, untouched


def test_no_record_means_nothing_is_carried() -> None:
    assert with_kept({}, PARENT, KEPT, LISTED) == {}


def test_a_kept_song_not_in_its_slot_or_marked_new_is_not_carried() -> None:
    moved = [("k1", False), ("k0", False), ("new", True), ("k3", False), ("k4", False)]
    assert [e["position"] for e in with_kept(record(), PARENT, KEPT, moved)[KEY]["songs"]] == []
    renewed = [("k0", True), *LISTED[1:]]
    assert [e["position"] for e in with_kept(record(), PARENT, KEPT, renewed)[KEY]["songs"]] == [1]


def test_a_slot_recorded_twice_in_the_parent_is_not_carried() -> None:
    parent = record(entry(0), entry(0), entry(1))
    assert [e["position"] for e in with_kept(record(), parent, KEPT, LISTED)[KEY]["songs"]] == [1]


def test_a_slot_the_new_record_names_is_never_overwritten() -> None:
    own = {**entry(1), "analysis_id": "mine"}
    out = with_kept(record(own), PARENT, KEPT, LISTED)
    assert [e["analysis_id"] for e in out[KEY]["songs"]] == ["an0", "mine"]


def test_a_slot_outside_the_list_is_not_carried() -> None:
    assert with_kept(record(), PARENT, [(9, "k9")], LISTED)[KEY]["songs"] == []


# --- the adapter ---------------------------------------------------------------------------------


def chosen() -> Any:
    from classbeat.agent import ChosenList

    songs = tuple(ChosenSong(sid, "t", "a", "climb", 120.0, 200, new, None) for sid, new in LISTED)
    return ChosenList(songs, 1000, 1000, 0, "fill", 0, "")


def seeded() -> Seeded:
    kept = tuple(KeptSong(slot, sid, "t", "a", "climb", 120.0, 200, None) for slot, sid in KEPT)
    return Seeded(uuid.uuid4(), kept, ("climb",), PARENT)


def test_kept_timing_carries_through_the_adapter() -> None:
    out = pick_analyse.kept_timing(record(entry(2)), seeded(), chosen())
    assert [e["position"] for e in out[KEY]["songs"]] == [0, 1, 2]


def test_kept_timing_without_a_seed_or_a_record_is_unchanged() -> None:
    assert pick_analyse.kept_timing(record(entry(2)), None, chosen()) == record(entry(2))
    assert pick_analyse.kept_timing({}, seeded(), chosen()) == {}


@pytest.mark.parametrize("error", [KeyError, ValueError, TypeError, RuntimeError])
def test_an_error_of_any_type_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise error("injected")

    monkeypatch.setattr(pick_analyse, "with_kept", boom)
    with pytest.raises(error):  # no fallback: the job ends ``internal`` (#1354)
        pick_analyse.kept_timing(record(entry(2)), seeded(), chosen())


def test_the_calc_reads_the_strict_marks() -> None:
    assert song_carry_calc.timing_marks(PARENT) == [
        (0, "approximate", "youtube"), (1, "approximate", "youtube"), (2, "approximate", "youtube"),
    ]  # fmt: skip


def test_a_class_copy_drops_the_record_with_the_list() -> None:
    music: dict[str, Any] = {
        "source": "mowl_spotify_playlist", "song_list_id": str(LIST), "fingerprint": "f",
    }  # fmt: skip
    music["spotdown"] = {"song_list_id": str(LIST), "tracks": [0]}
    assert music_copy(music) == {"source": "mowl_spotify_playlist", "fingerprint": "f"}


def test_a_stamped_draft_without_a_list_fails_the_job_internal() -> None:
    from classbeat.design import revision
    from classbeat.jobs import JobFailure

    m = tt.stamp(a_map(), 0, "approximate", "youtube")
    with pytest.raises(JobFailure) as failed:
        revision._sections(m, None, None)  # pyright: ignore[reportPrivateUsage]
    assert failed.value.category == "internal"
    assert revision._sections(a_map(), None, None) is None  # pyright: ignore[reportPrivateUsage]


# --- the record's size bound at the most tracks (#1411 follow-up) --------------------------------


def a_full_record(n: int) -> dict[str, Any]:
    """The stage-A/B record of ``n`` tracks, built by the real record builders from what a long
    playlist's tracks carry: a 128-character fingerprint (the longest the reader takes), a sha256
    config hash, every evidence key the timing policy writes and ``content.explicit``."""
    audio = AudioIdentity("a" * 64, "f" * 128, "mp3", 44100.0, 2.0, 241.123, 240.456)
    evidence = {
        "played_kind": "spotify",
        "source_kind": "youtube",
        "sha256_equal": False,
        "uploaded_as_played": False,
        "anchors": 4,
        "match_band": "confident",
        "recording_match_confidence": 0.912,
        "duration_check": "within_tolerance",
        "decoded_duration_check": "within_tolerance",
        "isrc_equal": True,
        "alignment": "aligned",
        "residual_s": 0.842,
        "offset_s": 12.345,
    }
    decision = TimingDecision("approximate", "recording_match_residual_ok", evidence)
    explicit = {"is_explicit": True, "confidence": 0.93}
    outcomes = {
        i: SongOutcome(
            "spotdown",
            Analysed(
                str(uuid.uuid4()),
                ("spotdown-analyzer", "6.3.0", "c" * 64),
                audio,
                decision,
                explicit,
            ),
            Fit(True, ()),
        )
        for i in range(n)
    }
    matches = {
        f"id{i}": TrackMatch("timing", "matched", "confident", 0.912, "match-v1") for i in range(n)
    }
    return {
        **match_record(list(matches), matches, {}, rounds=0, candidates=n, missing=0),
        **analysis_record(outcomes, {}, rounds=0),
    }


def test_a_record_of_the_most_tracks_stays_within_its_bound() -> None:
    n = MAX_TRACKS
    record = a_full_record(n)
    size = len(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
    assert size <= MAX_RECORD_BYTES, f"{size} B for {n} tracks, bound {MAX_RECORD_BYTES}"
    field = SpotdownSections(tracks=list(range(n)), record=record)  # the model accepts it
    assert field.record == record
