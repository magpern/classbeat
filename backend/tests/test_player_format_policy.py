"""PC-4 (#1057): a revision the player format cannot express is refused with a reason."""

import ast
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import classbeat
from classbeat.player_format_model import PlayerClass, PlayerSong
from classbeat.player_format_policy import Converted, Refusal, convert, refusal
from player_format_tools import FIXTURES, player_class, reference_document, violations

FIXTURE = json.loads(FIXTURES[0].read_text("utf-8"))
BASE = player_class(FIXTURE, reference_document(FIXTURE))
DRAFT: dict[str, Any] = dict(BASE.draft)


def _with_interval(**change: Any) -> PlayerClass:
    segments = json.loads(json.dumps(DRAFT["segments"]))
    segments[0]["intervals"][0].update(change)
    return replace(BASE, draft={**DRAFT, "segments": segments})


def _song(index: int, **change: Any) -> PlayerClass:
    songs = list(BASE.songs)
    songs[index] = replace(songs[index], **change)
    return replace(BASE, songs=songs)


CASES: list[tuple[str, PlayerClass]] = [
    ("no_intervals", replace(BASE, draft={**DRAFT, "segments": []})),
    (
        "no_intervals",
        replace(
            BASE, draft={**DRAFT, "segments": [{"name": "x", "kind": "main", "intervals": []}]}
        ),
    ),
    ("no_title", replace(BASE, draft={**DRAFT, "name": "   "})),
    ("description_too_long", replace(BASE, draft={**DRAFT, "description": "x" * 2001})),
    ("zone_out_of_range", _with_interval(zone=8)),
    ("interval_length", _with_interval(sec=0)),
    ("cue_too_long", _with_interval(cue="c" * 121)),
    ("cadence_out_of_range", _with_interval(rpm=[0, 90])),
    ("cadence_out_of_range", _with_interval(rpm=[80, 301])),
    ("class_too_long", _with_interval(sec=86_400)),
    ("zone_out_of_range", replace(_with_interval(zone=6), sport="running")),
    ("songs_on_soundless", replace(BASE, music_source="none")),
    ("no_songs", replace(BASE, songs=[])),
    ("crossfade", replace(BASE, crossfade_ms=30_001)),
    ("song_text", _song(0, artist="  ")),
    ("song_window", _song(0, end_ms=BASE.songs[0].start_ms)),
    ("song_window", _song(0, length_ms=1)),
    ("song_id", _song(0, spotify_track="not-a-track-id")),
    ("song_gap", _song(1, start_ms=BASE.songs[1].start_ms + 1)),
    ("songs_length", replace(BASE, songs=BASE.songs[:-1])),
]


def test_the_base_revision_converts() -> None:
    assert isinstance(convert(BASE), Converted)
    assert refusal(BASE) is None


@pytest.mark.parametrize(("code", "cls"), CASES, ids=[f"{c}-{i}" for i, (c, _) in enumerate(CASES)])
def test_an_inexpressible_revision_is_refused_with_a_reason(code: str, cls: PlayerClass) -> None:
    found = convert(cls)
    assert isinstance(found, Refusal)
    assert found.code == code
    assert found.reason.endswith(".")
    assert len(found.reason) > 15
    assert refusal(cls) == found


def test_a_class_title_over_80_characters_is_cut_not_refused() -> None:
    done = convert(replace(BASE, draft={**DRAFT, "name": "T" * 100}))
    assert isinstance(done, Converted)
    assert done.document["class"]["title"] == "T" * 80
    assert violations(done.document) == []


def test_a_bpm_outside_the_format_is_left_out_not_refused() -> None:
    done = convert(_song(0, bpm=400.0))
    assert isinstance(done, Converted)
    assert "bpm" not in done.document["songs"][0]
    assert violations(done.document) == []


def test_a_swedebeat_song_carries_its_mix_id() -> None:
    song = PlayerSong("T", "A", 1000, 0, 1000, swedebeat_mix="mix-1")
    cls = PlayerClass(
        draft={
            "name": "n",
            "segments": [
                {
                    "name": "s",
                    "kind": "main",
                    "intervals": [{"sec": 1, "zone": 2, "rpm": "beat", "standing": False}],
                }
            ],
        },
        sport="cycling",
        music_source="swedebeat",
        crossfade_ms=0,
        songs=[song],
    )
    done = convert(cls)
    assert isinstance(done, Converted)
    assert done.document["songs"][0]["ids"] == {"swedebeat_mix": "mix-1"}
    assert violations(done.document) == []


def test_the_player_modules_import_no_io() -> None:
    forbidden = (
        "sqlalchemy", "httpx", "fastapi", "socket", "subprocess", "os", "pathlib", "requests",
        "urllib", "classbeat.db", "classbeat.egress", "psycopg", "anthropic",
    )  # fmt: skip
    src = Path(classbeat.__file__).parent
    for name in ("player_format_model", "player_hash_calc", "player_format_policy"):
        tree = ast.parse((src / f"{name}.py").read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in forbidden, (name, module)
                assert not module.startswith(("classbeat.db", "classbeat.egress")), (name, module)
