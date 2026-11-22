"""spotdown's stages A, B and D for a ``design_draft`` on the user's MOWL playlist, and its revise
and refit (ADR 0040 amendment "spotdown is the only analysis path", items 3 to 5; #1407), as jobs
on the fakes: the fake MOWL (``designtools``) and the fake spotdown (``spotdownfake``, in process;
a track's match status is a ``[<status>]`` token in its title, a job's end a source's
``ends_by_source``). No model but ``FakeProvider``, no network, no real account.

- every track of the playlist is matched, analysed and read again (stage D), and designed on
  spotdown's sections only; the draft keeps its own record (``music.spotdown.record``);
- a track spotdown cannot match or time has no spare: ``analysis_failed``, no revision;
- spotdown unreachable (each kind) ends in its ``outage_policy`` category with its line logged;
- a revise and a refit read the same analyses again (the record carry) and keep the field; a
  playlist that changed since is ``music_changed``; a base without the field is ``music_changed``
  (never MOWL's sections);
- an error of any type in the stages is ``internal``: never swallowed into MOWL's sections.
"""

import asyncio
import copy
import json
import logging
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat import design, egress_spotdown
from classbeat.agent import track_timing_calc
from classbeat.design import DESIGN_DRAFT, REFIT_DRAFT, REVISE_DRAFT
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.songs import draft_spotdown, playlist_spotdown, spotdown_carry
from classbeat.songs import phase2_spotdown as stage_d
from classbeat.spotdown import outage_policy
from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from classbeat.spotdown.song_match_record_model import KEY as MATCH_KEY
from designtools import BRIEF, FakeMowlMusic
from jobapi import new_key
from optools import PLAYLIST
from revisetools import ReviseEnv
from songanalysisdocs import youtube_doc
from spotdownfake import FakeSpotdown

TRACKS = 8  # ``synthetic_tracks``' default
ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, LookupError)


async def run_any(renv: ReviseEnv, job_id: UUID) -> Any:
    registry = KindRegistry()
    for kind in (DESIGN_DRAFT, REVISE_DRAFT, REFIT_DRAFT):
        registry.register(kind)
    settings = renv.denv.app.settings
    kind = renv.denv.ops.row(job_id).kind
    row = await queue.claim(renv.denv.ops.factory, "sd-w", settings.job_lease_seconds, [kind])
    assert row is not None
    await execute(
        Execution(row),
        renv.denv.ops.factory,
        registry,
        RunConfig(lease_seconds=settings.job_lease_seconds),
        asyncio.Event(),
    )
    return renv.denv.ops.row(job_id)


def ending(renv: ReviseEnv, job_id: UUID) -> str:
    event = renv.sql(
        "SELECT payload ->> 'category' AS category FROM job_events WHERE job_id = :j "
        "AND kind IN ('retry_scheduled', 'failed') ORDER BY seq DESC LIMIT 1",
        j=job_id,
    ).first()
    return "" if event is None else event.category


async def designing(renv: ReviseEnv) -> tuple[UUID, Any]:
    """A ``design_draft`` on the MOWL playlist, run once: (job id, its row)."""
    renv.use("good_first_draft")
    body = {
        "kind": design.KIND,
        "input": {
            "title": "Tuesday climb",
            "brief": BRIEF,
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST},
            "mowl_connection_id": str(renv.browser.connection_id),
        },
    }
    made = await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["id"])
    return job_id, await run_any(renv, job_id)


def drafts(renv: ReviseEnv, job_id: UUID) -> list[Any]:
    return renv.sql("SELECT * FROM class_revisions WHERE job_id = :j", j=job_id).all()


def tag(fake_mowl: FakeMowlMusic, status: str, n: int) -> None:
    fake_mowl.music.tracks[n]["Title"] += f" [{status}]"


def gets(fake: FakeSpotdown) -> int:
    return sum(s.method == "GET" and s.path.startswith("/v2/analyses/") for s in fake.seen)


@pytest.fixture
def designed_maps(monkeypatch: pytest.MonkeyPatch) -> list[dict[int, Any]]:
    """The timing classes of each map stage D gave back."""
    found: list[dict[int, Any]] = []
    real = stage_d.listed_music

    async def seen(*args: Any) -> Any:
        step = await real(*args)
        found.append(dict(track_timing_calc.classes(step.data.analysis["map"])))
        return step

    for module in (stage_d, draft_spotdown, spotdown_carry):
        monkeypatch.setattr(module, "listed_music", seen)
    return found


