"""The SwedeBeat studio's pure rules (ADR 0036 sections 3 and 10): the setting, the evidence rule,
swaps, undo, gaps, the length check and what the approval hash covers. No I/O."""

from typing import Any

import pytest

from classbeat.agent.brief import Brief
from classbeat.agent.songs import TOLERANCE, PickPlan
from classbeat.songs.song_list import content_hash
from classbeat.studio.output_choice_policy import STUDIO_VALUES
from classbeat.swedebeat_match.verdict_policy import Verdict
from classbeat.swedebeat_studio.candidate_policy import (
    Candidate,
    facts_of,
    pair_swaps,
    short,
    song_of,
    to_replace,
)
from classbeat.swedebeat_studio.config import StudioConfig
from classbeat.swedebeat_studio.config_policy import OUTPUTS, VALUES, canonical, studio_values
from classbeat.swedebeat_studio.evidence_policy import evidence, merge, really_missing, retry_slots
from classbeat.swedebeat_studio.length_policy import UPPER, within
from classbeat.swedebeat_studio.song_list_model import StudioListContent
from classbeat.swedebeat_studio.swap_policy import (
    GapInMix,
    another_slots,
    askable,
    expand,
    mix_tracks,
    undo,
    undoable,
)


def _found(n: int = 0, score: float = 0.93) -> Verdict:
    return Verdict("found", f"SB Title {n}", f"SB Artist {n}", 240, f"sb-ref-{n}", 128.0, score)


MISSING = Verdict("missing")
UNSURE = Verdict("unsure", "Other", "Artist", 240, "sb-ref-u", 128.0, 0.6)


def _id(n: int) -> str:
    return f"StudioSong{n:012d}"


def _cand(n: int, role: str, found: Verdict = MISSING) -> Candidate:
    return Candidate(_id(n), f"Song {n}", f"Band {n}", role, None, found)


# --- the setting ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "values"),
    [
        ("", frozenset[str]()),
        ("  ", frozenset[str]()),
        ("design", {"design"}),
        (" design , swedebeat_playlist", {"design", "swedebeat_playlist"}),
        ("design,swedebeat_mix,mowl", {"design", "swedebeat_mix", "mowl"}),
        ("design,swedebeat_mix,bass_swap", {"design", "swedebeat_mix", "bass_swap"}),  # B8
        (
            "design,swedebeat_playlist,swedebeat_mix,mowl,bass_swap",
            {"design", "swedebeat_playlist", "swedebeat_mix", "mowl", "bass_swap"},
        ),
    ],
)
def test_the_studio_setting_accepts_its_values(raw: str, values: set[str]) -> None:
    assert studio_values(raw) == values


@pytest.mark.parametrize(
    "raw",
    [
        "bogus",
        "design,bogus",
        "design,,swedebeat_mix",
        "design,design",
        "swedebeat_playlist",  # an output needs design
        "mowl",
        "design,mowl",  # mowl needs swedebeat_mix
        "DESIGN",
        "bass_swap",  # bass_swap needs swedebeat_mix (ADR 0036 amendment B8)
        "design,bass_swap",
        "design,swedebeat_playlist,bass_swap",
        "design,swedebeat_playlist,mowl,bass_swap",
        "design,swedebeat_mix,BASS_SWAP",  # an unknown value
        "design,swedebeat_mix,bass-swap",
        "design,swedebeat_mix,bass_swap,bass_swap",
    ],
)
def test_any_other_studio_value_refuses_to_start(raw: str) -> None:
    with pytest.raises(ValueError, match="swedebeat_studio"):
        studio_values(raw)


def test_the_stored_setting_is_in_a_fixed_order() -> None:
    assert canonical("mowl, swedebeat_mix ,design") == "design,swedebeat_mix,mowl"
    assert canonical("") == ""
    assert canonical("bass_swap,swedebeat_mix,design") == "design,swedebeat_mix,bass_swap"


def test_the_setting_without_bass_swap_has_it_off() -> None:
    assert not StudioConfig(studio_values("design,swedebeat_mix")).bass_swap  # the default
    assert not StudioConfig().bass_swap
    assert StudioConfig(studio_values("design,swedebeat_mix,bass_swap")).bass_swap


def test_bass_swap_is_never_an_output() -> None:
    assert frozenset({"swedebeat_playlist", "swedebeat_mix", "mowl"}) == OUTPUTS
    assert "bass_swap" in VALUES
    assert "bass_swap" not in OUTPUTS
    assert OUTPUTS == STUDIO_VALUES  # the output choice's three


# --- the evidence rule ---------------------------------------------------------------------


def test_a_transient_per_track_miss_keeps_the_song() -> None:
    first = [_found(0), MISSING, _found(2)]
    assert retry_slots(first) == [1]
    final = merge(first, {1: _found(1)})
    assert [v.status for v in final] == ["found", "found", "found"]
    found = evidence(first, final)
    assert (found.state, found.sent, found.missing_first, found.missing) == ("ok", 3, 1, 0)
    assert really_missing(final) == []


