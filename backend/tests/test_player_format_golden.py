"""PC-4 (#1057): the golden revisions convert to documents the player accepts, with its hash."""

import json
from typing import Any

import pytest

from classbeat.player_format_model import to_player_document
from classbeat.player_format_policy import Converted, convert
from classbeat.player_hash_calc import content_hash
from player_format_tools import (
    FIXTURES,
    player_class,
    reference_document,
    reference_hash,
    violations,
)

RUNNING: dict[str, Any] = {
    "name": "  Treadmill   intervals ",
    "sport": "running",
    "segments": [
        {
            "name": "Warm up",
            "kind": "warmup",
            "intervals": [{"sec": 300, "zone": 1, "cue": "Easy"}],
        },
        {
            "name": "Main",
            "kind": "main",
            "intervals": [
                {"sec": 120, "zone": 4, "gradient": 1, "cue": "Up\n the hill"},
                {"sec": 60, "zone": 2, "gradient": 0},
            ],
        },
    ],
}
SOUNDLESS: dict[str, Any] = {
    "name": "Quiet ride",
    "segments": [
        {
            "name": "Main",
            "kind": "main",
            "intervals": [
                {"sec": 60, "zone": 2, "rpm": [80, 90], "standing": False},
                {"sec": 180, "zone": 7, "rpm": [100, 110], "standing": True, "ftp_test": 3},
            ],
        }
    ],
}


def _golden() -> list[tuple[str, dict[str, Any]]]:
    return [
        (p.name.removesuffix(".input.json"), json.loads(p.read_text("utf-8"))) for p in FIXTURES
    ]


GOLDEN = [*_golden(), ("running", RUNNING), ("soundless", SOUNDLESS)]
IDS = [name for name, _ in GOLDEN]


def test_the_three_player_fixtures_are_all_there() -> None:
    assert len(FIXTURES) == 3


@pytest.mark.parametrize(("name", "fixture"), GOLDEN, ids=IDS)
def test_every_golden_revision_is_valid_against_the_strict_schema(
    name: str, fixture: dict[str, Any]
) -> None:
    reference = reference_document(fixture)
    done = convert(player_class(fixture, reference))
    assert isinstance(done, Converted), name
    assert done.document["format_version"] == "1.1"
    assert violations(done.document) == []


@pytest.mark.parametrize(("name", "fixture"), GOLDEN, ids=IDS)
def test_the_document_and_hash_equal_the_reference_converters(
    name: str, fixture: dict[str, Any]
) -> None:
    reference = reference_document(fixture)
    done = convert(player_class(fixture, reference))
    assert isinstance(done, Converted), name
    assert done.document == reference
    assert done.content_hash == reference_hash(reference)
    assert done.content_hash.startswith("sha256:")
    assert len(done.content_hash) == len("sha256:") + 64


def test_the_hash_ignores_key_order_and_changes_with_content() -> None:
    reference = reference_document(GOLDEN[0][1])
    reordered = dict(reversed(list(reference.items())))
    assert content_hash(reordered) == content_hash(reference) == reference_hash(reference)
    changed = {**reference, "class": {**reference["class"], "title": "Another"}}
    assert content_hash(changed) != content_hash(reference)


def test_the_game_is_dropped_and_the_ftp_test_kept() -> None:
    fixture = dict(GOLDEN)["class-40min-game-and-ftp-test"]
    done = convert(player_class(fixture, reference_document(fixture)))
    assert isinstance(done, Converted)
    assert all("game" not in iv for iv in done.document["intervals"])
    assert [iv["test"]["minutes"] for iv in done.document["intervals"] if "test" in iv] == [20]


def test_a_soundless_class_has_no_songs_and_missing_timing() -> None:
    done = convert(player_class(SOUNDLESS, reference_document(SOUNDLESS)))
    assert isinstance(done, Converted)
    assert done.document["music"] == {
        "source": "none",
        "design_crossfade_ms": 0,
        "song_timing": "missing",
    }
    assert done.document["songs"] == []


def test_the_model_alone_maps_without_deciding() -> None:
    fixture = GOLDEN[0][1]
    cls = player_class(fixture, reference_document(fixture))
    assert to_player_document(cls) == reference_document(fixture)
