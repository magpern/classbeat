"""#1302 review B2 (ADR 0040 section 6, amendment "spotdown is the only analysis path", #1353): **a
class designed on spotdown keeps its maps and provenance.** Its record carries the analyser and
the provenance of each track; a later local row beside spotdown's is never read; a revise reads
the recorded spotdown rows with no new analysis, also with spotdown no longer installed (a cache
hit needs no configuration); gone from the cache, the rows are not replaced: ``music_changed``.
On the fakes (the fake gateway, the fake model, the fake spotdown); never the network."""

import copy
import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from classbeat.classes import studio_timeline
from classbeat.design import track_spotdown
from classbeat.spotdown import studio_tracks_config
from ctx import make_context
from spotdownfake import FakeSpotdown, spotdown
from studiomaps import track_map
from studiotools import StudioEnv
from test_swedebeat_studio_design import (
    LENGTH,
    MIX_8,
    analysed,
    approved,
    design_script,
    designed,
    revise,
    run_kind,
    start_design,
)

__all__ = ["analysed", "spotdown"]  # the fixtures


ON = studio_tracks_config.TracksConfig(frozenset({"upload"}), b"k" * 32)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(track_spotdown, "pause", pause)
    yield
    studio_tracks_config.configure(None)


@pytest.fixture
def spotdown_maps(spotdown: FakeSpotdown, analysed: list[str]) -> FakeSpotdown:
    """The fake spotdown with the studio fixture's 240 s map as its document of each track
    (``analysed``, autouse in the studio design tests, installs it)."""
    assert analysed == []
    return spotdown


def local_beside(studio: StudioEnv, owner: Any) -> None:
    """A local map of every track beside the spotdown rows (what a later local job stores)."""
    studio.songs.sql(
        "INSERT INTO swedebeat_track_maps (owner_id, reference, analyser_version, sha256, map) "
        "SELECT owner_id, reference, analyser_version, sha256, CAST(:m AS jsonb) "
        "FROM swedebeat_track_maps WHERE owner_id = :o AND analyser = 'spotdown'",
        o=owner,
        m=json.dumps(track_map(LENGTH, bpm=100.0)),
    )


async def test_a_class_designed_on_spotdown_keeps_its_maps_and_provenance(
    studio: StudioEnv, analysed: list[str], spotdown_maps: FakeSpotdown
) -> None:
    rider, class_id, _list, current = await designed(studio)
    assert analysed  # spotdown was asked
    analysed.clear()
    base = studio.songs.revision(current.current_revision_id)
    records = base.music["analyses"]
    assert len(records) == 8
    assert {r["analyser"] for r in records} == {"spotdown"}
    for record in records:  # the provenance kept with the class, not only in the cache
        assert record["provenance"]["timing"]["class"] == "exact"
        assert record["provenance"]["audio"]["sha256"] == record["sha256"]
    stored = studio.songs.sql(
        "SELECT map FROM swedebeat_track_maps WHERE owner_id = :o AND analyser = 'spotdown'",
        o=rider.user_id,
    ).scalars()
    assert all(m["cut_candidates"] for m in stored)  # #1328: each map carries its candidates
    # Not installed any more, a later local row of the same tracks coexist with spotdown's.
    studio_tracks_config.configure(None)  # a cache hit needs no configuration
    local_beside(studio, rider.user_id)
    ctx = make_context(rider.user_id)
    before = await studio_timeline.view(studio.songs.spot.env.factory, ctx, class_id)
    assert isinstance(before, dict)
    # A revise reads exactly the spotdown rows the class was designed on.
    cut = copy.deepcopy(MIX_8)
    cut["tracks"][-1]["end_sec"] = 230.0
    studio.songs.script = design_script(cut, note="Cut the last song.")
    response = await revise(rider, class_id, base.id, "Cut the last song shorter")
    row = await run_kind(studio, UUID(response.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
    new_id = studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).scalar_one()
    assert studio.songs.revision(new_id).music["analyses"] == records
    assert analysed == []
    # Gone from the cache, the spotdown rows are never replaced by the local ones: music_changed.
    studio.songs.sql(
        "DELETE FROM swedebeat_track_maps WHERE owner_id = :o AND analyser = 'spotdown'",
        o=rider.user_id,
    )
    assert await studio_timeline.view(studio.songs.spot.env.factory, ctx, class_id) == (
        "music_changed"
    )
    studio.songs.script = design_script(MIX_8)
    again = await revise(rider, class_id, new_id, "Harder")
    row = await run_kind(studio, UUID(again.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "music_changed")


async def test_the_design_stage_is_shown_the_stored_timing_class_of_spotdown_rows(
    studio: StudioEnv, analysed: list[str], spotdown_maps: FakeSpotdown
) -> None:
    """``draft.StudioMusic.of`` gives each stage track its stored timing class (not ``None``):
    the model's ``get_music`` names it."""
    studio_tracks_config.configure(ON)
    rider, class_id, list_id = await approved(studio)
    script = design_script(MIX_8)
    script["turns"][0]["calls"][0]["contains"] = ['"timing":"exact"']
    studio.songs.script = script
    response = await start_design(rider, class_id, list_id)
    assert response.status_code == 201, response.text
    row = await run_kind(studio, UUID(response.json()["job_id"]))
    assert (row.state, row.error_category) == ("completed", None)