def test_every_song_still_missing_is_suspected_not_proof() -> None:
    first = [MISSING, MISSING, MISSING]
    final = merge(first, {0: MISSING, 1: MISSING, 2: MISSING})
    assert evidence(first, final).state == "suspected_unavailable"
    # Fewer than three sent is not enough to suspect anything.
    assert evidence(first[:2], final[:2]).state == "ok"


def test_a_high_miss_rate_alone_never_blocks() -> None:
    first = [MISSING] * 7 + [_found(n) for n in range(3)]  # 70 % missing
    final = merge(first, dict.fromkeys(range(7), MISSING))
    found = evidence(first, final)
    assert found.state == "ok"
    assert really_missing(final) == list(range(7))


def test_an_unsure_answer_is_not_asked_again_and_counts_as_not_found() -> None:
    first = [UNSURE, _found(1), MISSING]
    assert retry_slots(first) == [2]
    final = merge(first, {2: MISSING})
    assert evidence(first, final).state == "ok"  # one unsure: not every song missing
    assert really_missing(final) == [0, 2]
    with pytest.raises(ValueError, match="missing"):
        merge(first, {0: _found(0)})  # only a missing track is matched again


def test_a_found_song_below_same_song_has_no_facts() -> None:
    assert facts_of(_found(0, 0.85)) is not None
    assert facts_of(Verdict("found", "T", "A", 240, "ref", 128.0, None)) is None
    assert facts_of(UNSURE) is None
    assert facts_of(Verdict("found", "T", "A", None, "ref", 128.0, 0.9)) is None  # no duration
    assert facts_of(Verdict("found", "T", "A", 240, "ref", None, 0.9)) is None  # no BPM


# --- swaps ---------------------------------------------------------------------------------


def test_each_missing_original_takes_a_usable_new_song_of_its_role() -> None:
    originals = [_cand(1, "climb"), _cand(2, "sprint", UNSURE), _cand(3, "climb")]
    fresh = [
        _cand(10, "sprint", _found(10)),
        _cand(11, "climb", MISSING),
        _cand(12, "climb", _found(12, 0.9)),
    ]
    swaps, left = pair_swaps(originals, fresh)
    assert set(swaps) == {_id(12), _id(10)}
    climb = swaps[_id(12)]
    assert (climb.original.title, climb.replacement.title) == ("Song 1", "Song 12")
    assert (climb.reason, climb.score, climb.undone) == ("missing", 0.9, False)
    assert swaps[_id(10)].reason == "unsure"
    assert left == [originals[2]]  # no second usable climb


def _plan() -> PickPlan:
    return PickPlan.build(Brief(length_min=30, intensity="moderate", climbs=1, sprints=2))


def test_only_the_missing_songs_the_list_needs_are_swapped() -> None:
    plan = _plan()
    found = [_cand(n, role, _found(n)) for n, role in enumerate(["warmup", "build"])]
    found += [_cand(6, "sprint", _found(6)), _cand(12, "cooldown", _found(12))]
    missing = [_cand(n, role) for n, role in [(1, "warmup"), (3, "build"), (4, "climb")]]
    missing += [_cand(n, role) for n, role in [(5, "climb"), (7, "sprint"), (10, "recovery")]]
    assert short(found, plan.slots, plan.required_roles())
    wanted = to_replace(missing, found, plan.slots, plan.required_roles())
    # A climb and a sprint first (the roles the list lacks), then two more for the 8 slots.
    assert [c.spotify_id for c in wanted] == [_id(4), _id(7), _id(1), _id(3)]
    many = found + [_cand(20 + n, "sprint", _found(20 + n)) for n in range(3)]
    many += [_cand(30, "climb", _found(30))]
    assert not short(many, plan.slots, plan.required_roles())


def _song(n: int, role: str = "climb", **extra: Any) -> dict[str, Any]:
    facts = {
        "reference": f"sb-ref-{n}",
        "title": f"SB {n}",
        "artist": f"SB Band {n}",
        "duration_sec": 240,
        "bpm": 128.0,
        "score": 0.93,
    }
    song: dict[str, Any] = {
        "spotify_id": _id(n),
        "title": f"Song {n}",
        "artist": f"Band {n}",
        "role": role,
        "new": True,
        "cover": None,
        "gap": False,
        "swedebeat": facts,
        "swap": None,
    }
    return song | extra


def _content(**extra: Any) -> dict[str, Any]:
    songs = [_song(0, "warmup"), _song(1), _song(2, "cooldown")]
    swapped = song_of(_cand(5, "climb", _found(5)), "climb", None)
    swap = pair_swaps([_cand(1, "climb")], [_cand(5, "climb", _found(5))])[0][_id(5)]
    songs[1] = swapped.model_copy(update={"swap": swap}).model_dump(mode="json")
    base: dict[str, Any] = {
        "v": 1,
        "source": "swedebeat_studio",
        "mode": "pick",
        "target_sec": 700,
        "total_sec": 702,
        "crossfade_sec": 9.0,
        "songs": songs,
        "evidence": {"state": "ok", "sent": 3, "missing_first": 1, "missing": 1},
        "rounds": 1,
        "note": "",
    }
    return base | extra


