"""A song list's ``timing`` mark (ADR 0040 section 3, issue #1303 stage C): stored only for an
approximate song, so every other list is stored, hashed and shown exactly as before."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.classes.song_list_out import SongOut
from classbeat.songs import song_list
from classbeat.songs.song_list import SongListContent


def content() -> dict[str, Any]:
    songs = tuple(
        ChosenSong(f"TimingTrack{n:011d}", f"Song {n}", "A Band", role, 120.0, 300, True, None)
        for n, role in enumerate(("warmup", "build", "cooldown"))
    )
    made = song_list.build(
        ChosenList(songs, 882, 900, 9.0, "pick", 0, ""), account="spotify-user", provenance={}
    )
    return made.content.model_dump(mode="json")


def test_a_list_without_marks_dumps_and_hashes_as_before() -> None:
    raw = content()
    assert all("timing" not in s for s in raw["songs"])
    parsed = SongListContent.model_validate(raw)
    assert parsed.model_dump(mode="json") == raw
    assert song_list.content_hash(parsed.model_dump(mode="json")) == song_list.content_hash(raw)


def test_an_approximate_song_keeps_its_mark_and_changes_the_hash() -> None:
    raw = content()
    raw["songs"][1]["timing"] = "approximate"
    parsed = SongListContent.model_validate(raw)
    assert [s.timing for s in parsed.songs] == [None, "approximate", None]
    assert parsed.model_dump(mode="json") == raw
    assert song_list.content_hash(raw) != song_list.content_hash(content())


@pytest.mark.parametrize("value", ["exact", "unusable", "APPROXIMATE", True, 1])
def test_any_other_mark_is_refused(value: object) -> None:
    raw = content()
    raw["songs"][0]["timing"] = value
    with pytest.raises(ValidationError):
        SongListContent.model_validate(raw)


def song_out(**more: Any) -> dict[str, Any]:
    base = {"position": 1, "title": "T", "artist": "A", "role": "warmup", "bpm": 120.0}
    out = SongOut(**base, length_sec=200, new=False, **more)
    return json.loads(out.model_dump_json())


def test_the_page_gets_the_mark_only_for_an_approximate_song() -> None:
    assert "timing" not in song_out()
    assert "timing" not in song_out(timing=None)
    assert song_out(timing="approximate")["timing"] == "approximate"
