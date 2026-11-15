"""The catalogue reference and the download's pure rules (ADR 0036 sections 3.4 and 8, SR C2):
``track_policy``, the match parser keeping ``reference`` and ``bpm`` (an invalid reference makes the
song missing), and the stored match: a revision stored before SR C2, without either, still loads.
No I/O."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.swedebeat_gateway.match_policy import Item, Track, parse_match
from classbeat.swedebeat_gateway.track_policy import (
    DOWNLOAD_MAX,
    declared_length,
    download_category,
    download_path,
    is_audio_mpeg,
    is_identity,
    is_reference,
)
from classbeat.swedebeat_match.revision import MatchContent, content_hash
from classbeat.swedebeat_match.verdict_policy import Verdict, verdict

GOOD = ["a", "Z", "0", "_", "-", "A" * 64, "abc_DEF-123", "ref-4f0336f0ea292c50"]
BAD: list[object] = [
    "", "A" * 65, "a b", "a/b", "../x", "a.mp3", "räf", "a\n", "a\x00", "%2F", "a?x=1", "a#b",
    None, 12, b"abc", ["a"],
]  # fmt: skip


@pytest.mark.parametrize("value", GOOD)
def test_a_reference_is_1_to_64_letters_digits_underscores_or_hyphens(value: str) -> None:
    assert is_reference(value)
    assert download_path(value) == f"/tracks/{value}/download"


@pytest.mark.parametrize("value", BAD)
def test_anything_else_is_no_reference(value: object) -> None:
    assert not is_reference(value)
    with pytest.raises(ValueError, match="reference"):
        download_path(value)  # pyright: ignore[reportArgumentType]


def test_the_cap_is_60_mb() -> None:
    assert DOWNLOAD_MAX == 60 * 1024 * 1024


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("audio/mpeg", True), ("AUDIO/MPEG", True), (" audio/mpeg ; x=y", True),
        ("audio/mp3", False), ("audio/mpeg3", False), ("application/json", False),
        ("text/html; audio/mpeg", False), ("", False), (None, False),
    ],
)  # fmt: skip
def test_only_audio_mpeg_is_audio(value: str | None, ok: bool) -> None:
    assert is_audio_mpeg(value) is ok


@pytest.mark.parametrize(
    ("values", "ok"),
    [
        ([], True), (["identity"], True), (["", " Identity "], True),
        (["gzip"], False), (["identity, gzip"], False), (["identity", "br"], False),
    ],
)  # fmt: skip
def test_only_identity_encoding_is_taken(values: list[str], ok: bool) -> None:
    assert is_identity(values) is ok


def test_a_declared_length_is_digits_or_refused() -> None:
    assert declared_length(None) is None
    assert declared_length(" 417 ") == 417
    for bad in ("-1", "1e3", "0x10", "", "\uff11\uff12", "9" * 16):
        with pytest.raises(ValueError, match="Content-Length"):
            declared_length(bad)


@pytest.mark.parametrize(
    ("status", "unknown_client", "category"),
    [
        (200, False, None),
        (401, True, "app_misconfigured"),
        (401, False, "bearer_refused"),
        (403, False, "app_misconfigured"),
        (404, False, "track_unavailable"),
        (413, False, "track_too_large"),
        (429, False, "rate_limited"),
        (502, False, "upstream_unavailable"),
        (500, False, "upstream_unavailable"),
        (302, False, "redirect"),
        (422, False, "invalid_response"),
        (201, False, "invalid_response"),
    ],
)
def test_the_download_status_table(status: int, unknown_client: bool, category: str) -> None:
    assert download_category(status, unknown_client=unknown_client) == category


# --- The match keeps the reference, the duration and the BPM ------------------------------


def _answer(track: dict[str, Any] | None, status: str = "found") -> bytes:
    return json.dumps({"items": [{"status": status, "score": 0.9, "track": track}]}).encode()


def _track(**more: Any) -> dict[str, Any]:
    return {"reference": "sb_ref-1", "title": "T", "artist": "A", "duration": 200.4} | more


def test_the_parser_keeps_the_reference_duration_and_bpm() -> None:
    [item] = parse_match(_answer(_track(bpm=127.96, url="https://x.example/a.mp3")), 1) or []
    assert item == Item("found", 0.9, Track("T", "A", 200, "sb_ref-1", 128.0))
    assert verdict(item) == Verdict("found", "T", "A", 200, "sb_ref-1", 128.0)


@pytest.mark.parametrize("bpm", [None, 0, 0.5, 400.1, "128", True, float("nan")])
def test_a_bpm_outside_1_to_400_is_none(bpm: object) -> None:
    raw = json.dumps({"items": [{"status": "found", "score": 0.9, "track": _track(bpm=bpm)}]})
    [item] = parse_match(raw.encode(), 1) or []
    assert item.track is not None and item.track.bpm is None  # noqa: PT018


@pytest.mark.parametrize("reference", [*BAD[:12], None, 12])
def test_an_invalid_or_absent_reference_makes_the_song_missing(reference: object) -> None:
    track = _track(reference=reference)
    [item] = parse_match(_answer(track), 1) or []
    assert item.track is None
    assert verdict(item) == Verdict("missing")
    no_key = {k: v for k, v in _track().items() if k != "reference"}
    [item] = parse_match(_answer(no_key), 1) or []
    assert verdict(item) == Verdict("missing")


# --- The stored match: before SR C2 and after ----------------------------------------------


def _content(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "v": 1,
        "song_list_hash": "ab" * 32,
        "songs": [
            {"position": 1, "status": "found", "wanted": {"title": "T", "artist": "A"},
             "swedebeat": hit},
        ],
        "found": 1, "unsure": 0, "missing": 0, "swedebeat_sec": 200,
    }  # fmt: skip


def test_a_match_stored_before_sr_c2_still_loads_and_hashes_the_same() -> None:
    stored = _content({"title": "T", "artist": "A", "duration": 200})
    digest = content_hash(stored)
    content = MatchContent.model_validate(stored)
    hit = content.songs[0].swedebeat
    assert hit is not None and (hit.reference, hit.bpm) == (None, None)  # noqa: PT018
    assert content_hash(stored) == digest  # the load re-checks the hash of the stored JSON


def test_a_match_with_the_reference_round_trips() -> None:
    stored = _content(
        {"title": "T", "artist": "A", "duration": 200, "reference": "r-1", "bpm": 128}
    )
    content = MatchContent.model_validate(json.loads(json.dumps(stored)))
    dumped = content.model_dump(mode="json")
    assert dumped["songs"][0]["swedebeat"] == {
        "title": "T", "artist": "A", "duration": 200, "reference": "r-1", "bpm": 128.0,
    }  # fmt: skip
    assert MatchContent.model_validate(json.loads(json.dumps(dumped))) == content


@pytest.mark.parametrize(
    "extra",
    [{"reference": "../x"}, {"reference": ""}, {"reference": "a" * 65}, {"reference": 1},
     {"bpm": 0}, {"bpm": 401}, {"bpm": "128"}, {"url": "https://x.example"}],
)  # fmt: skip
def test_a_stored_hit_with_a_bad_reference_bpm_or_another_field_does_not_load(
    extra: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        MatchContent.model_validate(
            _content({"title": "T", "artist": "A", "duration": 200} | extra)
        )
