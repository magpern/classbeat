"""#1302 review C1 (ADR 0040 section 6): **a designed class reads exactly the analysis it was
designed on**, by its analyser, its bytes and its map (``track_cache.recorded``). For a class
designed on spotdown's maps, each later reader (the timeline, a revise and the outputs job) is
run against three caches, on the fakes (the fake gateway, the fake model, the fake spotdown);
never the network:

- ``kept``: the rows as the design stored them, so the reader works (the gate can pass);
- ``remapped``: the spotdown rows hold another map of the same bytes (a re-analysis by the same
  analyser, say under another policy version): ``music_changed``, which only the map hash sees;
- ``local_only``: the spotdown rows are gone and a ``local`` row of the same bytes with **the same
  map** is left: ``music_changed``, which only the analyser comparison sees.

Plus the stored form (review non-blocking 1): a map whose jsonb form differs from its Python form
(``-0.0``, ``1e+16``) is recorded by the hash of what is stored, so its class stays readable.
"""

import copy
import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest

from classbeat.classes import studio_timeline
from classbeat.design import track_spotdown
from classbeat.design.studio_revision_model import map_hash
from classbeat.spotdown import studio_tracks_config
from ctx import make_context
from spotdownfake import FakeSpotdown, spotdown
from studiomaps import track_map
from studiooutputs import outputs_on, produce
from studiotools import StudioEnv
from test_spotdown_studio_design import ON, no_pause, spotdown_maps
from test_swedebeat_studio_design import (
    LENGTH,
    MIX_8,
    analysed,
    design_script,
    designed,
    revise,
    run_kind,
)

__all__ = ["analysed", "no_pause", "outputs_on", "spotdown", "spotdown_maps"]  # the fixtures

CACHES = ("kept", "remapped", "local_only")


def remap(studio: StudioEnv, owner: UUID) -> None:
    """Another valid map of the same bytes, by the same analyser, provenance untouched."""
    other = track_map(LENGTH, bpm=100.0)
    studio.songs.sql(
        "UPDATE swedebeat_track_maps SET map = CAST(:m AS jsonb) "
        "WHERE owner_id = :o AND analyser = 'spotdown'",
        o=owner,
        m=json.dumps(other),
    )


def local_only(studio: StudioEnv, owner: UUID) -> None:
    """A ``local`` row of the same bytes with the very same map, then the spotdown rows gone."""
    studio.songs.sql(
        "INSERT INTO swedebeat_track_maps (owner_id, reference, analyser_version, sha256, map) "
        "SELECT owner_id, reference, analyser_version, sha256, map "
        "FROM swedebeat_track_maps WHERE owner_id = :o AND analyser = 'spotdown'",
        o=owner,
    )
    studio.songs.sql(
        "DELETE FROM swedebeat_track_maps WHERE owner_id = :o AND analyser = 'spotdown'",
        o=owner,
    )
    left = studio.songs.sql(
        "SELECT DISTINCT analyser FROM swedebeat_track_maps WHERE owner_id = :o", o=owner
    ).scalars()
    assert list(left) == ["local"]


def change(studio: StudioEnv, owner: UUID, cache: str) -> None:
    if cache == "remapped":
        remap(studio, owner)
    elif cache == "local_only":
        local_only(studio, owner)


def cut_last() -> dict[str, Any]:
    cut = copy.deepcopy(MIX_8)
    cut["tracks"][-1]["end_sec"] = 230.0
    return cut


@pytest.fixture
def on_spotdown(spotdown_maps: FakeSpotdown) -> Iterator[FakeSpotdown]:
    studio_tracks_config.configure(ON)
    yield spotdown_maps
    studio_tracks_config.configure(None)


