"""The composer's pure rules (ADR 0038 section 5, #1188): the sketch's song slots, where pinned
songs go, and favoured tracks over search hits where they fit. Table-driven, no I/O."""

from typing import Any

import pytest

from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.agent.pick_pool import PoolPick, PoolResult
from classbeat.agent.registry import Entry
from classbeat.agent.songs import PoolTrack
from classbeat.songs.composer_pick import candidate_set
from classbeat.songs.composer_slots_policy import pin_slots, slot_roles
from classbeat.songs.composer_sources import Favoured, Pinned
from classbeat.songs.favoured_policy import prefer_favoured


def sketch(*parts: tuple[str, int, int, bool]) -> dict[str, Any]:
    """Segments of one interval each: (kind, seconds, zone, standing)."""
    return {
        "segments": [
            {"kind": k, "intervals": [{"sec": s, "zone": z, "standing": st}]}
            for k, s, z, st in parts
        ]
    }


@pytest.mark.parametrize(
    ("content", "roles"),
    [
        (sketch(("warmup", 480, 2, False), ("main", 960, 4, True), ("cooldown", 480, 1, False)),
         ["warmup", "warmup", "climb", "climb", "climb", "climb", "cooldown", "cooldown"]),
        (sketch(("main", 240, 6, False), ("main", 240, 3, False), ("cooldown", 240, 1, False)),
         ["warmup", "build", "cooldown"]),  # an opener first, a cooldown last, always
        (sketch(("warmup", 240, 2, False), ("main", 480, 5, False), ("recovery", 240, 1, False),
                ("cooldown", 240, 1, False)),
         ["warmup", "interval", "interval", "recovery", "cooldown"]),
    ],
)  # fmt: skip
def test_slot_roles_follow_the_sketch(content: dict[str, Any], roles: list[str]) -> None:
    assert slot_roles(content) == roles


ROLES = ["warmup", "build", "climb", "sprint", "climb", "climb", "recovery", "cooldown"]


@pytest.mark.parametrize(
    ("hints", "slots"),
    [
        (["warm-up"], [0]),
        (["climb 2"], [4]),
        (["last climb"], [5]),
        (["the cooldown"], [7]),
        (["song 4"], [3]),
        (["first sprint"], [3]),
        ([None, "nowhere in particular"], [1, 2]),  # the first free middle slots
        (["last climb", "last climb"], [5, 4]),  # the nearest free slot; a pin never moves one
        (["climb 9"], [5]),  # past the last climb: the last one
    ],
)
def test_pins_go_to_their_positions(hints: list[str | None], slots: list[int]) -> None:
    assert pin_slots(hints, ROLES) == slots


def track(tid: str, bpm: float, length: int = 240) -> PoolTrack:
    return PoolTrack(
        spotify_id=tid, title=tid, artist="A", role="build", bpm=bpm, length_sec=length
    )


def song(tid: str, bpm: float, length: int = 240, *, new: bool = True) -> ChosenSong:
    return ChosenSong(tid, tid, "A", "climb", bpm, length, new)


def chosen(*songs: ChosenSong, target: int = 951) -> ChosenList:
    total = sum(s.length_sec for s in songs) - 9 * (len(songs) - 1)
    return ChosenList(tuple(songs), total, target, 9.0, "pick", 0, "")


@pytest.mark.parametrize(
    ("favoured", "fixed", "expected"),
    [
        ([track("fav1", 122)], (), ["s0", "fav1", "s2", "s3"]),  # fits: within 10 % of the BPM
        ([track("fav1", 150)], (), ["s0", "s1", "s2", "s3"]),  # too far from the tempo
        ([track("fav1", 121, length=600)], (), ["s0", "s1", "s2", "s3"]),  # the total breaks
        ([track("fav1", 121)], (1,), ["s0", "s1", "s2", "fav1"]),  # a fixed slot never changes
        ([track("fav1", 99), track("fav2", 121)], (), ["fav1", "fav2", "s2", "s3"]),
    ],
)
def test_favoured_tracks_are_chosen_over_search_hits_where_they_fit(
    favoured: list[PoolTrack], fixed: tuple[int, ...], expected: list[str]
) -> None:
    listed = chosen(song("s0", 100), song("s1", 120), song("s2", 140, new=False), song("s3", 120))
    tracks = [track("s0", 100), track("s1", 120), track("s3", 120), *favoured]
    out = prefer_favoured(listed, tracks, {t.spotify_id for t in favoured}, set(fixed))
    assert [s.spotify_id for s in out.songs] == expected
    assert [s.role for s in out.songs] == [s.role for s in listed.songs]  # the slots keep roles


def test_a_swap_is_never_undone_by_the_favoured_rule() -> None:
    """#1188 review B2: the favoured song a swap took out is in the pool again (the playlists are
    read again), and the model chose a search hit for its slot; it is never put back."""
    swapped_out = track("fav_out", 126)
    listed = chosen(song("s0", 100), song("hit", 128), song("s2", 140, new=False), song("s3", 120))
    tracks = [track("s0", 100), track("hit", 128), track("s3", 120), swapped_out]
    out = prefer_favoured(listed, tracks, {"fav_out"}, set(), {"fav_out"})
    assert [s.spotify_id for s in out.songs] == ["s0", "hit", "s2", "s3"]
    back = prefer_favoured(listed, tracks, {"fav_out"}, set())  # without the exclusion: back
    assert "fav_out" in [s.spotify_id for s in back.songs]


def test_the_candidate_set_never_holds_an_excluded_song() -> None:
    """#1188 review F1: the one set the pool write, the analysis and the choice use."""

    def entry(ref: int, tid: str) -> Entry:
        return Entry(ref, tid, "A", "B", None, 240, tid)

    model = PoolResult((PoolPick(0, "build", "out"), PoolPick(1, "build", "hit")))
    favoured = Favoured((entry(0, "out"), entry(2, "fav")), ())
    pinned = Pinned(((0, entry(3, "pin")),), ())
    picked = candidate_set(model, favoured, pinned, {}, frozenset({"out"}))
    assert [p.spotify_id for p in picked] == ["hit", "pin", "fav"]
    unfiltered = candidate_set(model, favoured, pinned, {}, frozenset())
    assert "out" in [p.spotify_id for p in unfiltered]
