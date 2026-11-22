"""spotdown's stages A, B and D in the composer (ADR 0040 amendment "spotdown is the only analysis
path", items 3 to 5; ADR 0038 sections 5 to 7; #1386), as jobs on the fakes (``composertools``'
world with the fake spotdown; a track's match status is a ``[<status>]`` token in its title). No
network, no real account, no real model.

- a fill's list carries the match and analysis records, every song ``approximate``; its preview
  is designed on spotdown's sections (stage D in phase 1) and phase 2 reads them again;
- a pinned song spotdown cannot match is Not as asked ``pinned_not_found`` and not in the list; a
  pinned song spotdown cannot analyse has no spare (``analysis_failed``), an unsuitable one is
  kept at its slot with a warning;
- a change carries the waiting list's entries; a waiting list without them is ``analysis_failed``;
- spotdown unreachable when the preview's analyses are read is ``analysis_unavailable``.
"""

from typing import Any
from uuid import UUID

import pytest

from classbeat.agent import track_timing_calc
from classbeat.songs import composer_design, pick_match_batch
from classbeat.spotdown.song_analysis_record_model import KEY, timing_marks
from classbeat.spotdown.song_match_record_model import KEY as MATCH_KEY
from composertools import (
    REVISED,
    ComposerEnv,
    change_script,
    choose_turns,
    design_turns,
    fill_script,
    pool_turns,
)
from pickfake import POOL_14
from songanalysisdocs import youtube_doc
from songtools import Rider, song_id
from spotdownfake import FakeSpotdown, spotdown
from spotifycore import mowl_bpm

__all__ = ["spotdown"]  # the fixture

pytestmark = pytest.mark.usefixtures("jenv", "waits")

PINNED = 3  # "Ride Song 3 alpha", pinned to "the climb": slot 2 of the sketch


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(_run: object, _seconds: float) -> None:
        return None

    monkeypatch.setattr(pick_match_batch, "_sleep", sleep)


@pytest.fixture
def designed_on(monkeypatch: pytest.MonkeyPatch) -> list[dict[int, str]]:
    """The timing classes of every map the composer's preview was designed on."""
    seen: list[dict[int, Any]] = []
    real = composer_design.design_on

    async def recording(run: Any, step: Any, *args: Any) -> Any:
        seen.append(dict(track_timing_calc.classes(step.data.analysis["map"])))
        return await real(run, step, *args)

    monkeypatch.setattr(composer_design, "design_on", recording)
    return seen


def ending(cenv: ComposerEnv, job_id: UUID) -> str:
    event = cenv.sql(
        "SELECT payload ->> 'category' AS category FROM job_events WHERE job_id = :j "
        "AND kind IN ('retry_scheduled', 'failed') ORDER BY seq DESC LIMIT 1",
        j=job_id,
    ).first()
    return "" if event is None else event.category


def ref(song: int) -> int:
    """The registry's ref of a catalogue song with the pin found: the pin first, then the two
    searches, each song once (``test_composer_sources.ref`` without a favoured track)."""
    if song == PINNED:
        return 0
    return song + 1 if song < PINNED else song


def pinned_script() -> dict[str, Any]:
    """The fill with the pin at the climb: the choice's pool is the model's picks less the pin."""
    pool = [(ref(i), role) for i, role in POOL_14]
    tracks = [i for i, _ in POOL_14 if i != PINNED]
    at = tracks.index
    listed = [
        ("pool", at(0), "warmup"), ("pool", at(2), "build"), ("kept", 0, "climb"),
        ("pool", at(10), "recovery"), ("pool", at(6), "sprint"), ("pool", at(7), "sprint"),
        ("pool", at(11), "recovery"), ("pool", at(12), "cooldown"),
    ]  # fmt: skip
    turns = [*pool_turns(pool), *choose_turns(listed), *design_turns()]
    return {"model": "fake-model", "drafts": {"revised": REVISED}, "turns": turns}


def unpinned_script() -> dict[str, Any]:
    """The same fill when the pin is missing: no kept slot, the pool's own climb (song 4)."""
    pool = [(ref(i), role) for i, role in POOL_14]
    tracks = [i for i, _ in POOL_14 if i != PINNED]
    at = tracks.index
    listed = [
        ("pool", at(0), "warmup"), ("pool", at(2), "build"), ("pool", at(4), "climb"),
        ("pool", at(10), "recovery"), ("pool", at(6), "sprint"), ("pool", at(7), "sprint"),
        ("pool", at(11), "recovery"), ("pool", at(12), "cooldown"),
    ]  # fmt: skip
    turns = [*pool_turns(pool), *choose_turns(listed), *design_turns()]
    return {"model": "fake-model", "drafts": {"revised": REVISED}, "turns": turns}


async def pinned_class(cenv: ComposerEnv) -> tuple[Rider, UUID]:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(
        rider, pinned=[{"title": "Ride Song 3 alpha", "at": "the climb"}]
    )
    return rider, class_id


def bad_docs(spotdown: FakeSpotdown, n: int, **kw: Any) -> None:
    tempo = float(mowl_bpm(song_id(n))) * kw.pop("tempo", 1.0)
    for kind in ("vid", "alt"):
        video = f"{kind}{n:08d}"
        spotdown.documents[f"youtube:{video}"] = youtube_doc(video, tempo, **kw)


