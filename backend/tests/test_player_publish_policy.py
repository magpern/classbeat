"""``player_publish_policy`` and ``player_class_calc`` (ADR 0034 sections 4 and 5, #1060): pure,
table-driven. The player's every answer to a publication and to the reconciliation's read, the
envelope of a send and of a removal, the request hash and the song windows.
"""

from typing import Any
from uuid import UUID

import pytest

from classbeat.player_class_calc import PickedSong, player_class, song_windows
from classbeat.player_publish_policy import (
    NUMBER_LIMIT,
    REMOVE_KIND,
    SEND_KIND,
    answer,
    envelope,
    publication_hash,
    reconciled,
    valid_number,
)
from player_format_tools import reference_converter

CLASS = UUID(int=42)
OTHER = UUID(int=43)


def stale(n: Any) -> dict[str, Any]:
    return {"error": "stale_publication", "highest_publication_number": n}


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"status": "applied", "highest_publication_number": 3}, ("applied", None)),
        (200, {"status": "repeat", "highest_publication_number": 3}, ("applied", None)),
        (200, {"status": "maybe"}, ("unsure", None)),
        (200, None, ("unsure", None)),
        (401, None, ("unauthorized", None)),
        (409, stale(9), ("ahead", 9)),
        (409, stale(3), ("internal", None)),  # not ahead of our number
        (409, stale(2), ("internal", None)),
        (409, stale(NUMBER_LIMIT), ("internal", None)),
        (409, stale("9"), ("internal", None)),
        (409, stale(True), ("internal", None)),
        (409, {"error": "stale_publication"}, ("internal", None)),
        (
            409,
            {"error": "publication_conflict", "highest_publication_number": 3},
            ("conflict", None),
        ),
        (409, {"error": "too_many_classes"}, ("refused", None)),
        (413, {"error": "document_too_large"}, ("refused", None)),
        (422, {"error": "content_hash_mismatch"}, ("refused", None)),
        (408, None, ("unsure", None)),
        (500, None, ("unsure", None)),
        (503, {"error": "busy"}, ("unsure", None)),
        (302, None, ("unsure", None)),
    ],
)
def test_every_answer_to_a_publication(
    status: int, body: dict[str, Any] | None, expected: tuple[str, int | None]
) -> None:
    judged = answer(status, body, 3)
    assert (judged.answer, judged.ahead_to) == expected


def found(n: Any, *, removed: bool = False, class_id: UUID = CLASS) -> dict[str, Any]:
    return {"source_class_id": str(class_id), "highest_publication_number": n, "removed": removed}


@pytest.mark.parametrize(
    ("status", "body", "removed", "expected"),
    [
        (200, found(4), False, "succeeded"),
        (200, found(4, removed=True), True, "succeeded"),
        (200, found(4, removed=True), False, "inconclusive"),  # same number, other kind
        (200, found(4), True, "inconclusive"),
        (200, found(3), False, "failed"),  # lower: ours never arrived
        (200, found(5), False, "inconclusive"),  # higher: not ours to judge
        (200, found(4, class_id=OTHER), False, "inconclusive"),
        (200, found("4"), False, "inconclusive"),
        (200, found(0), False, "inconclusive"),
        (200, None, False, "inconclusive"),
        (404, {"error": "not_found"}, False, "failed"),
        (404, None, False, "inconclusive"),
        (401, None, False, "inconclusive"),
        (500, None, False, "inconclusive"),
    ],
)
def test_every_answer_to_the_reconciliation(
    status: int, body: dict[str, Any] | None, removed: bool, expected: str
) -> None:
    assert reconciled(status, body, CLASS, 4, removed) == expected


def test_a_send_carries_its_document_and_a_removal_neither() -> None:
    send = envelope(CLASS, 2, {"name": "x"}, "sha256:" + "a" * 64)
    assert send == {
        "source_class_id": str(CLASS),
        "publication_number": 2,
        "removed": False,
        "content_hash": "sha256:" + "a" * 64,
        "document": {"name": "x"},
    }
    assert envelope(CLASS, 3, None, None) == {
        "source_class_id": str(CLASS), "publication_number": 3, "removed": True,
    }  # fmt: skip


def test_the_request_hash_binds_kind_class_number_and_document() -> None:
    base = publication_hash(SEND_KIND, CLASS, 2, "sha256:" + "a" * 64)
    assert base != publication_hash(SEND_KIND, CLASS, 2, "sha256:" + "b" * 64)
    assert base != publication_hash(SEND_KIND, CLASS, 3, "sha256:" + "a" * 64)
    assert base != publication_hash(SEND_KIND, OTHER, 2, "sha256:" + "a" * 64)
    assert publication_hash(REMOVE_KIND, CLASS, 2, None) != publication_hash(
        SEND_KIND, CLASS, 2, None
    )
    assert base == publication_hash(SEND_KIND, CLASS, 2, "sha256:" + "a" * 64)


@pytest.mark.parametrize(
    ("value", "valid"),
    [(1, True), (NUMBER_LIMIT - 1, True), (0, False), (-1, False), (NUMBER_LIMIT, False),
     (True, False), (1.0, False), ("1", False), (None, False)],
)  # fmt: skip
def test_a_valid_number(value: Any, valid: bool) -> None:
    assert valid_number(value) is valid


SONGS = (
    PickedSong("One", "A", 200, 120.0, "1" * 22),
    PickedSong("Two", "B", 180, None, "2" * 22),
    PickedSong("Three", "C", 240, 95.5, "3" * 22),
)


@pytest.mark.parametrize("crossfade_ms", [0, 6000])
def test_the_song_windows_are_the_reference_converters(crossfade_ms: int) -> None:
    class_ms = 560_000
    ours = song_windows(SONGS, crossfade_ms, class_ms)
    theirs = reference_converter._songs(  # pyright: ignore[reportPrivateUsage]
        [
            {"title": s.title, "artist": s.artist, "length_sec": s.length_sec, "bpm": s.bpm,
             "spotify_id": s.spotify_id}
            for s in SONGS
        ],
        crossfade_ms,
        class_ms,
    )  # fmt: skip
    assert [(w.length_ms, w.start_ms, w.end_ms) for w in ours] == [
        (t["length_ms"], t["start_ms"], t["end_ms"]) for t in theirs
    ]


def test_a_soundless_class_has_no_crossfade_and_no_songs() -> None:
    draft = {"name": "x", "segments": []}
    made = player_class(draft, "cycling", "none", 8.0, SONGS)
    assert (made.crossfade_ms, made.songs) == (0, ())
