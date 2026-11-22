"""spotdown's stages A, B and D for a class on the user's own Spotify playlist (ADR 0040 amendment
"spotdown is the only analysis path", items 3 to 5; #1386), as a job on the fakes: the fake
Spotify, the fake MOWL, the fake spotdown (``spotdownfake``, in process; a track's match status is a
``[<status>]`` token in its title, a job's end a source's ``ends_by_source``). No model, no
network, no real account.

- every song of the user's list is matched and analysed (stage A, stage B as fixed positions) and
  recorded; phase 2 reads every analysis again (stage D) and designs on spotdown's sections only;
- the user's list has no spare: a song that is missing at either stage is ``analysis_failed``; an
  unsuitable one is accepted with a warning, and the list never changes;
- spotdown unreachable (each kind) ends in its ``outage_policy`` category with its line logged;
- a stored list without the analysis record is ``analysis_failed`` in phase 2 (stage D is always
  strict; the removed "give back MOWL's music" branch);
- an error of any type in the stages is ``internal``: never swallowed into MOWL's sections.
"""

import logging
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat import egress_spotdown
from classbeat.agent import track_timing_calc
from classbeat.songs import phase2_spotdown as stage_d
from classbeat.songs import pick_match_batch, playlist_spotdown
from classbeat.spotdown import outage_policy
from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from classbeat.spotdown.song_match_record_model import KEY as MATCH_KEY
from songanalysisdocs import youtube_doc
from songtools import Rider, SongEnv, song_id
from spotdownfake import FakeSpotdown, spotdown
from spotifycore import mowl_bpm
from spotifyfake import USER_A

__all__ = ["spotdown"]  # the fixture

pytestmark = pytest.mark.usefixtures("songenv", "waits")

ORDER = [5, 3, 11, 0, 7, 2, 9, 14]  # as ``test_design_with_songs_own_playlist``
OWN = [song_id(i) for i in ORDER]
ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, LookupError)


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(_run: object, _seconds: float) -> None:
        return None

    monkeypatch.setattr(pick_match_batch, "_sleep", sleep)


def source(songenv: SongEnv) -> dict[str, str]:
    mine = songenv.fake.add_playlist(USER_A, "My Tuesday ride", [f"spotify:track:{i}" for i in OWN])
    return {"source": "mowl_spotify_playlist", "spotify_playlist_id": mine.id}


def tag(songenv: SongEnv, status: str, n: int) -> None:
    songenv.fake.catalogue[song_id(n)]["name"] += f" [{status}]"


def ending(songenv: SongEnv, job_id: UUID) -> str:
    event = songenv.sql(
        "SELECT payload ->> 'category' AS category FROM job_events WHERE job_id = :j "
        "AND kind IN ('retry_scheduled', 'failed') ORDER BY seq DESC LIMIT 1",
        j=job_id,
    ).first()
    return "" if event is None else event.category


async def ended(songenv: SongEnv, rider: Rider) -> tuple[UUID, str, str]:
    job_id = await songenv.created(rider, source=source(songenv))
    row = await songenv.run(job_id)
    return job_id, row.state, ending(songenv, job_id)


