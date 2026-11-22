"""#1353 (ADR 0040 section 8 and its amendment, item 4): **every failure kind ends a SwedeBeat mix
analysis in its category**, from the same table as the studio tracks (``spotdownkinds``). Nothing
is stored and nothing is left on disk; every ``Unreachable`` site logs
``outage_policy.log_message``; there is no local fallback; and a retried ending gives the
next attempt a fresh wait budget (a requeue that is not a retry keeps it).
"""

import logging
from pathlib import Path
from typing import Any

import pytest

from classbeat.design import mix_spotdown, track_spotdown
from classbeat.spotdown import outage_policy, studio_tracks_config
from classbeat.spotdown.studio_tracks_config import TracksConfig
from mowlfake import FakeMowl
from optools import TOKEN_B, OpsEnv, xenv
from spotdownfake import FakeSpotdown, spotdown
from spotdownkinds import REASONS, all_kinds, apply
from test_spotdown_mix_job import NAME, PreferHarness, on_mix, rows
from test_swedebeat_fetch import add_beats, env, leftovers, operator_log, work

__all__ = ["env", "operator_log", "spotdown", "work", "xenv"]  # the fixtures

pytestmark = pytest.mark.usefixtures("mowl_api", "analysis_slot_by_default")
KEY = bytes(range(32))
UPLOAD_ON = TracksConfig(KEY)


@pytest.fixture(autouse=True)
def installed(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(track_spotdown, "pause", pause)
    studio_tracks_config.configure(UPLOAD_ON)
    yield
    studio_tracks_config.configure(None)


@pytest.fixture
def harness(xenv: OpsEnv, mowl_api: FakeMowl) -> PreferHarness:
    return PreferHarness(xenv, mowl_api.media)


@pytest.mark.parametrize(("kind", "category"), all_kinds())
async def test_each_failure_kind_ends_the_mix_in_its_category(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    work: Path,
    kind: str,
    category: str,
) -> None:
    config = apply(
        kind,
        spotdown,
        monkeypatch,
        unmappable=(mix_spotdown, "valid_map", lambda *_a: False),
        broken=(mix_spotdown, "valid_map"),
    )
    if config == "not_configured":
        studio_tracks_config.configure(None)
    owner, connection = await on_mix(harness, mowl_api)
    with caplog.at_level(logging.WARNING):
        row = await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert (row.state, row.error_category) == ("failed", category)
    assert rows(harness, owner) == []
    assert leftovers(work) == []
    if kind.startswith("unreachable:"):  # every Unreachable site logs the fixed line
        reason = kind.removeprefix("unreachable:")
        assert REASONS[reason] == category
        assert f"category={category} reason={reason}" in caplog.text
        assert outage_policy.log_message(reason) in caplog.text  # pyright: ignore[reportArgumentType]
    assert NAME not in caplog.text  # no mix name in what is logged


async def test_there_is_no_local_fallback_on_the_mix_path(
    harness: PreferHarness,
    spotdown: FakeSpotdown,
    mowl_api: Any,
) -> None:
    owner, connection = await on_mix(harness, mowl_api)
    row = await harness.run(owner, connection, add_beats(mowl_api, NAME))
    assert (row.state, row.error_category) == ("completed", None)
    spotdown.connect_error = True  # and on a failure no local fallback is tried either
    other, other_connection = await on_mix(harness, mowl_api, TOKEN_B)
    failed = await harness.run(other, other_connection, add_beats(mowl_api, NAME, token=TOKEN_B))
    assert failed.error_category == "analysis_unavailable"
