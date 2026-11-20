"""#1301 acceptance (ADR 0040 section 9): **in shadow mode, the design output is byte-identical to
the output without it.** Two studio designs of the same list and script on the fakes (the fake
gateway, the fake model, the fake spotdown), one with shadow mode off and one with it on: the
stored revision's content, its hash and its music (but the list's own id) are the same bytes, and
the shadow comparisons were recorded. Never the network."""

import json
from collections.abc import Iterator
from typing import Any

import pytest

from classbeat.design import track_shadow
from classbeat.spotdown import shadow_config
from spotdownfake import FakeSpotdown, spotdown
from studiotools import StudioEnv, pick_script
from test_swedebeat_studio_design import analysed, designed

__all__ = ["analysed", "spotdown"]  # the fixtures

ON = shadow_config.ShadowConfig(True, frozenset({"upload"}), bytes(range(32)))


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(track_shadow, "_pause", pause)
    yield
    shadow_config.configure(None)


def output(studio: StudioEnv, current: Any) -> tuple[bytes, bytes, bytes]:
    stored = studio.songs.revision(current.current_revision_id)
    music = {k: v for k, v in stored.music.items() if k != "song_list_id"}
    dump = json.dumps
    return (
        dump(stored.content, sort_keys=True).encode(),
        bytes(stored.content_hash),
        dump(music, sort_keys=True).encode(),
    )


async def test_the_design_is_byte_identical_with_shadow_mode_on(
    studio: StudioEnv, analysed: list[str], spotdown: FakeSpotdown
) -> None:
    _rider, _class, _list, without = await designed(studio)
    assert spotdown.seen == []  # off: nothing was sent
    shadow_config.configure(ON)
    studio.songs.script = pick_script()  # the second rider's pick
    rider, _class, _list, with_shadow = await designed(studio)
    assert output(studio, with_shadow) == output(studio, without)
    assert with_shadow.brief == without.brief
    rows = (
        studio.songs.sql(
            "SELECT outcome FROM spotdown_shadow_tracks WHERE owner_id = :o", o=rider.user_id
        )
        .scalars()
        .all()
    )
    assert rows
    assert set(rows) == {"compared"}  # the shadow ran beside the design


async def test_the_design_is_byte_identical_when_spotdown_is_unreachable(
    studio: StudioEnv, analysed: list[str], spotdown: FakeSpotdown
) -> None:
    _rider, _class, _list, without = await designed(studio)
    shadow_config.configure(ON)
    studio.songs.script = pick_script()  # the second rider's pick
    spotdown.connect_error = True
    rider, _class, _list, with_shadow = await designed(studio)
    assert output(studio, with_shadow) == output(studio, without)
    rows = studio.songs.sql(
        "SELECT outcome, detail FROM spotdown_shadow_tracks WHERE owner_id = :o", o=rider.user_id
    ).all()
    assert {(r.outcome, r.detail) for r in rows} == {("unreachable", "connection")}