async def test_every_song_is_matched_analysed_and_marked_in_the_users_order(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    rider = await songenv.rider()
    _, revision_id, _ = await songenv.waiting(rider, source=source(songenv))
    stored = songenv.revision(revision_id)
    assert [s["spotify_id"] for s in stored.content["songs"]] == OWN  # unchanged, in order
    assert {s["timing"] for s in stored.content["songs"]} == {"approximate"}
    match = stored.provenance[MATCH_KEY]
    assert [e["use"] for e in match["songs"]] == ["timing"] * 8
    assert [p for p, _, _ in timing_marks(stored.provenance)] == list(range(8))
    assert stored.provenance[KEY]["swaps"] == []
    assert sum(s.path == "/v2/match" for s in spotdown.seen) == 1
    assert sum(s.path == "/v2/analyses" for s in spotdown.seen) == 8  # every song, once
    sent = str([s.body for s in spotdown.seen])
    assert all(i not in sent for i in OWN)  # no Spotify id leaves


async def test_phase_two_designs_every_song_on_spotdowns_sections(
    songenv: SongEnv, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    designed: list[dict[int, Any]] = []
    real = stage_d.listed_music

    async def seen(*args: Any) -> Any:
        step = await real(*args)
        designed.append(dict(track_timing_calc.classes(step.data.analysis["map"])))
        return step

    monkeypatch.setattr(stage_d, "listed_music", seen)

    def gets() -> int:
        return sum(s.method == "GET" and s.path.startswith("/v2/analyses/") for s in spotdown.seen)

    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider, source=source(songenv))
    before = gets()
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert designed == [dict.fromkeys(range(8), "approximate")]
    assert gets() - before == 8  # stage D read every recorded analysis again


MISSING = ["not_found", "uncertain", "source_unavailable", "provider_error"]


@pytest.mark.parametrize("status", MISSING)
async def test_a_song_spotdown_cannot_match_has_no_spare_and_fails_the_job(
    songenv: SongEnv, spotdown: FakeSpotdown, status: str
) -> None:
    tag(songenv, status, ORDER[3])
    job_id, state, category = await ended(songenv, await songenv.rider())
    assert (state, category) == ("failed", "analysis_failed")
    assert songenv.row(job_id).approval_revision_id is None  # never a list on MOWL's sections
    assert not any(s.path == "/v2/analyses" for s in spotdown.seen)


async def test_a_song_spotdown_cannot_analyse_has_no_spare_and_fails_the_job(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    n = ORDER[2]
    for kind in ("vid", "alt"):
        spotdown.ends_by_source[f"youtube:{kind}{n:08d}"] = ["alignment_failed"]
    job_id, state, category = await ended(songenv, await songenv.rider())
    assert (state, category) == ("failed", "analysis_failed")
    assert songenv.row(job_id).approval_revision_id is None


async def test_an_unusable_timing_has_no_spare_and_fails_the_job(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    n = ORDER[1]
    for kind in ("vid", "alt"):
        video = f"{kind}{n:08d}"
        spotdown.documents[f"youtube:{video}"] = youtube_doc(
            video, float(mowl_bpm(song_id(n))), residual=2.5
        )
    _, state, category = await ended(songenv, await songenv.rider())
    assert (state, category) == ("failed", "analysis_failed")


async def test_an_unsuitable_song_is_kept_with_a_warning_and_the_list_never_changes(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    n = ORDER[4]
    for kind in ("vid", "alt"):
        video = f"{kind}{n:08d}"
        spotdown.documents[f"youtube:{video}"] = youtube_doc(
            video, float(mowl_bpm(song_id(n))) * 1.2
        )
    rider = await songenv.rider()
    _, revision_id, _ = await songenv.waiting(rider, source=source(songenv))
    stored = songenv.revision(revision_id)
    assert [s["spotify_id"] for s in stored.content["songs"]] == OWN
    entries = {e["position"]: e for e in stored.provenance[KEY]["songs"]}
    assert entries[4]["warning"] is True
    assert [p for p, e in entries.items() if e.get("warning")] == [4]
    assert stored.provenance[KEY]["swaps"] == []


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
    songenv: SongEnv,
    spotdown: FakeSpotdown,
    caplog: pytest.LogCaptureFixture,
    how: str,
    reason: str,
    ending_of: tuple[str, str],
) -> None:
    rider = await songenv.rider()
    if how == "connect_error":
        spotdown.connect_error = True
    elif how == "not_configured":
        egress_spotdown.install_spotdown(None, None)
    else:
        spotdown.inject["/v2/match"] = [httpx.Response(int(how))]
    with caplog.at_level(logging.INFO):
        job_id, state, category = await ended(songenv, rider)
    assert (state, category) == ending_of
    assert songenv.row(job_id).approval_revision_id is None
    assert outage_policy.log_message(reason) in caplog.text  # type: ignore[arg-type]


async def test_an_unreachable_analysis_read_in_phase_two_is_analysis_unavailable(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    rider = await songenv.rider()
    job_id, _ = await songenv.approved(rider, source=source(songenv))
    spotdown.connect_error = True
    row = await songenv.run(job_id)
    assert (row.state, ending(songenv, job_id)) == ("queued", "analysis_unavailable")  # retried
    assert not songenv.sql(
        "SELECT 1 FROM class_revisions WHERE class_id = :c AND kind = 'draft'", c=row.class_id
    ).first()  # nothing designed on MOWL's sections


async def test_a_list_without_the_record_is_analysis_failed_in_phase_two(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    """Stage D is always strict (#1386): no "give back MOWL's music" for a list with no record."""
    rider = await songenv.rider()
    job_id, revision_id = await songenv.approved(rider, source=source(songenv))
    songenv.sql(
        "UPDATE class_revisions SET provenance = provenance - 'spotdown_analysis' WHERE id = :r",
        r=revision_id,
    )
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "analysis_failed")
    assert not songenv.sql(
        "SELECT 1 FROM class_revisions WHERE class_id = :c AND kind = 'draft'", c=row.class_id
    ).first()  # nothing designed on MOWL's sections


@pytest.mark.parametrize("error", ERRORS, ids=[e.__name__ for e in ERRORS])
@pytest.mark.parametrize("where", ["match_picks", "analyse_list"])
async def test_an_error_of_any_type_in_the_stages_is_internal(
    songenv: SongEnv, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch, where: str,
    error: type[Exception],
) -> None:  # fmt: skip
    async def raising(*_a: Any, **_k: Any) -> Any:
        raise error("boom")

    monkeypatch.setattr(playlist_spotdown, where, raising)
    job_id, state, category = await ended(songenv, await songenv.rider())
    assert (state, category) == ("failed", "internal")
    assert songenv.row(job_id).approval_revision_id is None