# --- the design ----------------------------------------------------------------------------------


async def test_every_track_is_matched_analysed_and_designed_on_spotdown(
    renv: ReviseEnv, spotdown: FakeSpotdown, designed_maps: list[dict[int, Any]]
) -> None:
    job_id, row = await designing(renv)
    assert (row.state, row.error_category) == ("completed", None)
    assert designed_maps == [dict.fromkeys(range(TRACKS), "approximate")]
    assert sum(s.path == "/v2/match" for s in spotdown.seen) == 1
    assert sum(s.path == "/v2/analyses" for s in spotdown.seen) == TRACKS
    (draft,) = drafts(renv, job_id)
    field = draft.music["spotdown"]
    assert field["tracks"] == list(range(TRACKS))
    assert "song_list_id" not in field
    assert [p for p, _, _ in timing_marks(field["record"])] == list(range(TRACKS))
    assert [e["use"] for e in field["record"][MATCH_KEY]["songs"]] == ["timing"] * TRACKS
    assert field["record"][KEY]["swaps"] == []
    sent = str([s.body for s in spotdown.seen])
    assert "PlantedTrk" not in sent  # no Spotify id leaves


async def test_a_track_with_no_bpm_yet_is_still_timed_on_spotdown(
    renv: ReviseEnv,
    spotdown: FakeSpotdown,
    fake_mowl: FakeMowlMusic,
    designed_maps: list[dict[int, Any]],
) -> None:
    fake_mowl.bpm_never = {2}
    job_id, row = await designing(renv)
    assert (row.state, row.error_category) == ("completed", None)
    assert designed_maps == [dict.fromkeys(range(TRACKS), "approximate")]
    (draft,) = drafts(renv, job_id)
    assert [u["index"] for u in draft.music["unknown_bpm"]] == [2]  # still told


MISSING = ["not_found", "uncertain", "source_unavailable", "provider_error"]


@pytest.mark.parametrize("status", MISSING)
async def test_a_track_spotdown_cannot_match_fails_the_design(
    renv: ReviseEnv, spotdown: FakeSpotdown, fake_mowl: FakeMowlMusic, status: str
) -> None:
    tag(fake_mowl, status, 3)
    job_id, row = await designing(renv)
    assert (row.state, ending(renv, job_id)) == ("failed", "analysis_failed")
    assert drafts(renv, job_id) == []  # never a draft on MOWL's sections
    assert not any(s.path == "/v2/analyses" for s in spotdown.seen)


async def test_a_track_spotdown_cannot_time_fails_the_design(
    renv: ReviseEnv, spotdown: FakeSpotdown
) -> None:
    for kind in ("vid", "alt"):
        video = f"{kind}{5:08d}"
        spotdown.documents[f"youtube:{video}"] = youtube_doc(video, 140.0, residual=2.5)
    job_id, row = await designing(renv)
    assert (row.state, ending(renv, job_id)) == ("failed", "analysis_failed")
    assert drafts(renv, job_id) == []


async def test_a_track_spotdown_cannot_align_fails_the_design(
    renv: ReviseEnv, spotdown: FakeSpotdown, fake_mowl: FakeMowlMusic
) -> None:
    track = fake_mowl.music.tracks[1]
    sections = fake_mowl.music.sections.pop(track["SpotifyTrackID"])
    track["SpotifyTrackID"] = "localFile1"  # not a Spotify track id: nothing to align to
    fake_mowl.music.sections["localFile1"] = sections
    job_id, row = await designing(renv)
    assert (row.state, ending(renv, job_id)) == ("failed", "analysis_failed")
    assert not spotdown.seen  # refused before anything is sent


# (how, the reason logged, the attempt's ending)
UNREACHABLE = [
    ("connect_error", "connection", ("queued", "analysis_unavailable")),
    ("503", "server_error", ("queued", "analysis_unavailable")),
    ("401", "unauthorized", ("queued", "analysis_unavailable")),
    ("not_configured", "not_configured", ("queued", "analysis_unavailable")),
    ("413", "too_large", ("failed", "analysis_failed")),
    ("400", "rejected", ("failed", "analysis_failed")),
    ("409", "conflict", ("failed", "internal")),
]


