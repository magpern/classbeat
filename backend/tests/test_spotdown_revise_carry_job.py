"""SD-P6 stage E (#1303, ADR 0040 and its stage E amendment): spotdown's timing kept through a
``revise_draft``, a ``refit_draft`` and a "Change songs" (``revise_songs``) of a Spotify design,
on the fakes (Spotify, the MOWL pool, the model and ``spotdownfake``, in process; the netguard
refuses any other connection).

- a draft designed on spotdown's sections records which tracks (``music.spotdown``); a revise or
  refit reads exactly those tracks again (verified, as stage D) and records them again, so a chain
  of revisions keeps them; a track the base had on MOWL's sections stays on them;
- a failure in one track (its read ``rate_limited``) changes that track only;
- switched off, spotdown unreachable, a ``5xx`` or an error of any type in each call: the revise's
  music is exactly today's (the step as MOWL gave it) and the revision records no ``spotdown``;
- a change of one song analyses only the new song and carries every kept song's timing.
"""

import asyncio
import copy
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat.agent import track_timing_calc as tt
from classbeat.design import REFIT_DRAFT, REVISE_DRAFT, spotdown_base
from classbeat.design.music import MusicStep
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.songs import REVISE_SONGS, spotdown_carry
from classbeat.songs import kind as songs_kind
from classbeat.songs.song_list import SongListRevision
from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from jobapi import new_key
from revisesongtools import REQUEST, change, current, replace_script, revise_script
from songtools import Rider, SongEnv
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_song_sections_job import designed, fake, hooked, reads, switch

__all__ = ["fake", "hooked", "spotdown"]  # the fixtures

pytestmark = pytest.mark.usefixtures("songenv")

KINDS = (songs_kind.KIND, REVISE_SONGS.name, REVISE_DRAFT.name, REFIT_DRAFT.name)


@pytest.fixture
def carried(monkeypatch: pytest.MonkeyPatch) -> list[tuple[MusicStep, dict[str, Any]]]:
    """Each ``carried_music`` call of a revise or refit: the step it gave back and the analysis
    as MOWL gave it."""
    calls: list[tuple[MusicStep, dict[str, Any]]] = []
    real = spotdown_carry.carried_music

    async def recording(run: Any, step: MusicStep, sections: Any) -> MusicStep:
        before = copy.deepcopy(dict(step.data.analysis))
        out = await real(run, step, sections)
        calls.append((out, before))
        return out

    monkeypatch.setattr(spotdown_carry, "carried_music", recording)
    return calls


async def run_any(songenv: SongEnv, job_id: UUID) -> Any:
    """The job's next attempt with the song kinds and the design revise kinds registered."""
    songenv.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
    lease = songenv.spot.env.settings.job_lease_seconds
    row = await queue.claim(songenv.factory, "sd-e-w", lease, list(KINDS))
    assert row is not None, "nothing to claim"
    registry = KindRegistry()
    for k in (songs_kind.DESIGN_WITH_SONGS, REVISE_SONGS, REVISE_DRAFT, REFIT_DRAFT):
        registry.register(k)
    config = RunConfig(lease_seconds=lease, spotify_calls_per_job=songenv.calls_per_job)
    await execute(Execution(row), songenv.factory, registry, config, asyncio.Event())
    return songenv.row(job_id)


