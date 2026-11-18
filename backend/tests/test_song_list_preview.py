"""The composer's ``song_list`` content ``v: 3`` with its ``preview`` (ADR 0038 section 5, #1188):
``v: 1`` and ``v: 2`` still parse and hash as before; ``v: 3`` needs the preview, which must be of
these songs and parse as a draft; the hash covers the preview; ``song_times`` reads each song's
span from an analysis map; and the maximum-size case either fits in 64 KB as Postgres measures it
or fails ``invalid_output`` in ``build``, before any insert. No network, no model."""

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.design.revision import MAX_CONTENT_BYTES, canonical
from classbeat.jobs import JobFailure
from classbeat.songs import song_list
from classbeat.songs.preview_model import Preview, PreviewMusic, PreviewSong, song_times

RUN = uuid.UUID(int=5)


def ids(n: int) -> list[str]:
    return [f"PreviewTrack{i:010d}" for i in range(n)]


def chosen(n: int = 3, *, title: str = "Song", artist: str = "A Band") -> ChosenList:
    roles = ["warmup", *(["climb"] * (n - 2)), "cooldown"] if n > 2 else ["warmup"] * n
    songs = tuple(
        ChosenSong(tid, f"{title} {i}"[:200], artist, role, 120.0, 300, True, None)
        for i, (tid, role) in enumerate(zip(ids(n), roles, strict=True))
    )
    return ChosenList(songs, 300 * n - 9 * (n - 1), 300 * n, 9.0, "pick", 0, "")


def draft(segments: int = 1, intervals: int = 1, cue: str = "") -> dict[str, Any]:
    return {
        "name": "Hills",
        "description": "",
        "segments": [
            {
                "name": f"Part {s}",
                "kind": "main",
                "intervals": [
                    {"sec": 60, "zone": 3, "rpm": [80, 90], "standing": False, "cue": cue}
                    for _ in range(intervals)
                ],
            }
            for s in range(segments)
        ],
    }


def preview(n: int = 3, content: dict[str, Any] | None = None) -> Preview:
    return Preview(
        content=content or draft(),
        music=PreviewMusic(
            fingerprint=song_list.fingerprint(tuple(ids(n))),
            songs=[PreviewSong(start=291.0 * i, end=291.0 * i + 300.0) for i in range(n)],
        ),
        design_run_id=RUN,
    )


def built(p: Preview | None, n: int = 3) -> song_list.SongListRevision:
    return song_list.build(chosen(n), account="spotify-user", provenance={}, preview=p)


def test_v1_and_v2_still_parse_and_hash_as_stored() -> None:
    v2 = built(None).content.model_dump(mode="json")
    assert v2["v"] == 2
    assert "preview" not in v2
    v1 = {
        **v2,
        "v": 1,
        "songs": [{k: x for k, x in s.items() if k != "cover"} for s in v2["songs"]],
    }
    assert song_list.SongListContent.model_validate(v1).preview is None
    parsed = song_list.SongListContent.model_validate(v2)
    assert song_list.content_hash(parsed.model_dump(mode="json")) == song_list.content_hash(v2)


def test_v3_is_the_list_with_a_preview_and_only_v3_has_one() -> None:
    made = built(preview())
    dumped = made.content.model_dump(mode="json")
    assert dumped["v"] == 3
    assert dumped["preview"]["design_run_id"] == str(RUN)
    assert dumped["preview"]["music"]["fingerprint"] == made.music.fingerprint
    again = song_list.SongListContent.model_validate(json.loads(json.dumps(dumped)))
    assert again == made.content  # read back from JSON as stored
    with pytest.raises(ValidationError):
        song_list.SongListContent.model_validate({**dumped, "v": 2})
    without = {k: x for k, x in dumped.items() if k != "preview"}
    with pytest.raises(ValidationError):
        song_list.SongListContent.model_validate(without)  # v: 3 needs its preview


def test_a_preview_of_other_songs_or_not_a_draft_is_refused() -> None:
    dumped = built(preview()).content.model_dump(mode="json")
    other = json.loads(json.dumps(dumped))
    other["preview"]["music"]["fingerprint"] = song_list.fingerprint(tuple(reversed(ids(3))))
    broken = json.loads(json.dumps(dumped))
    broken["preview"]["content"] = {"name": "x"}
    for raw in (other, broken):
        with pytest.raises(ValidationError):
            song_list.SongListContent.model_validate(raw)
    with pytest.raises(JobFailure) as failed:
        built(preview(n=2))  # a preview of two songs for a list of three
    assert failed.value.category == "invalid_output"


