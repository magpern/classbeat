"""SD-P6 stage E (#1303, ADR 0040 and its stage E amendment; #1354): spotdown's timing kept through
a ``revise_draft``, a ``refit_draft`` and a "Change songs" (``revise_songs``) of a Spotify design,
on the fakes (Spotify, the MOWL pool, the model and ``spotdownfake``, in process; the netguard
refuses any other connection).

- a draft designed on spotdown's sections records its tracks (``music.spotdown``, all of them); a
  revise or refit reads exactly those tracks again (verified, as stage D) and records them again,
  so a chain of revisions keeps them;
- there is no fallback to MOWL's sections: spotdown unreachable, a ``5xx`` or a ``rate_limited``
  read is ``analysis_unavailable`` (the job is retried), an analysis that is gone is
  ``analysis_failed``, other music than the list's or a list that is gone is ``music_changed``, an
  error of any type in a call is ``internal``; the revision is not made;
- a change of one song analyses only the new song and carries every kept song's timing.
"""

import asyncio
import copy
import logging
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
from classbeat.spotdown import outage_policy
from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from jobapi import new_key
from revisesongtools import REQUEST, change, current, replace_script, revise_script
from songtools import Rider, SongEnv
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_song_analysis_job import ending
from test_spotdown_song_sections_job import designed, fake, hooked

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


async def revise(
    songenv: SongEnv,
    rider: Rider,
    class_id: UUID,
    base: UUID,
    *,
    ends: tuple[str, str] | None = None,
) -> Any:
    """A chat revise of ``base``, run to the end: its new revision. ``ends`` (state, category): the
    attempt ends so instead and the job's row is returned."""
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
    job_id = UUID(made.json()["id"])
    row = await run_any(songenv, job_id)
    if ends is not None:
        assert (row.state, ending(songenv, job_id)) == ends
        return row
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


async def spotdown_class(songenv: SongEnv) -> tuple[Rider, UUID, Any]:
    """A class designed on spotdown's sections: the rider, the class and its draft."""
    listed, rider = await designed(songenv)
    draft = songenv.sql(
        "SELECT * FROM class_revisions WHERE kind = 'draft' AND music ->> 'song_list_id' = :l",
        l=str(listed.id),
    ).one()
    return rider, draft.class_id, draft


def design_map(hooked: list[Any]) -> dict[str, Any]:
    _, step, _ = hooked[-1]
    return copy.deepcopy(step.data.analysis["map"])


# --- kept ----------------------------------------------------------------------------------------


async def test_a_revise_and_a_refit_of_it_keep_the_tracks_on_spotdowns_sections(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any], carried: list[Any]
) -> None:
    rider, class_id, draft = await spotdown_class(songenv)
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
    rider, class_id, draft = await spotdown_class(songenv)
    designed_on = design_map(hooked)
    refitted = await refit(songenv, rider, class_id, draft.id)
    [(step, _)] = carried
    assert step.data.analysis["map"]["songs"] == designed_on["songs"]
    assert tt.classes(step.data.analysis["map"]) == {n: "approximate" for n in range(8)}
    assert refitted.music["spotdown"] == draft.music["spotdown"]
    assert refitted.music["beat_one"] == [{"song": 1, "beats": 1}]
    assert refitted.music["song_list_id"] == draft.music["song_list_id"]


# --- no fallback ----------------------------------------------------------------------------------


def _boom(*_a: Any, **_k: Any) -> Any:
    raise KeyError("injected")


def _reordered(mp: pytest.MonkeyPatch) -> None:
    real = spotdown_carry.song_list.load

    async def reordered(*a: Any, **k: Any) -> Any:
        got = await real(*a, **k)
        assert got is not None
        songs = list(reversed(got.content.songs))
        content = got.content.model_copy(update={"songs": songs})
        return SongListRevision(content, got.content_hash, got.music, got.provenance, None)

    mp.setattr(spotdown_carry.song_list, "load", reordered)


def _gone(mp: pytest.MonkeyPatch) -> None:
    async def none(*_a: Any, **_k: Any) -> Any:
        return None

    mp.setattr(spotdown_carry.song_list, "load", none)


def _status(code: int, **headers: str) -> Any:
    def inject(fake: FakeSpotdown, _mp: pytest.MonkeyPatch) -> None:
        fake.inject[f"/v2/analyses/{sorted(fake.analyses)[5]}"] = [
            httpx.Response(code, headers=headers)
        ]

    return inject


# how the revise fails -> (state after the attempt, category)
FAILURES: dict[str, tuple[Any, str, str]] = {
    "unreachable": (
        lambda f, _m: setattr(f, "connect_error", True),
        "queued",
        "analysis_unavailable",
    ),
    "server_error": (_status(503), "queued", "analysis_unavailable"),
    "rate_limited": (_status(429, **{"Retry-After": "5"}), "queued", "analysis_unavailable"),
    "gone": (_status(404), "failed", "analysis_failed"),
    "list_gone": (lambda _f, m: _gone(m), "failed", "music_changed"),
    "other_music": (lambda _f, m: _reordered(m), "failed", "music_changed"),
    "list_error": (
        lambda _f, m: m.setattr(spotdown_carry.song_list, "load", _boom),
        "failed",
        "internal",
    ),
    "read_error": (
        lambda _f, m: m.setattr(spotdown_carry, "spotdown_music", _boom),
        "failed",
        "internal",
    ),
    "base_error": (
        lambda _f, m: m.setattr(spotdown_base.SpotdownSections, "model_validate_json", _boom),
        "failed",
        "internal",
    ),
}


LOGGED = {"unreachable": "connection", "server_error": "server_error"}  # the reason logged


@pytest.mark.parametrize("how", list(FAILURES))
async def test_a_failure_in_a_revise_has_no_fallback_and_no_revision(
    songenv: SongEnv,
    fake: FakeSpotdown,
    hooked: list[Any],
    carried: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    how: str,
) -> None:
    rider, class_id, draft = await spotdown_class(songenv)
    inject, state, category = FAILURES[how]
    inject(fake, monkeypatch)
    with caplog.at_level(logging.INFO):
        await revise(songenv, rider, class_id, draft.id, ends=(state, category))
    if how in LOGGED:
        assert outage_policy.log_message(LOGGED[how]) in caplog.text  # type: ignore[arg-type]
    assert current(songenv, class_id) == draft.id  # no new revision
    assert carried == [] or all(  # a carry that raised gave no step back
        tt.classes(step.data.analysis["map"]) for step, _ in carried
    )


async def test_a_draft_on_a_list_without_its_spotdown_field_is_music_changed(
    songenv: SongEnv, fake: FakeSpotdown, carried: list[Any]
) -> None:
    """#1386: a base designed on a song list but without ``music.spotdown`` (a draft from before
    the stages) is never revised on MOWL's sections: ``music_changed``, nothing read or stored."""
    rider, class_id, draft = await spotdown_class(songenv)
    songenv.sql("UPDATE class_revisions SET music = music - 'spotdown' WHERE id = :r", r=draft.id)
    reads = len(fake.seen)
    await revise(songenv, rider, class_id, draft.id, ends=("failed", "music_changed"))
    assert current(songenv, class_id) == draft.id  # no new revision
    assert carried == []  # never reached the carry
    assert not [s for s in fake.seen[reads:] if s.path.startswith("/v2/analyses/")]


# --- a change of one song -----------------------------------------------------------------------


async def test_a_change_of_one_song_analyses_it_alone_and_keeps_the_others_timing(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any]
) -> None:
    rider, class_id, draft = await spotdown_class(songenv)
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
