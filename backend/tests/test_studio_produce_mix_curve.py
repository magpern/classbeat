"""#1330 on the database: the mix output reads each stored map's timing class and low band, so a
transition between two ``exact`` spotdown rows with bass and locked downbeats is rendered with the
``bass_swap`` curve (and any other row stays ``equal_power``); the timeline shows the transitions'
soft hints (``downbeats_not_locked``) from the same stored facts. The bass swap is off by default
(#1368), so every test here sets the ``bass_swap`` studio value it needs itself (``bass_swap_on``)
and puts the earlier value back; it never relies on what another test left. On the fakes, never
the network."""

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from classbeat.design.studio_revision_model import map_hash
from classbeat.spotdown.provenance_model import RECORD_VERSION
from classbeat.spotdown.studio_map_model import STUDIO_POLICY_VERSIONS
from classbeat.swedebeat_studio import config as studio_config
from studiooutputs import (
    ALL_ON,
    Renders,
    outputs_on,  # noqa: F401 - fixture
    produce,
    renders_fixture,  # noqa: F401 - the ``renders`` fixture
)
from studiotools import StudioEnv
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on", "bass_swap_on")


@pytest.fixture
def bass_swap_on(outputs_on: None) -> Iterator[None]:  # noqa: F811 - after ``outputs_on``
    """Every output and the bass swap on (off by default, #1368), whatever an earlier test left;
    the earlier studio settings come back afterwards."""
    before = studio_config.current()
    studio_config.configure(studio_config.StudioConfig(ALL_ON | {"bass_swap"}))
    try:
        yield
    finally:
        studio_config.configure(before)


def beats(step: float) -> list[dict[str, Any]]:
    """Trusted downbeats every ``step`` s from 1 s; at 2 s the overlap of two whole 240 s pieces
    (8 s) locks, at 2.2 s it does not."""
    return [
        {"at_s": round(1.0 + step * n, 3), "kind": "downbeat", "confidence": 0.9,
         "method": "estimated", "trusted": True}
        for n in range(int(238.9 / step) + 1)
    ]  # fmt: skip


BASS = {"step_s": 1.0, "values": [0.8] * 241}


def store_spotdown_rows(
    studio: StudioEnv,
    revision_id: UUID,
    *,
    with_bass: bool,
    step: float = 2.0,
    slow_first: bool = False,
) -> None:
    """Every stored map becomes a current ``exact`` spotdown row (``current_provenance``), and
    the draft's record of the analyses it was designed on says so (the output reads exactly it)."""
    shared = {"cut_candidates": beats(step), **({"low_band": BASS} if with_bass else {})}
    records: list[dict[str, Any]] = []
    rows = studio.songs.sql("SELECT id, reference, sha256, map FROM swedebeat_track_maps").all()
    for n, row in enumerate(rows):
        extra = dict(shared)
        if slow_first and n == 0:  # one track at another tempo: a neighbour pair clashes
            extra["songs"] = [{**row.map["songs"][0], "bpm": 80.0}]
        record = {
            "record_version": RECORD_VERSION,
            "audio": {"sha256": bytes(row.sha256).hex()},
            "timing": {"class": "exact", "reason": "played_recording"},
            "policy_versions": dict(STUDIO_POLICY_VERSIONS),
        }
        studio.songs.sql(
            "UPDATE swedebeat_track_maps SET analyser = 'spotdown', "
            "provenance = CAST(:provenance AS jsonb), map = map || CAST(:extra AS jsonb) "
            "WHERE id = :id",
            id=row.id,
            provenance=json.dumps(record),
            extra=json.dumps(extra),
        )
        records.append({
            "reference": row.reference, "analyser": "spotdown", "sha256": bytes(row.sha256).hex(),
            "map_hash": map_hash({**row.map, **extra}), "provenance": record,
        })  # fmt: skip
    studio.songs.sql(
        "UPDATE class_revisions SET music = jsonb_set(music, '{analyses}', CAST(:r AS jsonb)) "
        "WHERE id = :id",
        id=revision_id,
        r=json.dumps(records),
    )


async def curves(
    studio: StudioEnv, renders: Renders, *, with_bass: bool, step: float = 2.0
) -> list[str | None]:
    rider, class_id, _, current = await designed(studio)
    store_spotdown_rows(studio, current.current_revision_id, with_bass=with_bass, step=step)
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("completed", None)
    (plan,) = renders.plans
    return [t.get("curve") for t in plan]


async def test_exact_spotdown_rows_with_bass_are_rendered_with_a_bass_swap(
    studio: StudioEnv, renders: Renders
) -> None:
    found = await curves(studio, renders, with_bass=True)
    assert found[0] is None  # the first piece has no incoming transition
    assert found[1:] == ["bass_swap"] * 7


async def test_with_the_bass_swap_off_exact_rows_with_bass_stay_equal_power(
    studio: StudioEnv, renders: Renders
) -> None:
    studio_config.configure(studio_config.StudioConfig(ALL_ON))  # bass_swap_on puts it back
    assert await curves(studio, renders, with_bass=True) == [None] * 8


async def test_without_a_stored_low_band_the_curve_stays_equal_power(
    studio: StudioEnv, renders: Renders
) -> None:
    assert await curves(studio, renders, with_bass=False) == [None] * 8


async def test_downbeats_that_do_not_meet_are_no_bass_swap_and_a_hint_on_the_timeline(
    studio: StudioEnv, renders: Renders
) -> None:
    rider, class_id, _, current = await designed(studio)
    store_spotdown_rows(studio, current.current_revision_id, with_bass=True, step=2.2)
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("completed", None)
    assert [t.get("curve") for t in renders.plans[0]] == [None] * 8
    answer = await rider.browser.get(f"/api/classes/{class_id}/timeline")
    assert answer.status_code == 200, answer.text
    found = [(h["code"], h["pieces"]) for h in answer.json()["hints"]]
    assert ("downbeats_not_locked", [0, 1]) in found


async def test_clashing_tempos_are_a_tempo_jump_hint_on_the_timeline(studio: StudioEnv) -> None:
    rider, class_id, _, current = await designed(studio)
    store_spotdown_rows(studio, current.current_revision_id, with_bass=True, slow_first=True)
    answer = await rider.browser.get(f"/api/classes/{class_id}/timeline")
    assert answer.status_code == 200, answer.text
    assert "tempo_jump" in [h["code"] for h in answer.json()["hints"]]
