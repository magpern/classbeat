"""The latest instruction is the current revision's (#1493): the song fill and the composer's jobs
design to the class's brief with the sketch's nearest stated layer applied, not to the brief the
first design stored. Against Postgres and ``FakeProvider``; never the network or a model."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.agent.brief import Brief
from classbeat.agent.stated_layer import layer_of
from composertools import REVISED, ComposerEnv, fill_script
from songtools import BRIEF

pytestmark = pytest.mark.usefixtures("jenv")

SEVEN = [
    {"kind": "warmup"},
    {"kind": "efforts", "count": {"min": 7, "max": 7}, "load": "heavy"},
    {"kind": "cooldown"},
]


def layer(shape: list[dict[str, Any]]) -> dict[str, Any]:
    layered = Brief.model_validate({**BRIEF, "shape": shape, "explicit": ["shape"]})
    return layer_of(layered)


def stated(cenv: ComposerEnv, revision: UUID, stated_layer: dict[str, Any]) -> None:
    cenv.sql(
        "UPDATE class_revisions SET provenance = provenance || CAST(:p AS jsonb) WHERE id = :r",
        p=json.dumps({"stated": stated_layer}),
        r=revision,
    )


async def test_a_fill_is_started_with_the_layered_brief(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    stated(cenv, sketch, layer(SEVEN))
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
    given = cenv.sql(
        "SELECT input -> 'brief' FROM jobs WHERE id = :j", j=UUID(made.json()["job_id"])
    ).scalar_one()
    assert given["shape"] == SEVEN  # the first design's brief has none
    stored = cenv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert "shape" not in stored  # classes.brief stays as the first design stored it


async def test_a_fill_without_a_layer_keeps_the_stored_brief(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    made = await cenv.fill(rider, class_id)
    given = cenv.sql(
        "SELECT input -> 'brief' FROM jobs WHERE id = :j", j=UUID(made.json()["job_id"])
    ).scalar_one()
    assert "shape" not in given
    assert given["length_min"] == BRIEF["length_min"]


async def test_the_composer_designs_on_the_layered_brief(cenv: ComposerEnv) -> None:
    """The worker reads the sketch with its nearest stated layer: the design stage is told the
    shape of the latest request, and the first design's brief had no shape at all."""
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    rising = [{"kind": "efforts", "trend": "rising", "load": "heavy"}]
    stated(cenv, sketch, layer(rising))
    script = fill_script()
    script["drafts"]["revised"] = {
        **REVISED,
        "segments": [{**seg, "section": 0} for seg in REVISED["segments"]],
    }
    await cenv.waiting(rider, class_id, script)
    told = [
        s.spec.first_message
        for p in cenv.songenv.providers
        for s in p.sent
        if s.spec.stage == "revise"
    ]
    assert told
    assert all('"shape":[{"kind":"efforts"' in m.replace(" ", "") for m in told), told[0]
