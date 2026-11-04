"""Beat-one corrections in the draft kinds (ADR 0007 amendment "beat-one corrections", BC2).

Through the jobs API, the worker side, Postgres as ``classbeat_app``, the fake MOWL and
``FakeProvider`` scripts; never the network. Two classes are designed on the same playlist with
the same script, so their drafts are equal; a correction on one class then changes only that
class's chat revision. Corrections of another fingerprint, another class or another user are
never read.
"""

import json
import uuid
from typing import Any
from uuid import UUID

import pytest

from classbeat.agent import beat_one, mix_input, model_input
from classbeat.agent.beat_one import Correction
from classbeat.agent.mix_input import MixData, SpotifyData
from classbeat.agent.model_input import MusicData
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")


def correct(renv: ReviseEnv, owner: UUID, class_id: UUID, fingerprint: str, song: int, beats: int):
    renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, :s, :b, 120)",
        o=owner, c=class_id, f=fingerprint, s=song, b=beats,
    )  # fmt: skip


def seconds(content: dict[str, Any]) -> list[int]:
    return [i["sec"] for s in content["segments"] for i in s["intervals"]]


async def two_classes(renv: ReviseEnv) -> tuple[tuple[UUID, Any], tuple[UUID, Any]]:
    a, a_base = await renv.design()
    b, b_base = await renv.design()
    first_a, first_b = renv.revisions(a)[0], renv.revisions(b)[0]
    assert first_a.content == first_b.content, "same music and script: the same draft"
    assert first_a.music["fingerprint"] == first_b.music["fingerprint"]
    return (a, a_base), (b, b_base)


async def test_a_correction_shifts_its_own_class_only_and_is_recorded(renv: ReviseEnv) -> None:
    (a, a_base), (b, b_base) = await two_classes(renv)
    base = renv.revisions(b)[0]
    fingerprint, songs = base.music["fingerprint"], base.music["songs"]
    for song in songs:  # every song the class plays, half a bar later
        correct(renv, renv.owner, b, fingerprint, song["song"], 2)
    await renv.revise(a, a_base, "revise_good")
    await renv.revise(b, b_base, "revise_good")
    plain, corrected = renv.revisions(a)[1], renv.revisions(b)[1]
    moved = seconds(corrected.content)
    assert seconds(plain.content) != moved, "the corrected beat 1 moves the fitted changes"
    assert sum(seconds(plain.content)) == sum(seconds(corrected.content)), "the total is kept"
    assert corrected.music["beat_one"] == [{"song": s["song"], "beats": 2} for s in songs]
    assert "beat_one" not in plain.music
    assert corrected.music["fingerprint"] == plain.music["fingerprint"], "the music's own hash"
    # The model saw numbers only: the same message shape, no correction as text.
    sent_a, sent_b = renv.providers[2].sent[0].spec, renv.providers[3].sent[0].spec
    message_a, message_b = sent_a.first_message, sent_b.first_message
    for word in ("beat_one", "beat1_shift", "correction"):
        assert word not in message_b
    head_a = json.loads(message_a[message_a.index('{"brief"') :])
    head_b = json.loads(message_b[message_b.index('{"brief"') :])
    assert set(head_a) == set(head_b)
    assert head_a["base_draft"] == head_b["base_draft"]


async def test_corrections_of_other_music_another_class_or_another_user_are_not_read(
    renv: ReviseEnv,
) -> None:
    (a, a_base), (b, b_base) = await two_classes(renv)
    fingerprint = renv.revisions(b)[0].music["fingerprint"]
    correct(renv, renv.owner, b, "f" * 64, 1, 3)  # another fingerprint: inert
    correct(renv, renv.owner, a, "e" * 64, 1, -3)
    other = renv.sql(
        "INSERT INTO users (email, email_normalized, email_verified_at) "
        "VALUES (:e, :e, now()) RETURNING id",
        e=f"other-{uuid.uuid4().hex[:8]}@example.test",
    ).scalar_one()
    other_class = renv.sql(
        "INSERT INTO classes (owner_id, title) VALUES (:o, 'theirs') RETURNING id", o=other
    ).scalar_one()
    correct(renv, other, other_class, fingerprint, 1, 2)  # same music, another user
    await renv.revise(a, a_base, "revise_good")
    await renv.revise(b, b_base, "revise_good")
    first, second = renv.revisions(a)[1], renv.revisions(b)[1]
    assert first.content == second.content
    assert "beat_one" not in first.music
    assert "beat_one" not in second.music


def mix(beat1_margin: float = 1.0) -> dict[str, Any]:
    return {
        "version": 1,
        "duration": 240.0,
        "songs": [
            {"song": 1, "start": 0.0, "end": 240.0, "bpm": 120.0, "first_downbeat": 0.5,
             "first_phrase": 0.5, "phrase_bars": 8, "grid_score": 0.9,
             "beat1_margin": beat1_margin},
        ],
        "events": [{"t": 60.5, "precise": 60.5, "kind": "drop", "strength": 0.8, "song": 1}],
    }  # fmt: skip