def test_the_hash_covers_the_preview() -> None:
    base = built(preview())
    changed_design = built(preview(content=draft(segments=2)))
    p = preview()
    moved = p.model_copy(
        update={
            "music": p.music.model_copy(
                update={"songs": [*p.music.songs[:2], PreviewSong(start=600.0, end=880.0)]}
            )
        }
    )
    changed_times = built(moved)
    other_run = built(p.model_copy(update={"design_run_id": uuid.UUID(int=6)}))
    hashes = {x.content_hash for x in (base, changed_design, changed_times, other_run)}
    assert len(hashes) == 4
    assert base.track_ids == changed_design.track_ids  # the same songs: only the design differs
    assert built(None).content_hash not in hashes


def test_song_times_is_each_tracks_span_in_play_order() -> None:
    analysis = {
        "tracks": [{"track": 1}, {"track": 2}],
        "songs": [
            {"track": 1, "start": 0.0, "end": 100.0},
            {"track": 1, "start": 100.0, "end": 241.0},
            {"track": 2, "start": 232.0, "end": 400.5},
        ],
    }
    assert song_times(analysis) == [
        PreviewSong(start=0.0, end=241.0),
        PreviewSong(start=232.0, end=400.5),
    ]
    with pytest.raises(ValueError, match="without a section"):
        song_times({"tracks": [{"track": 1}, {"track": 2}], "songs": analysis["songs"][:2]})


def maximum(cue: str, segments: int, intervals: int) -> song_list.SongListRevision:
    """40 songs with 200-character titles and artists, and a draft of the given size."""
    n = 40
    roles = ["warmup", *(["climb"] * (n - 2)), "cooldown"]
    songs = tuple(
        ChosenSong(tid, "T" * 200, "Ä" * 200, role, 120.0, 300, True, None)
        for tid, role in zip(ids(n), roles, strict=True)
    )
    chose = ChosenList(songs, 300 * n - 9 * (n - 1), 300 * n, 9.0, "pick", 0, "N" * 300)
    return song_list.build(
        chose,
        account="spotify-user",
        provenance={},
        preview=preview(n, draft(segments, intervals, cue)),
    )


def test_the_schema_maxima_fail_invalid_output_before_any_insert() -> None:
    with pytest.raises(JobFailure) as failed:
        maximum("C" * 120, segments=20, intervals=60)  # the draft schema's maxima
    assert failed.value.category == "invalid_output"


def test_a_list_that_fits_fits_postgres_check_too(database_url: str) -> None:
    """The largest list ``build`` accepts here is within ``octet_length(content::text)``, the
    check of migration 0004, measured by Postgres itself (``jsonb`` prints spaces the canonical
    JSON has none of, so the canonical size alone is not enough)."""
    fits = None
    for intervals in range(60, 0, -1):
        try:
            fits = maximum("C" * 120, segments=5, intervals=intervals)
        except JobFailure as failure:
            refused = failure.category
            assert refused == "invalid_output"
            continue
        break
    assert fits is not None
    dumped = fits.content.model_dump(mode="json")
    engine = create_engine(database_url)
    try:
        with engine.connect() as db:
            size = db.execute(
                text("SELECT octet_length(CAST(:c AS jsonb)::text)"),
                {"c": json.dumps(dumped, sort_keys=True, ensure_ascii=False)},
            ).scalar_one()
    finally:
        engine.dispose()
    assert size == song_list.stored_bytes(dumped)
    assert len(canonical(dumped)) < size <= MAX_CONTENT_BYTES
    assert size > MAX_CONTENT_BYTES - 4096  # the search really found the edge


def test_a_preview_with_another_number_of_spans_is_refused() -> None:
    """#1188 review: the fingerprint of these songs and one span per song, never fewer."""
    p = preview()
    short = p.model_copy(update={"music": p.music.model_copy(update={"songs": p.music.songs[:2]})})
    with pytest.raises(JobFailure) as failed:
        built(short)
    assert failed.value.category == "invalid_output"
    dumped = built(p).content.model_dump(mode="json")
    dumped["preview"]["music"]["songs"] = dumped["preview"]["music"]["songs"][:2]
    with pytest.raises(ValidationError):
        song_list.SongListContent.model_validate(dumped)
