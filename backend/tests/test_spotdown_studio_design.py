"""#1302 acceptance (ADR 0040 section 8): **with spotdown on but unreachable, or failing, the
studio design is byte-identical to today's.** Two studio designs of the same list and script on
the fakes (the fake gateway, the fake model, the fake spotdown), one with spotdown off and one with
it the source for every owner: the stored revision's content, its hash and its music (but the
list's own id) are the same bytes, and every map is the local one. Never the network."""

from collections.abc import Iterator
from typing import Any

import pytest

from classbeat.design import track_spotdown
from classbeat.spotdown import studio_tracks_config
from spotdownfake import FakeSpotdown, spotdown
from studiotools import StudioEnv, pick_script
from test_spotdown_shadow_design import output
from test_swedebeat_studio_design import analysed, designed

__all__ = ["analysed", "spotdown"]  # the fixtures


class Everyone(frozenset[Any]):
    """An allowlist that holds every owner (the riders are made inside ``designed``)."""

    def __contains__(self, _owner: object) -> bool:
        return True


ON = studio_tracks_config.TracksConfig("allowlist", Everyone(), frozenset({"upload"}), b"k" * 32)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(track_spotdown, "_pause", pause)
    yield
    studio_tracks_config.configure(None)


@pytest.mark.parametrize(
    "setup", [{"connect_error": True}, {"end": "processing_failed:decode_error"}]
)
async def test_the_design_is_byte_identical_when_spotdown_fails(
    studio: StudioEnv, analysed: list[str], spotdown: FakeSpotdown, setup: dict[str, Any]
) -> None:
    _rider, _class, _list, without = await designed(studio)
    assert spotdown.seen == []  # off: nothing was sent
    local = len(analysed)
    studio_tracks_config.configure(ON)
    for name, value in setup.items():
        setattr(spotdown, name, value)
    studio.songs.script = pick_script()  # the second rider's pick
    rider, _class, _list, with_spotdown = await designed(studio)
    assert spotdown.seen  # spotdown was the source and was asked
    assert output(studio, with_spotdown) == output(studio, without)
    assert with_spotdown.brief == without.brief
    # The fake gateway serves the same bytes for every track: the first fell back to the local
    # analysis, the others are the owner's own analysis of those bytes (no spotdown call).
    assert len(analysed) == local + 1
    analysers = studio.songs.sql(
        "SELECT DISTINCT analyser FROM swedebeat_track_maps WHERE owner_id = :o", o=rider.user_id
    ).scalars()
    assert list(analysers) == ["local"]