def test_the_fitted_map_of_a_mix_carries_its_corrections_and_the_cache_stays() -> None:
    stored = mix()
    data = MixData({"map": stored, "duration": 240.0}, 9001, (), (Correction(1, -1),))
    m = mix_input.music_map(data)
    assert m["songs"][0]["first_downbeat"] == pytest.approx(0.0, abs=1e-3)
    assert stored["songs"][0]["first_downbeat"] == 0.5, "the stored analysis is never changed"
    assert mix_input.unsure_beat_one(m) == [], "a corrected song is no longer an estimate"
    plain = mix_input.music_map(MixData({"map": stored, "duration": 240.0}, 9001))
    assert mix_input.unsure_beat_one(plain) == [0]


def test_a_spotify_map_with_corrections_is_the_plain_map_corrected() -> None:
    from mowl import spotify

    tracks = [{"SpotifyTrackID": "1" * 22, "Title": "t", "Artist": "a", "Duration": 200_000,
               "BPM": 120}]  # fmt: skip
    analysis = {"map": spotify.build_map(tracks, {}), "duration": 200.0}
    plain = mix_input.music_map(MusicData(analysis, 7))
    moved = mix_input.music_map(SpotifyData(analysis, 7, (), (Correction(1, 1),)))
    assert moved == beat_one.apply(plain, [Correction(1, 1)])
    assert moved != plain


def _probe(titles: str, bpm: float = 120.0) -> tuple[Any, Any, dict[str, Any]]:
    """The reviewer's probe: about 112 KB of ``unknown_bpm`` titles, a 200-track playlist whose
    titles are 100 four-byte characters, and a correction for every track."""
    from mowl import spotify

    from classbeat.design import revision

    tracks = [
        {"SpotifyTrackID": f"{n:022d}", "Title": titles, "Artist": "a", "Duration": 60_000,
         "BPM": bpm}
        for n in range(1, 201)
    ]  # fmt: skip

    class Step:
        data = SpotifyData(
            {"map": {}, "duration": 0.0}, 7, (), tuple(Correction(n, 1) for n in range(1, 201))
        )

    music = revision.SpotifyRevisionMusic(
        source="mowl_spotify_playlist",
        link_id=uuid.uuid4(),
        fingerprint="a" * 64,
        unknown_bpm=[revision.UnknownBpmTrack(index=i, title="t" * 200) for i in range(500)],
    )
    return music, Step(), model_input.music_map(spotify.build_map(tracks, {}))


def test_the_size_check_covers_the_new_fields_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from classbeat.design import revision
    from classbeat.jobs import JobFailure

    music, step, m = _probe("\U0001d11e" * 100)  # a 4-byte character, 100 times
    done = revision._beat_one_fields(music, step, m, 100_000)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    new_fields = revision.canonical(
        {"beat_one": done.model_dump(mode="json")["beat_one"],
         "songs": done.model_dump(mode="json")["songs"]}
    )  # fmt: skip
    whole = revision.canonical(done.model_dump(mode="json", exclude_none=True))
    assert len(done.songs or []) == 200
    assert len(done.beat_one or []) == 200
    assert 90_000 < len(new_fields) < revision.MAX_BEAT_ONE_BYTES
    assert len(whole) > revision.MAX_BEAT_ONE_BYTES, "a check of the whole music would refuse it"
    # It validates back as Save reads it.
    assert revision.REVISION_MUSIC.validate_json(whole).songs == done.songs
    monkeypatch.setattr(revision, "MAX_BEAT_ONE_BYTES", len(new_fields) - 1)
    with pytest.raises(JobFailure):
        revision._beat_one_fields(music, step, m, 100_000)  # pyright: ignore[reportPrivateUsage, reportArgumentType]


@pytest.mark.parametrize("bpm", [-5.0, 2000.0, 0.01])
def test_a_song_with_an_unusable_bpm_drops_its_beat_data_and_the_job_completes(
    bpm: float, caplog: pytest.LogCaptureFixture
) -> None:
    from classbeat.design import revision

    music, step, m = _probe("Song", bpm=bpm)
    with caplog.at_level("WARNING", logger="classbeat.design.revision"):
        done = revision._beat_one_fields(music, step, m, 100_000)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    assert done.songs == []
    assert any(getattr(r, "skipped", 0) == 200 for r in caplog.records)


async def test_a_spotify_design_applies_the_corrections_its_class_holds(renv: ReviseEnv) -> None:
    """ADR 0007 amendment item 4: ``design_draft`` reads the class's corrections for its music's
    fingerprint. Class B's rows are set between its job's creation and its run."""
    from classbeat import design
    from designtools import BRIEF
    from jobapi import new_key
    from optools import PLAYLIST

    a, _ = await renv.design()
    first = renv.revisions(a)[0]
    fingerprint, songs = first.music["fingerprint"], first.music["songs"]
    renv.use("good_first_draft")
    body = {
        "kind": design.KIND,
        "input": {
            "title": "Second ride",
            "brief": BRIEF,
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    made = await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 201, made.text
    b = UUID(made.json()["class_id"])
    for song in songs:
        correct(renv, renv.owner, b, fingerprint, song["song"], 2)
    await renv.run(design.KIND)
    [second] = renv.revisions(b)
    assert second.music["fingerprint"] == fingerprint
    assert second.music["beat_one"] == [{"song": s["song"], "beats": 2} for s in songs]
    assert seconds(second.content) != seconds(first.content), "the fit moved"
    assert sum(seconds(second.content)) == sum(seconds(first.content))
