"""The fake model scripts of the composer flow's e2e (``e2e/tests/composer-workspace.spec.ts``,
ADR 0038, #1191) run through the real pipeline: the reading keeps a pinned song and a question,
the sketch is a sketch, and a chat change of it is a sketch again, twice, each through the pending
message the page's driver submits (the e2e's own words). The pinned song is one of the e2e fake
Spotify's catalogue. Never the network or a model."""

import json
import uuid
from uuid import UUID

import pytest

from agentfake import script
from classbeat.agent.providers.fake import FakeProvider
from classbeat.agent.records import Provenance
from classbeat.songs.composer_slots_policy import slot_roles
from interprettools import INTERPRET_REQUEST
from sketchtools import SketchEnv

pytestmark = pytest.mark.usefixtures("jenv")

REQUEST = "A hard climbing ride. Put Signal Harbor 18 by The Invented Band on the climb."
# The reading's quick answer (frontend `cw.answer.sentence.length`).
ANSWER = "Make the class 60 minutes long."
CHANGE = "Make the last climb longer."


async def test_the_composer_scripts_read_sketch_and_change(skenv: SketchEnv) -> None:
    ienv = skenv.ienv
    ienv.models(FakeProvider(script("composer_read")))
    made = await ienv.start(REQUEST)
    assert made.status_code == 201, made.text
    await ienv.run(INTERPRET_REQUEST.name)
    class_id = UUID(made.json()["class_id"])
    stored = ienv.interpretation(class_id)
    assert stored["pinned_songs"] == [
        {"title": "Signal Harbor 18", "artist": "The Invented Band", "at": "the climb"}
    ]
    assert [(q["topic"], q["assumed_index"]) for q in stored["questions"]] == [("length", 0)]

    handed = await ienv.handoff(class_id)
    assert handed.status_code == 201, handed.text
    skenv.renv.use("composer_sketch")
    await skenv.renv.run("design_draft")
    sketch = skenv.renv.current(class_id)
    assert sketch is not None
    first = Provenance.model_validate_json(json.dumps(skenv.revision(sketch).provenance))
    assert first.origin == "sketch"

    answered = await change(skenv, class_id, "composer_sketch_revised", ANSWER)
    assert answered != sketch
    changed = await change(skenv, class_id, "composer_sketch_again", CHANGE)
    assert changed not in (sketch, answered)
    for revision in (answered, changed):
        again = Provenance.model_validate_json(json.dumps(skenv.revision(revision).provenance))
        assert again.origin == "sketch"
    # The song slots stay the fill scripts' eight, whatever the changes were.
    roles = slot_roles(skenv.revision(changed).content)
    assert roles == slot_roles(skenv.revision(sketch).content)
    assert len(roles) == 8


async def change(skenv: SketchEnv, class_id: UUID, script_name: str, text: str) -> UUID:
    """The page's driver: store the message, submit it, let the worker run its revise."""
    base = f"/api/classes/{class_id}/pending-messages"
    message = uuid.uuid4()
    stored = await skenv.renv.browser.post(base, {"message_id": str(message), "body": text})
    assert stored.status_code == 201, stored.text
    made = await skenv.renv.browser.post(f"{base}/{message}/submit")
    assert made.status_code == 201, made.text
    skenv.renv.use(script_name)
    await skenv.renv.run("revise_draft")
    row = skenv.renv.denv.ops.row(UUID(made.json()["job_id"]))
    assert (row.state, row.error_category) == ("completed", None)
    current = skenv.renv.current(class_id)
    assert current is not None
    return current