async def revise(songenv: SongEnv, rider: Rider, class_id: UUID, base: UUID) -> Any:
    """A chat revise of ``base``, run to the end: its new revision."""
    songenv.script = revise_script()
    body = {
        "kind": REVISE_DRAFT.name,
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "request": REQUEST,
            "mowl_connection_id": str(rider.mowl_connection_id),
        },
    }
    made = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 201, made.text
    row = await run_any(songenv, UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    return songenv.revision(current(songenv, class_id))


async def refit(songenv: SongEnv, rider: Rider, class_id: UUID, base: UUID) -> Any:
    """A beat-one refit of ``base`` (one correction of track 1), run to the end."""
    music = songenv.revision(base).music
    songenv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, 1, 1, :bpm)",
        o=rider.user_id, c=class_id, f=music["fingerprint"], bpm=music["songs"][0]["bpm"],
    )  # fmt: skip
    body = {
        "kind": REFIT_DRAFT.name,
        "input": {
            "class_id": str(class_id),
            "base_revision_id": str(base),
            "mowl_connection_id": str(rider.mowl_connection_id),
        },
    }
    made = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 201, made.text
    row = await run_any(songenv, UUID(made.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    return songenv.revision(current(songenv, class_id))


def sections(m: dict[str, Any], position: int) -> list[dict[str, Any]]:
    """Track ``position``'s sections, without their numbers (they move with other tracks)."""
    return [{k: v for k, v in s.items() if k != "song"} for s in m["songs"]
            if s["track"] == position + 1]  # fmt: skip


async def spotdown_class(
    songenv: SongEnv, fake: FakeSpotdown, *, rate_limited: int | None = None
) -> tuple[Rider, UUID, Any, dict[str, Any]]:
    """A class designed on spotdown's sections: the rider, the class, its draft and the design's
    map. ``rate_limited``: that position's read at phase 2 is ``rate_limited`` (MOWL's sections)."""

    def before_design() -> None:
        if rate_limited is not None:
            listed = songenv.sql(
                "SELECT provenance FROM class_revisions WHERE kind = 'song_list' "
                "ORDER BY created_at DESC LIMIT 1"
            ).scalar_one()
            analysis = listed[KEY]["songs"][rate_limited]["analysis_id"]
            fake.inject[f"/v2/analyses/{analysis}"] = [
                httpx.Response(429, headers={"Retry-After": "5"})
            ]

    listed, rider = await designed(
        songenv, on_pick=True, on_design=True, before_design=before_design
    )
    draft = songenv.sql(
        "SELECT * FROM class_revisions WHERE kind = 'draft' AND music ->> 'song_list_id' = :l",
        l=str(listed.id),
    ).one()
    return rider, draft.class_id, draft, {}


def design_map(hooked: list[Any]) -> dict[str, Any]:
    _, step, _ = hooked[-1]
    return copy.deepcopy(step.data.analysis["map"])


# --- kept ----------------------------------------------------------------------------------------


async def test_a_revise_and_a_refit_of_it_keep_the_tracks_on_spotdowns_sections(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any], carried: list[Any]
) -> None:
    rider, class_id, draft, _ = await spotdown_class(songenv, fake)
    designed_on = design_map(hooked)
    list_id = draft.music["song_list_id"]
    assert draft.music["spotdown"] == {"song_list_id": list_id, "tracks": list(range(8))}

    first = await revise(songenv, rider, class_id, draft.id)
    [(step, before)] = carried
    m = step.data.analysis["map"]
    assert tt.classes(m) == {n: "approximate" for n in range(8)}
    assert m["songs"] == designed_on["songs"]  # the sections the class was designed on
    assert m["songs"] != before["map"]["songs"]  # not MOWL's
    assert "song_list_id" not in first.music  # a chat revise names no list (as before)
    assert first.music["spotdown"] == draft.music["spotdown"]

    # The same model script would give the same draft (refused), so the next revision of the
    # chain is a refit: its base, a chat revise, names no song_list_id.
    second = await refit(songenv, rider, class_id, first.id)
    step, _ = carried[-1]
    assert step.data.analysis["map"]["songs"] == designed_on["songs"]
    assert second.music["spotdown"] == draft.music["spotdown"]


async def test_a_refit_keeps_them_and_applies_the_correction(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any], carried: list[Any]
) -> None:
    rider, class_id, draft, _ = await spotdown_class(songenv, fake)
    designed_on = design_map(hooked)
    refitted = await refit(songenv, rider, class_id, draft.id)
    [(step, _)] = carried
    assert step.data.analysis["map"]["songs"] == designed_on["songs"]
    assert tt.classes(step.data.analysis["map"]) == {n: "approximate" for n in range(8)}
    assert refitted.music["spotdown"] == draft.music["spotdown"]
    assert refitted.music["beat_one"] == [{"song": 1, "beats": 1}]
    assert refitted.music["song_list_id"] == draft.music["song_list_id"]


async def test_a_track_the_base_had_on_mowls_sections_stays_on_them(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any], carried: list[Any]
) -> None:
    rider, class_id, draft, _ = await spotdown_class(songenv, fake, rate_limited=2)
    designed_on = design_map(hooked)
    assert draft.music["spotdown"]["tracks"] == [0, 1, 3, 4, 5, 6, 7]
    reads_before = len(reads(fake))
    revised = await revise(songenv, rider, class_id, draft.id)  # track 2 readable again now
    [(step, _)] = carried
    m = step.data.analysis["map"]
    assert set(tt.classes(m)) == {0, 1, 3, 4, 5, 6, 7}
    assert m["songs"] == designed_on["songs"]
    assert len(reads(fake)) - reads_before == 7  # never the track the base did not have
    assert revised.music["spotdown"]["tracks"] == [0, 1, 3, 4, 5, 6, 7]


async def test_a_failed_read_of_one_track_changes_that_track_only(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any], carried: list[Any]
) -> None:
    rider, class_id, draft, _ = await spotdown_class(songenv, fake)
    designed_on = design_map(hooked)
    listed = songenv.revision(UUID(draft.music["song_list_id"]))
    analysis = listed.provenance[KEY]["songs"][5]["analysis_id"]
    fake.inject[f"/v2/analyses/{analysis}"] = [httpx.Response(429, headers={"Retry-After": "5"})]
    revised = await revise(songenv, rider, class_id, draft.id)
    [(step, before)] = carried
    m = step.data.analysis["map"]
    assert set(tt.classes(m)) == set(range(8)) - {5}
    assert sections(m, 5) == sections(before["map"], 5)  # MOWL's sections for that track
    for n in set(range(8)) - {5}:
        assert sections(m, n) == sections(designed_on, n)  # every other track unchanged
    assert revised.music["spotdown"]["tracks"] == [0, 1, 2, 3, 4, 6, 7]


