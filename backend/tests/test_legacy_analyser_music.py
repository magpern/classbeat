"""#1299 (migration 0081): ``class_revisions.music`` stored before the ``analyser`` code was
dropped still reads. A SwedeBeat mix draft (``SwedebeatRevisionMusic``) and each studio track
record (``TrackRecord`` in ``StudioRevisionMusic.analyses``) were stored with
``analyser: "spotdown"``; the models are ``extra="forbid"``, so the before-validator
``studio_revision_model.drop_legacy_analyser`` drops that key, and only that value. Read on the
JSON path production uses (``validate_json``, ``model_validate_json`` of ``json.dumps(row.music)``):
Save and approval (``saving.approval.SAVED_MUSIC``), the player send, refit and beat-one
(``design.revision.REVISION_MUSIC``), and the studio's current draft, revise and mix proposal.
Pure: no database."""

import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from classbeat.design.revision import REVISION_MUSIC
from classbeat.design.revision_music_model import SwedebeatRevisionMusic
from classbeat.design.studio_revision_model import StudioRevisionMusic
from classbeat.saving.approval import SAVED_MUSIC

SHA = "c" * 64
LINK = str(uuid4())
PROVENANCE = {"analysis_id": "an-1", "analyzer": {"name": "spotdown"}, "timing": {"class": "exact"}}


def mix_music(**extra: Any) -> dict[str, Any]:
    """A mix draft's ``music`` as stored since #1304 (``exclude_none``), plus ``extra``."""
    return {
        "source": "mowl_swedebeat_mix",
        "mix_id": "mix-1",
        "name": "A mix",
        "duration": 600.0,
        "listed_duration": 600.0,
        "unsure_beat_one": [],
        "fingerprint": SHA,
        "link_id": LINK,
        "provenance": PROVENANCE,
        **extra,
    }


READERS: list[tuple[str, TypeAdapter[Any]]] = [
    ("SAVED_MUSIC", SAVED_MUSIC),
    ("REVISION_MUSIC", REVISION_MUSIC),
]


@pytest.mark.parametrize(("name", "reader"), READERS)
def test_a_mix_draft_stored_with_the_spotdown_analyser_reads_without_it(
    name: str, reader: TypeAdapter[Any]
) -> None:
    current = reader.validate_json(json.dumps(mix_music()))
    legacy = reader.validate_json(json.dumps(mix_music(analyser="spotdown")))
    assert isinstance(legacy, SwedebeatRevisionMusic), name
    assert legacy == current
    assert "analyser" not in legacy.model_dump(mode="json")
    assert legacy.provenance == PROVENANCE


@pytest.mark.parametrize(("name", "reader"), READERS)
@pytest.mark.parametrize("other", ["local", None, "mowl", 1])
def test_a_mix_draft_naming_another_analyser_is_refused(
    name: str, reader: TypeAdapter[Any], other: object
) -> None:
    with pytest.raises(ValidationError):
        reader.validate_json(json.dumps(mix_music(analyser=other)))


def studio_music(record: dict[str, Any]) -> str:
    return json.dumps({
        "source": "swedebeat_studio",
        "song_list_id": LINK,
        "mix_spec": {
            "tracks": [{"reference": "a", "start_sec": 0.0, "end_sec": 60.0}],
            "crossfades": [],
        },
        "map_version": 1,
        "analyses": [record],
    })  # fmt: skip


RECORD = {"reference": "a", "sha256": SHA, "map_hash": SHA, "provenance": PROVENANCE}


def test_a_studio_record_stored_with_the_spotdown_analyser_reads_without_it() -> None:
    current = StudioRevisionMusic.model_validate_json(studio_music(RECORD))
    legacy = StudioRevisionMusic.model_validate_json(
        studio_music({**RECORD, "analyser": "spotdown"})
    )
    assert legacy.analyses == current.analyses
    assert legacy.analyses is not None
    assert "analyser" not in legacy.model_dump(mode="json")["analyses"][0]


@pytest.mark.parametrize("other", ["local", None, "mowl", 1])
def test_a_studio_record_naming_another_analyser_is_refused(other: object) -> None:
    with pytest.raises(ValidationError):
        StudioRevisionMusic.model_validate_json(studio_music({**RECORD, "analyser": other}))
