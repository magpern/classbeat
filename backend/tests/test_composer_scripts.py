"""The fake model scripts of the composer flow's e2e (``e2e/tests/composer-workspace.spec.ts``,
ADR 0038, #1191) run through the real pipeline: the reading keeps a pinned song and a question,
the sketch is a sketch, and a chat change of it is a sketch again. Never the network or a model."""

import json
from uuid import UUID

import pytest

from agentfake import script
from classbeat.agent.providers.fake import FakeProvider
from classbeat.agent.records import Provenance
from interprettools import INTERPRET_REQUEST
from sketchtools import SketchEnv

pytestmark = pytest.mark.usefixtures("jenv")

REQUEST = "A hard climbing ride. Start with Dancing Queen by ABBA."


async def test_the_composer_scripts_read_sketch_and_change(skenv: SketchEnv) -> None:
    ienv = skenv.ienv
    ienv.models(FakeProvider(script("composer_read")))
    made = await ienv.start(REQUEST)
    assert made.status_code == 201, made.text
    await ienv.run(INTERPRET_REQUEST.name)
    class_id = UUID(made.json()["class_id"])
    stored = ienv.interpretation(class_id)
    assert stored["pinned_songs"] == [{"title": "Dancing Queen", "artist": "ABBA", "at": "Start"}]
    assert stored["questions"] == [{"path": "length_min", "question": "how_long", "assumed": 30}]

    handed = await ienv.handoff(class_id)
    assert handed.status_code == 201, handed.text
    skenv.renv.use("composer_sketch")
    await skenv.renv.run("design_draft")
    sketch = skenv.renv.current(class_id)
    assert sketch is not None
    first = Provenance.model_validate_json(json.dumps(skenv.revision(sketch).provenance))
    assert first.origin == "sketch"

    job_id, _ = await skenv.renv.revise(class_id, sketch, "composer_sketch_revised", "Easier start")
    row = skenv.renv.denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    revised = skenv.renv.current(class_id)
    assert revised is not None
    assert revised != sketch
    again = Provenance.model_validate_json(json.dumps(skenv.revision(revised).provenance))
    assert again.origin == "sketch"