# --- exactly today's ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "how",
    ["off", "unreachable", "server_error", "list_error", "other_music", "read_error", "base_error"],
)
async def test_a_fallback_in_a_revise_is_exactly_todays_music(
    songenv: SongEnv,
    fake: FakeSpotdown,
    hooked: list[Any],
    carried: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    rider, class_id, draft, _ = await spotdown_class(songenv, fake)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise KeyError("injected")

    fake.seen.clear()
    if how == "off":
        switch(None)
    elif how == "unreachable":
        fake.connect_error = True
    elif how == "server_error":
        for a in list(fake.analyses):
            fake.inject[f"/v2/analyses/{a}"] = [httpx.Response(503)]
    elif how == "list_error":
        monkeypatch.setattr(spotdown_carry.song_list, "load", boom)
    elif how == "other_music":
        real = spotdown_carry.song_list.load

        async def reordered(*a: Any, **k: Any) -> Any:
            got = await real(*a, **k)
            assert got is not None
            songs = list(reversed(got.content.songs))
            content = got.content.model_copy(update={"songs": songs})
            return SongListRevision(content, got.content_hash, got.music, got.provenance, None)

        monkeypatch.setattr(spotdown_carry.song_list, "load", reordered)
    elif how == "read_error":
        monkeypatch.setattr(spotdown_carry, "spotdown_music", boom)
    else:
        monkeypatch.setattr(spotdown_base.SpotdownSections, "model_validate_json", boom)
    revised = await revise(songenv, rider, class_id, draft.id)
    assert "spotdown" not in revised.music  # designed on today's sections: nothing to carry
    if how in ("off", "base_error"):
        assert carried == []  # nothing read: the music step is today's
        assert fake.seen == []
        return
    if how in ("list_error", "other_music"):
        assert reads(fake) == []  # refused before any analysis is read
    [(step, before)] = carried
    assert step.data.analysis == before  # the step exactly as MOWL gave it
    assert tt.classes(step.data.analysis["map"]) == {}


# --- a change of one song -----------------------------------------------------------------------


async def test_a_change_of_one_song_analyses_it_alone_and_keeps_the_others_timing(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any]
) -> None:
    rider, class_id, draft, _ = await spotdown_class(songenv, fake)
    designed_on = design_map(hooked)
    parent = songenv.revision(UUID(draft.music["song_list_id"]))
    starts_before = sum(1 for s in fake.seen if (s.method, s.path) == ("POST", "/v2/analyses"))
    songenv.script = replace_script()
    answer = await change(rider, class_id, draft.id)
    assert answer.status_code == 201, answer.text
    job_id = UUID(answer.json()["id"])
    row = await run_any(songenv, job_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    listed = songenv.revision(row.approval_revision_id)
    starts = sum(1 for s in fake.seen if (s.method, s.path) == ("POST", "/v2/analyses"))
    # Only slot 2 is analysed: its song and at most 2 refinement spares (the fake's analysis of
    # the new candidates does not fit the slot's BPM), never a kept song.
    assert 1 <= starts - starts_before <= 3
    entries = {e["position"]: e for e in listed.provenance[KEY]["songs"]}
    assert [p for p, e in entries.items() if not e.get("kept")] == [2]
    assert {w["position"] for w in listed.provenance[KEY]["swaps"]} <= {2}
    old = {e["position"]: e for e in parent.provenance[KEY]["songs"]}
    for n in set(range(8)) - {2}:
        assert entries[n] == {**old[n], "kept": True}  # the parent's entry, as it was
    assert [p for p, _, _ in timing_marks(listed.provenance)] == list(range(8))
    assert [s.get("timing") for s in listed.content["songs"]] == ["approximate"] * 8

    digest = bytes(row.approval_hash).hex()
    assert (await rider.approve(job_id, listed.id, digest)).status_code == 200
    songenv.script = revise_script()
    row = await run_any(songenv, job_id)
    assert (row.state, row.error_category) == ("completed", None)
    _, step, _ = hooked[-1]
    m = step.data.analysis["map"]
    kept = set(range(8)) - {2}
    # Every kept song is read again and stays on its sections; slot 2 is stage D's own case (the
    # fake's document of the new candidate is aligned to another track, so it is not verified).
    assert kept <= set(tt.classes(m))
    for n in kept:
        assert sections(m, n) == sections(designed_on, n)  # a kept song's timing unchanged
    revised = songenv.revision(current(songenv, class_id))
    assert revised.music["spotdown"] == {
        "song_list_id": str(listed.id),
        "tracks": sorted(tt.classes(m)),
    }


async def test_a_change_with_spotdown_off_carries_nothing(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any]
) -> None:
    rider, class_id, draft, _ = await spotdown_class(songenv, fake)
    switch(None)
    songenv.script = replace_script()
    answer = await change(rider, class_id, draft.id)
    assert answer.status_code == 201, answer.text
    row = await run_any(songenv, UUID(answer.json()["id"]))
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    listed = songenv.revision(row.approval_revision_id)
    assert KEY not in listed.provenance
    assert [s.get("timing") for s in listed.content["songs"]] == [None] * 8
