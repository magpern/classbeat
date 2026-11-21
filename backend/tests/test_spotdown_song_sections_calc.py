"""SD-P6 stage D (#1303, ADR 0040 sections 2 and 3): a Spotify pick's music data on spotdown's
sections (``song_sections_calc``). Pure: no network, no database, invented values only.

- what the stage-B record gives phase 2 (``recorded``), strictly;
- a document is used only when it is the recorded analysis aligned to this track (``verified``),
  each condition failing alone;
- a track's sections replaced as the library's own ``build_map`` would have built them, and every
  other track, section and event kept (``with_sections``);
- the stamp: a YouTube track is never ``exact``; ``unusable`` or an unknown class keeps MOWL's
  sections (``apply_track``).
"""

import copy
import hashlib
from typing import Any

import pytest
from mowl import spotify

from classbeat.agent import track_timing_calc as tt
from classbeat.songs.pick_analyse_batch import Want, reference_of
from classbeat.spotdown.provenance_read_calc import AudioIdentity
from classbeat.spotdown.song_analysis_record_model import (
    KEY,
    Analysed,
    SongOutcome,
    analysis_record,
)
from classbeat.spotdown.song_sections_calc import (
    MAX_LENGTH_DIFF_S,
    Recorded,
    apply_track,
    recorded,
    reference_id,
    verified,
    with_sections,
)
from classbeat.spotdown.timing_class_policy import TimingDecision
from classbeat.spotdown.track_fit_policy import Fit
from songanalysisdocs import youtube_doc

IDS = [f"SectSynth{i:013d}" for i in range(4)]
SEC = 240
LOUD = [-12.0, -6.0, -11.0, -5.0]