async def designed_on_spotdown(
    studio: StudioEnv, analysed: list[str], cache: str
) -> tuple[Any, UUID, Any]:
    rider, class_id, _list, current = await designed(studio)
    records = studio.songs.revision(current.current_revision_id).music["analyses"]
    assert {r["analyser"] for r in records} == {"spotdown"}
    analysed.clear()
    studio_tracks_config.configure(None)  # a later job, spotdown not installed: cache hits only
    change(studio, rider.user_id, cache)
    return rider, class_id, current


@pytest.mark.usefixtures("on_spotdown")
@pytest.mark.parametrize("cache", CACHES)
async def test_the_timeline_reads_only_the_recorded_row(
    studio: StudioEnv, analysed: list[str], cache: str
) -> None:
    rider, class_id, _current = await designed_on_spotdown(studio, analysed, cache)
    ctx = make_context(rider.user_id)
    view = await studio_timeline.view(studio.songs.spot.env.factory, ctx, class_id)
    if cache == "kept":
        assert isinstance(view, dict)
    else:
        assert view == "music_changed"
    assert analysed == []


@pytest.mark.usefixtures("on_spotdown")
@pytest.mark.parametrize("cache", CACHES)
async def test_a_revise_reads_only_the_recorded_row(
    studio: StudioEnv, analysed: list[str], cache: str
) -> None:
    rider, class_id, current = await designed_on_spotdown(studio, analysed, cache)
    studio.songs.script = design_script(cut_last(), note="Cut the last song.")
    response = await revise(rider, class_id, current.current_revision_id, "Cut the last song")
    row = await run_kind(studio, UUID(response.json()["id"]))
    if cache == "kept":
        assert (row.state, row.error_category) == ("completed", None)
    else:
        assert (row.state, row.error_category) == ("failed", "music_changed")
    assert analysed == []  # never analysed again in place of the recorded row


@pytest.mark.usefixtures("on_spotdown", "outputs_on")
@pytest.mark.parametrize("cache", CACHES)
async def test_the_outputs_read_only_the_recorded_row(
    studio: StudioEnv, analysed: list[str], cache: str
) -> None:
    rider, class_id, current = await designed_on_spotdown(studio, analysed, cache)
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    if cache == "kept":
        assert (done.state, done.error_category) == ("completed", None)
    else:
        assert (done.state, done.error_category) == ("failed", "music_changed")
        assert studio.gateway.playlists == {}  # nothing written
    assert analysed == []


# --- the stored form (review non-blocking 1) ----------------------------------------------------


def unlike_jsonb() -> dict[str, Any]:
    """A valid map whose jsonb form is not its Python form: ``-0.0`` is stored as ``0.0`` and
    ``1e+16`` as an integer."""
    made = track_map(LENGTH)
    made["sections"][0]["energy"] = -0.0
    made["scale"] = 1e16
    return made


async def test_a_map_is_recorded_by_the_hash_of_its_stored_form(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = track_spotdown.studio_map

    def studio_map(*args: Any) -> Any:
        mapped = real(*args)
        return replace(mapped, track_map=unlike_jsonb())

    monkeypatch.setattr(track_spotdown, "studio_map", studio_map)
    rider, class_id, _list, current = await designed(studio)
    stored = studio.songs.sql(
        "SELECT map FROM swedebeat_track_maps WHERE owner_id = :o", o=rider.user_id
    ).scalars()
    as_read = list(stored)
    assert as_read
    assert map_hash(as_read[0]) != map_hash(unlike_jsonb())  # jsonb changed the map's text
    records = studio.songs.revision(current.current_revision_id).music["analyses"]
    assert {r["map_hash"] for r in records} == {map_hash(as_read[0])}
    ctx = make_context(rider.user_id)
    view = await studio_timeline.view(studio.songs.spot.env.factory, ctx, class_id)
    assert isinstance(view, dict)  # not music_changed
    studio.songs.script = design_script(cut_last(), note="Cut the last song.")
    response = await revise(rider, class_id, current.current_revision_id, "Cut the last song")
    row = await run_kind(studio, UUID(response.json()["id"]))
    assert (row.state, row.error_category) == ("completed", None)
