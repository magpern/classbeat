"""#1299 (the timing class and provenance made mandatory): what ``class_revisions.music`` stored
before them still reads, on the JSON path production uses (``model_validate_json`` and
``validate_json`` of ``json.dumps(row.music)``), and what can no longer be stored is refused.

- A studio draft stored before #1302 has no ``analyses`` (absent; ``null`` once a user edit
  re-dumped it): it reads as an empty list (``studio_revision_model.legacy_analyses``), so it wants
  no row and its tracks read as gone, as before.
- A studio track record is always spotdown's, with its provenance: one without it is refused.
- A SwedeBeat mix draft stored before #1304 (a local map) has no ``provenance``: Save, the player
  send and refit still read it (the one legacy read left on ``SwedebeatRevisionMusic``).

Pure: no database."""

import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from classbeat.design.revision import REVISION_MUSIC
from classbeat.design.revision_music_model import SwedebeatRevisionMusic
from classbeat.design.studio_revision_model import StudioRevisionMusic, Wanted, wanted
from classbeat.saving.approval import SAVED_MUSIC

SHA = "d" * 64
LIST_ID = str(uuid4())
PROVENANCE = {"analysis_id": "an-1", "timing": {"class": "exact"}}
RECORD = {"reference": "a", "sha256": SHA, "map_hash": SHA, "provenance": PROVENANCE}


def studio_music(**fields: Any) -> dict[str, Any]:
    return {
        "source": "swedebeat_studio",
        "song_list_id": LIST_ID,
        "mix_spec": {
            "tracks": [{"reference": "a", "start_sec": 0.0, "end_sec": 60.0}],
            "crossfades": [],
        },
        "map_version": 1,
        **fields,
    }


@pytest.mark.parametrize("stored", [{}, {"analyses": None}], ids=["absent", "null"])
def test_a_studio_draft_stored_before_1302_reads_with_no_analyses(stored: dict[str, Any]) -> None:
    music = StudioRevisionMusic.model_validate_json(json.dumps(studio_music(**stored)))
    assert music.analyses == []
    assert wanted(music, ["a"]) == {}  # wants no row: its tracks read as gone (music_changed)
    assert music.model_dump(mode="json")["analyses"] == []  # a user edit stores the list


def test_a_studio_draft_with_its_records_wants_exactly_them() -> None:
    music = StudioRevisionMusic.model_validate_json(json.dumps(studio_music(analyses=[RECORD])))
    assert wanted(music, ["a", "b"]) == {"a": Wanted(SHA, SHA)}


@pytest.mark.parametrize(
    "record",
    [
        {k: v for k, v in RECORD.items() if k != "provenance"},
        {**RECORD, "provenance": None},
    ],
    ids=["absent", "null"],
)
def test_a_studio_record_without_its_provenance_is_refused(record: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StudioRevisionMusic.model_validate_json(json.dumps(studio_music(analyses=[record])))


@pytest.mark.parametrize("analyses", ["a", 1, {"a": RECORD}])
def test_analyses_of_another_shape_stay_refused(analyses: object) -> None:
    with pytest.raises(ValidationError):
        StudioRevisionMusic.model_validate_json(json.dumps(studio_music(analyses=analyses)))


def mix_music(**extra: Any) -> dict[str, Any]:
    """A mix draft's ``music`` as stored before #1304 (``exclude_none``, a local map)."""
    return {
        "source": "mowl_swedebeat_mix",
        "mix_id": "mix-1",
        "name": "A mix",
        "duration": 600.0,
        "listed_duration": 600.0,
        "unsure_beat_one": [],
        "fingerprint": SHA,
        "link_id": str(uuid4()),
        **extra,
    }


@pytest.mark.parametrize(("name", "reader"), [("SAVED", SAVED_MUSIC), ("REVISION", REVISION_MUSIC)])
def test_a_mix_draft_stored_before_1304_still_reads(name: str, reader: TypeAdapter[Any]) -> None:
    legacy = reader.validate_json(json.dumps(mix_music()))
    assert isinstance(legacy, SwedebeatRevisionMusic), name
    assert legacy.provenance is None
    current = reader.validate_json(json.dumps(mix_music(provenance=PROVENANCE)))
    assert current.provenance == PROVENANCE