def mowl_sections(track_sec: int = SEC) -> list[dict[str, Any]]:
    return [
        {"start": float(s), "duration": 60.0, "loudness": LOUD[(s // 60) % 4]}
        for s in range(0, track_sec, 60)
    ]


SPOTDOWN = [
    {"start": 0.0, "duration": 30.0, "loudness": 6.0},
    {"start": 30.0, "duration": 90.0, "loudness": 18.0},
    {"start": 120.0, "duration": 60.0, "loudness": 9.0},
    {"start": 180.0, "duration": 60.0, "loudness": 21.0},
]


def rows(bpms: tuple[int, ...] = (120, 124, 0, 132)) -> list[dict[str, Any]]:
    return [
        {"Title": f"Song {i}", "Artist": "Band", "Duration": SEC * 1000, "BPM": bpms[i],
         "SpotifyTrackID": IDS[i]}
        for i in range(4)
    ]  # fmt: skip


def playlist(override: dict[int, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """MOWL's map of the four tracks (9 s crossfade); ``override``: a track's own sections."""
    sections = {IDS[i]: mowl_sections() for i in range(4)}
    for i, secs in (override or {}).items():
        sections[IDS[i]] = secs
    return spotify.build_map(rows(), sections)


AUDIO = AudioIdentity("a" * 64, "fp", "mp3", 44100.0, 2.0, 240.0, 239.0)


def provenance(*positions: int, cls: str = "approximate") -> dict[str, Any]:
    decision = TimingDecision(cls, "matched_recording", {"residual_s": 0.3})  # pyright: ignore[reportArgumentType]
    outcomes = {
        p: SongOutcome(
            "spotdown", None, Analysed(f"an_{p:04d}", ("spotdown", "1", None), AUDIO, decision),
            Fit(True, ()),
        )
        for p in positions
    }  # fmt: skip
    return analysis_record(outcomes, {}, rounds=0)


def rec(position: int = 1, **kw: Any) -> Recorded:
    decision = TimingDecision("approximate", "matched_recording", {})
    base: dict[str, Any] = {
        "position": position, "analysis_id": "an_0001", "sha256": "a" * 64,
        "decision": decision, "source_kind": "youtube",
    }  # fmt: skip
    return Recorded(**(base | kw))


def doc_for(spotify_id: str = IDS[1], **kw: Any) -> dict[str, Any]:
    d = youtube_doc("vid00000001", 124.0, **kw)
    d["analysis_id"] = "an_0001"
    d["provenance"]["audio"]["sha256"] = "a" * 64
    d["alignment"]["reference"] = {"kind": "spotify", "id": reference_id(spotify_id),
                                   "duration_s": float(SEC)}  # fmt: skip
    return d


# --- reference_id -------------------------------------------------------------------------------


def test_the_reference_id_is_the_one_stage_b_sends() -> None:
    sent = reference_of(Want(0, IDS[0], (), 240.0))
    assert (sent.kind, sent.id) == ("spotify", reference_id(IDS[0]))
    assert reference_id(IDS[0]) == hashlib.sha256(f"spotify:{IDS[0]}".encode()).hexdigest()[:32]
    assert IDS[0] not in reference_id(IDS[0])


# --- recorded ------------------------------------------------------------------------------------


def test_recorded_gives_each_marked_song_with_its_analysis() -> None:
    got = recorded(provenance(0, 2))
    assert [(r.position, r.analysis_id, r.sha256, r.source_kind) for r in got] == [
        (0, "an_0000", "a" * 64, "youtube"), (2, "an_0002", "a" * 64, "youtube"),
    ]  # fmt: skip
    assert got[0].decision == TimingDecision(
        "approximate", "matched_recording", {"residual_s": 0.3}, "1"
    )


def test_recorded_is_empty_without_the_record() -> None:
    assert recorded({}) == []
    assert recorded({KEY: "x"}) == []
    assert recorded({KEY: {"songs": "x"}}) == []


def _broken(change: Any) -> list[Recorded]:
    prov = provenance(0, 1)
    change(prov[KEY]["songs"][1])
    return recorded(prov)


@pytest.mark.parametrize(
    "change",
    [
        lambda e: e.pop("analysis_id"),
        lambda e: e.update(analysis_id=""),
        lambda e: e.update(analysis_id=7),
        lambda e: e.pop("audio"),
        lambda e: e["audio"].update(sha256="A" * 64),
        lambda e: e["audio"].update(sha256="a" * 63),
        lambda e: e["timing"].pop("reason"),
        lambda e: e["timing"].update(reason=3),
        lambda e: e["timing"].update(evidence=[]),
        lambda e: e["timing"].pop("policy_version"),
        lambda e: e["timing"].update(policy_version=1),
        lambda e: e.update(use="sections"),
        lambda e: e["timing"].update({"class": "exact"}),
        lambda e: e["timing"].update({"class": "unusable"}),
    ],
    ids=[
        "no_id",
        "empty_id",
        "int_id",
        "no_audio",
        "upper_sha",
        "short_sha",
        "no_reason",
        "int_reason",
        "list_evidence",
        "no_version",
        "int_version",
        "sections",
        "exact",
        "unusable",
    ],
)
def test_recorded_leaves_out_an_entry_that_lacks_anything(change: Any) -> None:
    assert [r.position for r in _broken(change)] == [0]


def test_recorded_leaves_out_a_position_recorded_twice() -> None:
    prov = provenance(0, 1)
    prov[KEY]["songs"].append(copy.deepcopy(prov[KEY]["songs"][1]))
    assert [r.position for r in recorded(prov)] == [0]


# --- verified ------------------------------------------------------------------------------------


def test_the_recorded_analysis_of_this_track_is_verified() -> None:
    assert verified(doc_for(), rec(), IDS[1], float(SEC))
    assert verified(doc_for(), rec(), IDS[1], SEC + MAX_LENGTH_DIFF_S)  # the bound is inclusive


@pytest.mark.parametrize(
    "case",
    [
        "id",
        "sha",
        "kind_mismatch",
        "kind_not_allowed",
        "no_reference",
        "reference_kind",
        "reference_id",
        "other_track",
        "length",
        "no_length",
    ],
)
def test_each_condition_alone_refuses(case: str) -> None:
    d, r, track, length = doc_for(), rec(), IDS[1], float(SEC)
    if case == "id":
        d["analysis_id"] = "an_9999"
    elif case == "sha":
        d["provenance"]["audio"]["sha256"] = "b" * 64
    elif case == "kind_mismatch":
        r = rec(source_kind="url")
    elif case == "kind_not_allowed":  # the record and the document agree on a kind not allowed
        d["provenance"]["source"]["kind"] = "upload"
        r = rec(source_kind="upload")
    elif case == "no_reference":
        d["alignment"].pop("reference")
    elif case == "reference_kind":
        d["alignment"]["reference"]["kind"] = "isrc"
    elif case == "reference_id":
        d["alignment"]["reference"]["id"] = "0" * 32
    elif case == "other_track":
        track = IDS[2]
    elif case == "length":
        length = SEC + MAX_LENGTH_DIFF_S + 0.01
    else:
        d["alignment"]["reference"].pop("duration_s")
    assert not verified(d, r, track, length)


# --- with_sections -------------------------------------------------------------------------------


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_a_tracks_sections_are_what_build_map_makes_of_spotdowns(index: int) -> None:
    m = playlist()
    before = copy.deepcopy(m)
    assert with_sections(m, index, SPOTDOWN) == playlist({index: SPOTDOWN})
    assert m == before  # a copy


def test_the_other_tracks_are_kept_and_only_renumbered() -> None:
    m = playlist()
    out = with_sections(m, 1, SPOTDOWN)
    assert out["tracks"] == m["tracks"]
    assert (out["duration"], out["crossfade"]) == (m["duration"], m["crossfade"])

    def strip(songs: list[dict[str, Any]], track: int) -> list[dict[str, Any]]:
        return [{k: v for k, v in s.items() if k != "song"} for s in songs if s["track"] == track]

    for track in (1, 3, 4):
        assert strip(out["songs"], track) == strip(m["songs"], track)
    new = [s for s in out["songs"] if s["track"] == 2]
    assert [s["start"] - new[0]["start"] for s in new] == [0.0, 30.0, 120.0, 180.0]
    assert {s["bpm"] for s in new} == {124.0}  # MOWL's BPM: Spotify's own of the played track
    assert [s["song"] for s in out["songs"]] == list(range(1, len(out["songs"]) + 1))


def test_a_track_with_no_spotdown_section_at_its_start_gets_one() -> None:
    late = [{"start": 20.0, "duration": 220.0, "loudness": 9.0}]
    assert with_sections(playlist(), 2, late) == playlist({2: late})


def test_a_map_out_of_order_raises() -> None:
    m = playlist()
    m["songs"] = list(reversed(m["songs"]))
    with pytest.raises(ValueError, match="sections"):
        with_sections(m, 1, SPOTDOWN)
    m = playlist()
    m["events"] = list(reversed(m["events"]))
    with pytest.raises(ValueError, match="events"):
        with_sections(m, 1, SPOTDOWN)
    m = playlist()
    m["tracks"][1]["track"] = 3
    with pytest.raises(ValueError, match="tracks"):
        with_sections(m, 1, SPOTDOWN)


# --- apply_track: the class -------------------------------------------------------------------


def _decision(cls: object) -> TimingDecision:
    return TimingDecision(cls, "r", {})  # pyright: ignore[reportArgumentType]


def test_an_approximate_track_is_on_spotdowns_sections_and_stamped() -> None:
    out = apply_track(playlist(), 1, _decision("approximate"), "youtube", SPOTDOWN)
    assert out is not None
    assert tt.classes(out) == {1: "approximate"}
    assert (out["tracks"][1]["timing"], out["tracks"][1]["timing_source"]) == (
        "approximate", "youtube",
    )  # fmt: skip
    unstamped = copy.deepcopy(out)
    for key in ("timing", "timing_source"):
        unstamped["tracks"][1].pop(key)
    assert unstamped == playlist({1: SPOTDOWN})


@pytest.mark.parametrize(
    "cls", ["exact", "approximate", "unusable", "Exact", "precise", "", None, 1]
)
@pytest.mark.parametrize("kind", ["youtube", "url", "YouTube", "upload ", "", "spotify"])
def test_a_youtube_or_other_non_upload_track_never_becomes_exact(cls: object, kind: str) -> None:
    out = apply_track(playlist(), 1, _decision(cls), kind, SPOTDOWN)
    if out is None:  # MOWL's sections stay: today's track, no stamp
        assert tt.capped(cls, kind) != "approximate"
        return
    assert tt.classes(out) == {1: "approximate"}  # never exact, whatever the class said
    assert out["tracks"][1]["timing"] == "approximate"


@pytest.mark.parametrize("cls", ["unusable", "precise", "", None])
def test_unusable_or_an_unknown_class_keeps_mowls_sections(cls: object) -> None:
    assert apply_track(playlist(), 1, _decision(cls), "youtube", SPOTDOWN) is None


def test_an_exact_class_on_a_youtube_source_is_approximate() -> None:
    out = apply_track(playlist(), 1, _decision("exact"), "youtube", SPOTDOWN)
    assert out is not None
    assert tt.classes(out) == {1: "approximate"}
