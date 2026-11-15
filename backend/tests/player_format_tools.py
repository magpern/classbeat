"""Loads the vendored player format (``tests/player_format/README.md``) for the tests."""

import json
import sys
from pathlib import Path
from typing import Any

from classbeat.player_format_model import PlayerClass, PlayerSong

ROOT = Path(__file__).parent / "player_format"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import reference_converter  # noqa: E402  (vendored)
from classformat import check_document, content_hash  # noqa: E402  (vendored)

SCHEMA: dict[str, Any] = json.loads((ROOT / "schema" / "class.schema.json").read_text("utf-8"))
FIXTURES = sorted((ROOT / "fixtures").glob("*.input.json"))


def violations(document: dict[str, Any]) -> list[tuple[str, str]]:
    """Strict schema and semantic violations by the player's own validator."""
    return check_document(SCHEMA, document, "strict")


def reference_document(fixture: dict[str, Any]) -> dict[str, Any]:
    """The reference converter's document, relabelled format 1.1 (the only difference)."""
    return {**reference_converter.convert(fixture), "format_version": "1.1"}


def reference_hash(document: dict[str, Any]) -> str:
    return content_hash(document)


def player_class(fixture: dict[str, Any], reference: dict[str, Any]) -> PlayerClass:
    """The revision of a fixture: its draft, and the song windows the reference computed."""
    music = reference["music"]
    songs = [
        PlayerSong(
            title=s["title"],
            artist=s["artist"],
            length_ms=s["length_ms"],
            start_ms=s["start_ms"],
            end_ms=s["end_ms"],
            bpm=s.get("bpm"),
            spotify_track=s.get("ids", {}).get("spotify_track"),
        )
        for s in reference["songs"]
    ]
    return PlayerClass(
        draft={k: fixture[k] for k in ("name", "description", "segments") if k in fixture},
        sport=fixture.get("sport", "cycling"),
        music_source=music["source"],
        crossfade_ms=music["design_crossfade_ms"],
        songs=songs,
    )
