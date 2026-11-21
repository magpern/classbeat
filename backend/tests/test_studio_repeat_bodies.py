"""#1342 part 1 (ADR 0036 amendment A8): what an output writes for a mix that repeats a song, built
from the spec, never from model text. The final Spotify playlist lists each song once, in the
order of its first piece; X-Tracks has one entry per loop run (a non-adjacent repeat is its own
entry) and the entries' lengths still add up to the mix. Pure: no I/O."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from mowl import compose

from classbeat.studio import output_body_policy as body
from classbeat.swedebeat_studio.outputs_job import Producer
from classbeat.swedebeat_studio.outputs_spotify import track_ids

FACTS = {
    "ref-a": body.Facts("Alpha", "Ann", 128.0),
    "ref-b": body.Facts("Beta", "Bo", 130.0),
    "ref-c": body.Facts("Gamma", "Gus", 126.0),
}
DURATIONS = {"ref-a": 240.0, "ref-b": 210.0, "ref-c": 200.0}


def spec(*cuts: tuple[str, float, float], crossfades: list[int]) -> dict[str, Any]:
    return {
        "tracks": [{"reference": r, "start_sec": a, "end_sec": b} for r, a, b in cuts],
        "crossfades": crossfades,
    }


def composed_sec(value: dict[str, Any]) -> float:
    maps = [
        {"duration": DURATIONS[t["reference"]], "songs": [], "events": []} for t in value["tracks"]
    ]
    return float(compose.compose_map(maps, value)["duration"])


NON_ADJACENT = spec(
    ("ref-a", 0, 60.0), ("ref-b", 0, 210.0), ("ref-a", 0, 60.0), ("ref-c", 0, 200.0),
    ("ref-a", 100.0, 160.0), crossfades=[4, 8, 4, 6],
)  # fmt: skip
# A loop run (b, b: one song playing on) as part 2 will allow it; the bodies already take it.
LOOPED = spec(
    ("ref-a", 0, 120.0), ("ref-b", 0, 64.0), ("ref-b", 64.0, 128.0), ("ref-c", 0, 200.0),
    ("ref-a", 0, 120.0), crossfades=[6, 0, 8, 5],
)  # fmt: skip


@pytest.mark.parametrize(
    ("value", "titles"),
    [
        (NON_ADJACENT, ["Alpha", "Beta", "Alpha", "Gamma", "Alpha"]),
        (LOOPED, ["Alpha", "Beta", "Gamma", "Alpha"]),
    ],
)
def test_x_tracks_is_one_entry_per_loop_run_and_adds_up_to_the_mix(
    value: dict[str, Any], titles: list[str]
) -> None:
    plan = body.render_plan(value, DURATIONS)
    assert len(plan) == len(value["tracks"])  # the render keeps one entry per piece
    items = body.x_tracks(plan, FACTS)
    assert [i["title"] for i in items] == titles
    assert sum(i["duration"] for i in items) == pytest.approx(composed_sec(value), abs=0.002)
    assert "ref-" not in repr(items)


def test_a_loop_run_plays_its_pieces_on_until_the_next_entry() -> None:
    items = body.x_tracks(body.render_plan(LOOPED, DURATIONS), FACTS)
    assert items[1]["duration"] == pytest.approx(64.0 + 64.0 - 8.0, abs=0.001)


def _producer(references: list[str]) -> Producer:
    songs = [
        SimpleNamespace(swedebeat=SimpleNamespace(reference=r), spotify_id=f"sp{r[-1]}", gap=False)
        for r in ("ref-c", "ref-a", "ref-b")  # the approved list's own order
    ]
    draft = SimpleNamespace(songs=SimpleNamespace(content=SimpleNamespace(songs=songs)))
    return cast(Producer, SimpleNamespace(draft=draft, references=references))


@pytest.mark.parametrize(
    ("references", "ids"),
    [
        (["ref-b", "ref-a", "ref-c"], ["spb", "spa", "spc"]),  # no repeat: the mix's order
        (["ref-a", "ref-b", "ref-a", "ref-c", "ref-a"], ["spa", "spb", "spc"]),
        (["ref-c", "ref-c", "ref-a", "ref-b", "ref-c"], ["spc", "spa", "spb"]),
    ],
)
def test_the_spotify_playlist_lists_each_song_once_in_the_order_of_its_first_piece(
    references: list[str], ids: list[str]
) -> None:
    assert track_ids(_producer(references)) == ids