async def test_a_fill_records_both_stages_and_its_preview_is_designed_on_spotdown(
    cenv: ComposerEnv, spotdown: FakeSpotdown, designed_on: list[dict[int, str]]
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    _, list_id, _ = await cenv.waiting(rider, class_id)
    stored = cenv.songenv.revision(list_id)
    assert [e["use"] for e in stored.provenance[MATCH_KEY]["songs"]] == ["timing"] * 8
    assert [p for p, _, _ in timing_marks(stored.provenance)] == list(range(8))
    assert {s["timing"] for s in stored.content["songs"]} == {"approximate"}
    assert designed_on == [dict.fromkeys(range(8), "approximate")]  # never MOWL's sections
    gets = [s for s in spotdown.seen if s.method == "GET" and s.path.startswith("/v2/analyses/")]
    assert len(gets) == 16  # stage B read each document, then the preview's stage D again


async def test_a_pinned_song_spotdown_cannot_match_is_not_as_asked(
    cenv: ComposerEnv, spotdown: FakeSpotdown
) -> None:
    rider, class_id = await pinned_class(cenv)
    cenv.songenv.fake.catalogue[song_id(PINNED)]["name"] += " [not_found]"
    _, list_id, _ = await cenv.waiting(rider, class_id, unpinned_script())
    content = cenv.content(list_id)
    assert song_id(PINNED) not in [s["spotify_id"] for s in content["songs"]]
    unmet = {(u["path"], u["reason_code"]) for u in content["unmet"]}
    assert ("pinned_songs.0", "pinned_not_found") in unmet


async def test_a_pinned_song_spotdown_cannot_analyse_has_no_spare(
    cenv: ComposerEnv, spotdown: FakeSpotdown
) -> None:
    rider, class_id = await pinned_class(cenv)
    bad_docs(spotdown, PINNED, residual=2.5)
    made = await cenv.fill(rider, class_id)
    job_id = UUID(made.json()["job_id"])
    cenv.songenv.script = pinned_script()
    row = await cenv.songenv.run(job_id)
    assert (row.state, ending(cenv, job_id)) == ("failed", "analysis_failed")
    assert row.approval_revision_id is None


async def test_an_unsuitable_pinned_song_stays_at_its_slot_with_a_warning(
    cenv: ComposerEnv, spotdown: FakeSpotdown
) -> None:
    rider, class_id = await pinned_class(cenv)
    bad_docs(spotdown, PINNED, tempo=1.2)
    _, list_id, _ = await cenv.waiting(rider, class_id, pinned_script())
    stored = cenv.songenv.revision(list_id)
    assert [s["spotify_id"] for s in stored.content["songs"]][2] == song_id(PINNED)
    entries = {e["position"]: e for e in stored.provenance[KEY]["songs"]}
    assert entries[2]["warning"] is True
    assert all(s["position"] != 2 for s in stored.provenance[KEY]["swaps"])


async def test_an_unreachable_spotdown_at_the_previews_read_is_analysis_unavailable(
    cenv: ComposerEnv, spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    real = composer_design.listed_music

    async def cut(*args: Any) -> Any:
        spotdown.connect_error = True  # the match and the analyses got through; the read does not
        return await real(*args)

    monkeypatch.setattr(composer_design, "listed_music", cut)
    made = await cenv.fill(rider, class_id)
    job_id = UUID(made.json()["job_id"])
    cenv.songenv.script = fill_script()
    row = await cenv.songenv.run(job_id)
    assert (row.state, ending(cenv, job_id)) == ("queued", "analysis_unavailable")
    assert row.approval_revision_id is None


async def test_a_change_carries_every_entry_of_the_waiting_list(
    cenv: ComposerEnv, spotdown: FakeSpotdown, designed_on: list[dict[int, str]]
) -> None:
    from test_composer_change import send  # the change's own sender

    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    _, list_id, _ = await cenv.waiting(rider, class_id)
    waiting = cenv.songenv.revision(list_id).provenance
    submitted = await send(rider, class_id, "More standing climbs.")
    change_id = UUID(submitted.json()["job_id"])
    cenv.songenv.script = change_script()
    row = await cenv.songenv.run(change_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    stored = cenv.songenv.revision(row.approval_revision_id)

    def ids(p: Any) -> list[str]:
        return [e["analysis_id"] for e in p[KEY]["songs"]]

    assert ids(stored.provenance) == ids(waiting)  # the same analyses, slot by slot
    assert {e.get("kept") for e in stored.provenance[KEY]["songs"]} == {True}
    assert stored.provenance[MATCH_KEY] == waiting[MATCH_KEY]
    assert designed_on[-1] == dict.fromkeys(range(8), "approximate")


async def test_a_change_of_a_waiting_list_without_the_record_is_analysis_failed(
    cenv: ComposerEnv, spotdown: FakeSpotdown
) -> None:
    from test_composer_change import send

    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    _, list_id, _ = await cenv.waiting(rider, class_id)
    cenv.sql(
        "UPDATE class_revisions SET provenance = provenance - 'spotdown_analysis' WHERE id = :r",
        r=list_id,
    )
    submitted = await send(rider, class_id, "More standing climbs.")
    change_id = UUID(submitted.json()["job_id"])
    cenv.songenv.script = change_script()
    row = await cenv.songenv.run(change_id)
    assert (row.state, ending(cenv, change_id)) == ("failed", "analysis_failed")