def test_a_swap_is_recorded_and_can_be_undone_to_a_marked_gap() -> None:
    content = StudioListContent.model_validate(_content())
    assert undoable(content, 1) and not undoable(content, 0)  # noqa: PT018
    assert [t.reference for t in mix_tracks(content)] == ["sb-ref-0", "sb-ref-5", "sb-ref-2"]
    undone = undo(content, 1)
    gap = undone.songs[1]
    assert (gap.gap, gap.swedebeat, gap.spotify_id, gap.title) == (True, None, _id(1), "Song 1")
    assert gap.swap is not None and gap.swap.undone  # noqa: PT018
    assert undone.total_sec == 480  # two songs left in the mix, whole songs
    assert undone.crossfade_sec == 0
    assert not undoable(undone, 1)
    assert askable(undone, 1)  # the user can ask for another song for the gap
    with pytest.raises(ValueError, match="undo"):
        undo(undone, 1)


def test_a_gap_can_never_go_into_a_mix() -> None:
    undone = undo(StudioListContent.model_validate(_content()), 1)
    with pytest.raises(GapInMix):
        mix_tracks(undone)
    suspected = _content(
        songs=[_song(n) | {"gap": True, "swedebeat": None} for n in range(3)],
        evidence={"state": "suspected_unavailable", "sent": 3, "missing_first": 3, "missing": 3},
    )
    with pytest.raises(GapInMix):
        mix_tracks(StudioListContent.model_validate(suspected))


def test_the_model_refuses_a_gap_with_facts_and_a_suspected_list_with_songs() -> None:
    with pytest.raises(ValueError, match="gap"):
        StudioListContent.model_validate(_content(songs=[_song(0) | {"gap": True}]))
    with pytest.raises(ValueError, match="suspected"):
        StudioListContent.model_validate(
            _content(
                evidence={
                    "state": "suspected_unavailable",
                    "sent": 3,
                    "missing_first": 3,
                    "missing": 3,
                },
            )
        )
    bad = _song(0) | {"swedebeat": {**_song(0)["swedebeat"], "reference": "../x"}}
    with pytest.raises(ValueError, match="pattern"):
        StudioListContent.model_validate(_content(songs=[bad]))


def test_another_keeps_the_other_songs_in_their_slots_without_the_other_gaps() -> None:
    songs = [_song(0, "warmup"), _song(1), _song(2), _song(3, "cooldown")]
    songs[2] = _song(2) | {"gap": True, "swedebeat": None}  # a gap of a suspected song
    content = StudioListContent.model_validate(_content(songs=songs))
    # Slot 1 has no swap and no gap: nothing to ask another for.
    assert not askable(content, 1)
    with_gap = undo(StudioListContent.model_validate(_content()), 1)
    slots = another_slots(with_gap, 1)
    assert (slots.slot, slots.role, [k.slot for k in slots.kept]) == (1, "climb", [0, 2])
    assert slots.gaps == {}
    four = StudioListContent.model_validate(
        _content(songs=[*with_gap.model_dump(mode="json")["songs"], songs[2]])
    )
    slots = another_slots(four, 1)
    assert [k.slot for k in slots.kept] == [0, 2] and list(slots.gaps) == [3]  # noqa: PT018
    chosen = [four.songs[0], four.songs[1], four.songs[2]]
    assert [s.spotify_id for s in expand(chosen, slots.gaps)] == [
        _id(0), _id(1), _id(2), _id(2)
    ]  # fmt: skip


# --- length and the hash -------------------------------------------------------------------


def test_the_class_length_check_is_ten_percent_each_way() -> None:
    assert UPPER == TOLERANCE == 0.10
    assert within(1620, 1800) and not within(1619, 1800)  # noqa: PT018 - 10 % under
    assert within(1980, 1800) and not within(1981, 1800)  # noqa: PT018 - 10 % over


def test_the_approval_hash_changes_with_a_reference() -> None:
    base = _content()
    other = _content()
    other["songs"][0] = {**base["songs"][0], "swedebeat": {**base["songs"][0]["swedebeat"]}}
    assert content_hash(base) == content_hash(other)
    other["songs"][0]["swedebeat"]["reference"] = "sb-ref-other"
    assert content_hash(base) != content_hash(other)
    for key, value in (("duration_sec", 241), ("bpm", 127.9)):
        changed = _content()
        changed["songs"][0]["swedebeat"] = {**changed["songs"][0]["swedebeat"], key: value}
        assert content_hash(changed) != content_hash(base)


def test_a_studio_list_never_parses_as_a_song_picks_list() -> None:
    """``songs.song_list.load`` (the song pick's approval, resume and seed) refuses a studio list,
    so no reader of a song pick's list can take a studio list for one."""
    from pydantic import ValidationError

    from classbeat.songs.song_list import SongListContent

    with pytest.raises(ValidationError):
        SongListContent.model_validate(_content())
