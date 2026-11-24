"""A miss is never saved by itself (#1493, ADR 0038 section 7): in composer phase 2 a ``kept`` fit
re-times the approved preview with no model and no check; a ``redesigned`` fit runs the shape
check against the layered brief (the revise stage's gate) and, on a miss after its corrections,
records ``shape_off`` items on the draft, with ``save_pending`` unset. Never the network or a
model."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.agent.brief import Brief
from classbeat.agent.stated_layer import layer_of
from composertools import REVISED, ComposerEnv, fill_script, staged
from pickfake import call, turn
from songtools import BRIEF
from test_composer_phase2 import EIGHT_LONGER, approve, stretch

pytestmark = pytest.mark.usefixtures("jenv")

# ``REVISED`` has five efforts (zone 5 or more, at most 90 s) in its three main segments.
AS_DESIGNED = [{"kind": "efforts", "count": {"min": 5, "max": 5}}]
SEVEN = [{"kind": "efforts", "count": {"min": 7, "max": 7}}]


def tagged() -> dict[str, Any]:
    return {**REVISED, "segments": [{**s, "section": 0} for s in REVISED["segments"]]}


def stated(cenv: ComposerEnv, revision: UUID, shape: list[dict[str, Any]]) -> None:
    brief = Brief.model_validate({**BRIEF, "shape": shape, "explicit": ["shape"]})
    cenv.sql(
        "UPDATE class_revisions SET provenance = provenance || CAST(:p AS jsonb) WHERE id = :r",
        p=json.dumps({"stated": layer_of(brief)}),
        r=revision,
    )


def redesign_script(refused: int) -> dict[str, Any]:
    """The revise stage on the approved preview: ``refused`` submissions the gate sends back, then
    the one it accepts."""
    submit = call("submit_draft", {"draft": {"$draft": "revised"}, "note": "Timed."})
    return {
        "model": "fake-model",
        "drafts": {"revised": tagged()},
        "turns": [
            {**turn(call("get_music", {})), "only_in": "revise"},
            *({**turn(submit), "only_in": "revise"} for _ in range(refused + 1)),
        ],
    }


async def test_a_redesign_that_misses_the_shape_records_shape_off_and_is_not_saved(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    stated(cenv, sketch, AS_DESIGNED)
    script = fill_script()
    script["drafts"]["revised"] = tagged()
    job_id, list_id, digest = await cenv.waiting(rider, class_id, script)
    assert cenv.content(list_id).get("unmet") is None  # the preview kept the shape
    assert (await approve(rider, job_id, list_id, digest, save=True)).status_code == 200
    stretch(cenv, list_id, EIGHT_LONGER)  # the final songs differ: the draft is designed again
    stated(cenv, sketch, SEVEN)  # the latest instruction wants seven; the scripted design has five
    cenv.songenv.script = redesign_script(refused=3)
    row = await cenv.songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    draft_id = cenv.current(class_id)
    assert draft_id is not None
    draft = cenv.songenv.revision(draft_id)
    assert draft.provenance["fit"] == "redesigned"
    items = [i for i in draft.provenance["interpretation"] if i["path"].startswith("shape.")]
    assert [(i["path"], i["origin"], i["reason_code"]) for i in items] == [
        ("shape.0", "not_as_asked", "shape_off")
    ]
    assert items[0]["found"] == 5  # the scripted design has five efforts
    assert items[0]["value"] == {"kind": "efforts", "count": {"min": 7, "max": 7}}
    pending = cenv.sql("SELECT save_pending FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert pending is None  # only the instructor's own Save writes it


async def test_a_kept_fit_retimes_the_preview_with_no_model_and_no_check(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    stated(cenv, sketch, AS_DESIGNED)
    script = fill_script()
    script["drafts"]["revised"] = tagged()
    job_id, list_id, digest = await cenv.waiting(rider, class_id, script)
    assert (await approve(rider, job_id, list_id, digest, save=True)).status_code == 200
    stated(cenv, sketch, SEVEN)  # not looked at by a kept fit: no model, same shape
    before = sum(len(p.sent) for p in cenv.songenv.providers)
    row = await cenv.songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert sum(len(p.sent) for p in cenv.songenv.providers) == before
    draft_id = cenv.current(class_id)
    assert draft_id is not None
    draft = cenv.songenv.revision(draft_id)
    assert draft.provenance["fit"] == "kept"
    assert "interpretation" not in draft.provenance
    assert [s.get("section") for s in draft.content["segments"]] == [0] * 7  # the tags kept


def missing_fill_script() -> dict[str, Any]:
    """The fill's design, for a sketch whose layered shape wants seven efforts: the model keeps
    handing back the five of ``REVISED``, so the gate refuses it three times and then records it."""
    submit = call("submit_draft", {"draft": {"$draft": "revised"}, "note": "Timed."})
    design = staged("revise", [turn(call("get_music", {})), *(turn(submit) for _ in range(4))])
    script = fill_script(design=design)
    script["drafts"]["revised"] = tagged()
    return script


async def test_a_preview_that_misses_a_section_carries_it_to_the_approval_and_the_draft(
    cenv: ComposerEnv,
) -> None:
    """The instructor sees the miss before Approve (the list's ``unmet``) and, after a save
    approval and a kept fit, it rides along on the stored draft's provenance."""
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    stated(cenv, sketch, SEVEN)
    job_id, list_id, digest = await cenv.waiting(rider, class_id, missing_fill_script())
    unmet = [i for i in cenv.content(list_id)["unmet"] if i["path"].startswith("shape.")]
    assert [(i["path"], i["origin"], i["reason_code"], i["found"]) for i in unmet] == [
        ("shape.0", "not_as_asked", "shape_off", 5)
    ]
    assert unmet[0]["value"] == {"kind": "efforts", "count": {"min": 7, "max": 7}}
    assert (await approve(rider, job_id, list_id, digest, save=True)).status_code == 200
    row = await cenv.songenv.run(job_id)  # the songs are as approved: a kept fit, no model
    assert (row.state, row.error_category) == ("completed", None)
    draft_id = cenv.current(class_id)
    assert draft_id is not None
    draft = cenv.songenv.revision(draft_id)
    assert draft.provenance["fit"] == "kept"
    carried = [i for i in draft.provenance["interpretation"] if i["path"].startswith("shape.")]
    assert [(i["path"], i["reason_code"], i["found"]) for i in carried] == [
        ("shape.0", "shape_off", 5)
    ]
    # Approving with the miss shown is the instructor's acceptance: the kept draft is the one
    # the approved save names.
    pending = cenv.sql("SELECT save_pending FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert pending is not None
    assert pending["revision_id"] == str(draft_id)