@pytest.mark.parametrize(
    ("how", "reason", "ending_of"), UNREACHABLE, ids=[u[0] for u in UNREACHABLE]
)
async def test_each_unreachable_kind_ends_its_category_with_its_line_logged(
    renv: ReviseEnv,
    spotdown: FakeSpotdown,
    caplog: pytest.LogCaptureFixture,
    how: str,
    reason: str,
    ending_of: tuple[str, str],
) -> None:
    if how == "connect_error":
        spotdown.connect_error = True
    elif how == "not_configured":
        egress_spotdown.install_spotdown(None, None)
    else:
        spotdown.inject["/v2/match"] = [httpx.Response(int(how))]
    with caplog.at_level(logging.INFO):
        job_id, row = await designing(renv)
    assert (row.state, ending(renv, job_id)) == ending_of
    assert drafts(renv, job_id) == []
    assert outage_policy.log_message(reason) in caplog.text  # type: ignore[arg-type]


async def test_an_unreachable_read_at_stage_d_is_analysis_unavailable(
    renv: ReviseEnv, spotdown: FakeSpotdown, caplog: pytest.LogCaptureFixture
) -> None:
    real = stage_d.SpotdownClient.get_analysis

    async def unreachable(self: Any, analysis_id: str) -> Any:
        spotdown.connect_error = True
        try:
            return await real(self, analysis_id)
        finally:
            spotdown.connect_error = False

    with pytest.MonkeyPatch.context() as m, caplog.at_level(logging.INFO):
        m.setattr(stage_d.SpotdownClient, "get_analysis", unreachable)
        job_id, row = await designing(renv)
    assert (row.state, ending(renv, job_id)) == ("queued", "analysis_unavailable")  # retried
    assert drafts(renv, job_id) == []
    assert outage_policy.log_message("connection") in caplog.text


@pytest.mark.parametrize("error", ERRORS, ids=[e.__name__ for e in ERRORS])
@pytest.mark.parametrize("where", ["match_picks", "analyse_list"])
async def test_an_error_of_any_type_in_the_stages_is_internal(
    renv: ReviseEnv, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch, where: str,
    error: type[Exception],
) -> None:  # fmt: skip
    async def raising(*_a: Any, **_k: Any) -> Any:
        raise error("boom")

    monkeypatch.setattr(playlist_spotdown, where, raising)
    job_id, row = await designing(renv)
    assert (row.state, ending(renv, job_id)) == ("failed", "internal")
    assert drafts(renv, job_id) == []


# --- its revise and refit ------------------------------------------------------------------------


async def designed(renv: ReviseEnv) -> tuple[UUID, Any]:
    job_id, row = await designing(renv)
    assert (row.state, row.error_category) == ("completed", None)
    (draft,) = drafts(renv, job_id)
    return row.class_id, draft


async def refit(renv: ReviseEnv, class_id: UUID, base: Any) -> Any:
    """A beat-one refit of ``base`` (one correction of track 1), run to the end: the job's row."""
    renv.sql(
        "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
        "VALUES (:o, :c, :f, 1, 1, :bpm)",
        o=renv.owner, c=class_id, f=base.music["fingerprint"], bpm=base.music["songs"][0]["bpm"],
    )  # fmt: skip
    values = {
        "class_id": str(class_id),
        "base_revision_id": str(base.id),
        "mowl_connection_id": str(renv.browser.connection_id),
    }
    body = {"kind": REFIT_DRAFT.name, "input": values}
    made = await renv.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert made.status_code == 201, made.text
    return await run_any(renv, UUID(made.json()["id"]))


async def revised(renv: ReviseEnv, class_id: UUID, base: Any) -> Any:
    renv.use("revise_good")
    made = await renv.ask(class_id, base.id)
    assert made.status_code == 201, made.text
    return await run_any(renv, UUID(made.json()["id"]))


async def test_a_revise_and_a_refit_read_the_same_analyses_again(
    renv: ReviseEnv, spotdown: FakeSpotdown, designed_maps: list[dict[int, Any]]
) -> None:
    class_id, draft = await designed(renv)
    analyses = sum(s.path == "/v2/analyses" for s in spotdown.seen)
    before = gets(spotdown)
    row = await revised(renv, class_id, draft)
    assert (row.state, row.error_category) == ("completed", None)
    second = renv.sql("SELECT * FROM class_revisions WHERE id = :r", r=renv.current(class_id)).one()
    assert second.music["spotdown"] == draft.music["spotdown"]  # the same record, carried
    assert gets(spotdown) - before == TRACKS  # every analysis read again by its id
    assert sum(s.path == "/v2/analyses" for s in spotdown.seen) == analyses  # nothing new
    assert not [s for s in spotdown.seen if s.path == "/v2/match"][1:]  # matched once only
    row = await refit(renv, class_id, second)
    assert (row.state, row.error_category) == ("completed", None)
    third = renv.sql("SELECT * FROM class_revisions WHERE id = :r", r=renv.current(class_id)).one()
    assert third.music["spotdown"] == draft.music["spotdown"]
    assert third.music["beat_one"] == [{"song": 1, "beats": 1}]
    assert designed_maps[1:] == [dict.fromkeys(range(TRACKS), "approximate")] * 2


async def test_a_revise_after_the_playlist_changed_runs_the_stages_again(
    renv: ReviseEnv,
    spotdown: FakeSpotdown,
    fake_mowl: FakeMowlMusic,
    designed_maps: list[dict[int, Any]],
) -> None:
    """The user changed the playlist since: the base's record no longer fits, so the revise times
    the new music with the design's stages (never MOWL's sections) and tells the model."""
    class_id, draft = await designed(renv)
    del fake_mowl.music.tracks[-1]
    matches = sum(s.path == "/v2/match" for s in spotdown.seen)
    row = await revised(renv, class_id, draft)
    assert (row.state, row.error_category) == ("completed", None)
    second = renv.sql("SELECT * FROM class_revisions WHERE id = :r", r=renv.current(class_id)).one()
    assert second.music["fingerprint"] != draft.music["fingerprint"]
    assert second.music["spotdown"]["tracks"] == list(range(TRACKS - 1))  # a new record
    assert sum(s.path == "/v2/match" for s in spotdown.seen) == matches + 1
    assert designed_maps[-1] == dict.fromkeys(range(TRACKS - 1), "approximate")
    sent = renv.providers[-1].sent[0].spec
    assert "tracks were added, removed or replaced" in sent.first_message


async def test_a_refit_after_the_playlist_changed_is_music_changed_and_reads_nothing(
    renv: ReviseEnv, spotdown: FakeSpotdown, fake_mowl: FakeMowlMusic
) -> None:
    class_id, draft = await designed(renv)
    fake_mowl.music.tracks.reverse()  # the user reordered the playlist
    seen = len(spotdown.seen)
    row = await refit(renv, class_id, draft)
    assert (row.state, row.error_category) == ("failed", "music_changed")
    assert renv.current(class_id) == draft.id
    assert spotdown.seen[seen:] == []  # nothing matched or read for other music


@pytest.mark.parametrize("job", ["revise", "refit"])
async def test_a_base_without_the_field_is_music_changed_never_mowls_sections(
    renv: ReviseEnv, spotdown: FakeSpotdown, job: str
) -> None:
    """#1407, the literal rule: a Spotify base without ``music.spotdown`` (designed on MOWL's
    sections, before the stages) is ``music_changed``; nothing is read or stored."""
    class_id, draft = await designed(renv)
    renv.sql("UPDATE class_revisions SET music = music - 'spotdown' WHERE id = :r", r=draft.id)
    stripped = renv.sql("SELECT * FROM class_revisions WHERE id = :r", r=draft.id).one()
    seen = len(spotdown.seen)
    row = await (
        revised(renv, class_id, stripped) if job == "revise" else refit(renv, class_id, stripped)
    )
    assert (row.state, row.error_category) == ("failed", "music_changed")
    assert renv.current(class_id) == draft.id
    assert spotdown.seen[seen:] == []


async def test_a_record_that_lost_an_entry_fails_the_revise_analysis_failed(
    renv: ReviseEnv, spotdown: FakeSpotdown
) -> None:
    class_id, draft = await designed(renv)
    music = copy.deepcopy(draft.music)
    music["spotdown"]["record"][KEY]["songs"].pop(4)
    renv.sql(
        "UPDATE class_revisions SET music = CAST(:m AS jsonb) WHERE id = :r",
        m=json.dumps(music),
        r=draft.id,
    )
    row = await revised(renv, class_id, draft)
    assert (row.state, row.error_category) == ("failed", "analysis_failed")
    assert renv.current(class_id) == draft.id
