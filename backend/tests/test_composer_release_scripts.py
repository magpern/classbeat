"""The fake model scripts of the composer's release criteria (the ``composer-release-*``
e2e specs, roadmap Amendment 36, #1497) run through the real pipeline: the original request is
read as six sections, sketched with every segment tagged, and "make it seven bursts" gives
exactly seven efforts in section 3 (index 2) with the finishing block as it was.
Never the network or a model."""

import json
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import spotifycore
from agentfake import script
from classbeat.agent.brief import Brief
from classbeat.agent.providers.fake import FakeProvider
from composertools import ComposerEnv
from interprettools import INTERPRET_REQUEST
from sketchtools import SketchEnv
from songtools import BRIEF
from spotifyfake import USER_A
from test_composer_phase2 import approve

pytestmark = pytest.mark.usefixtures("jenv")

REQUEST = (
    "A 60 min class that starts off slow and builds up the momentum. A hilly section, that turns "
    "into a tough intervall section, thinking 5-7 short heavy bursts, back on flats, bulding up "
    "for a new hilly section, finishing of with a few bursts of power."
)
SEVEN = "Make it seven bursts."


def efforts(content: dict[str, Any], section: int) -> int:
    """The efforts (zone 5 or more, at most 90 s) in the segments tagged with ``section``."""
    return sum(
        1
        for seg in content["segments"]
        if seg.get("section") == section
        for i in seg["intervals"]
        if i["zone"] >= 5 and i["sec"] <= 90
    )


async def change(skenv: SketchEnv, class_id: UUID, script_name: str, text: str) -> UUID:
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


async def test_the_release_scripts_read_six_sections_sketch_them_and_make_it_seven(
    skenv: SketchEnv,
) -> None:
    ienv = skenv.ienv
    ienv.models(FakeProvider(script("composer_release_read")))
    made = await ienv.start(REQUEST)
    assert made.status_code == 201, made.text
    await ienv.run(INTERPRET_REQUEST.name)
    class_id = UUID(made.json()["class_id"])
    items = ienv.interpretation(class_id)["items"]
    shapes = [i for i in items if i["path"].startswith("shape.")]
    assert [i["path"] for i in shapes] == [f"shape.{n}" for n in range(6)]
    assert shapes[2]["value"]["count"] == {"min": 5, "max": 7}

    handed = await ienv.handoff(class_id)
    assert handed.status_code == 201, handed.text
    skenv.renv.use("composer_release_sketch")
    await skenv.renv.run("design_draft")
    sketch = skenv.renv.current(class_id)
    assert sketch is not None
    first = skenv.revision(sketch).content
    assert (efforts(first, 2), efforts(first, 5)) == (6, 3)

    seven = await change(skenv, class_id, "composer_release_seven", SEVEN)
    after = skenv.revision(seven).content
    assert (efforts(after, 2), efforts(after, 5)) == (7, 3)
    assert [s for s in after["segments"] if s.get("section") == 5] == [
        s for s in first["segments"] if s.get("section") == 5
    ]
    stated = skenv.revision(seven).provenance.get("stated")
    assert stated is not None
    assert stated["shape"][2]["count"] == {"min": 7, "max": 7}, json.dumps(stated)


# --- the fill and phase 2 on the e2e's world (the fake Spotify's catalogue, 240 s a song) ---

SCRIPTS = Path(__file__).parent / "agent_scripts"
SHAPE = json.loads((SCRIPTS / "composer_release_seven.json").read_text("utf-8"))["turns"][0][
    "calls"
][0]["input"]["shape"]


def load(name: str) -> dict[str, Any]:
    return json.loads((SCRIPTS / f"{name}.json").read_text(encoding="utf-8"))


async def seven_class(cenv: ComposerEnv, *, playlist: bool) -> tuple[Any, UUID, UUID]:
    """A composer class whose current revision is the seven-burst sketch, its brief the request's
    (60 minutes) with the layered shape of "make it seven bursts"."""
    fake = cenv.songenv.fake
    fake.catalogue = spotifycore._catalogue()  # pyright: ignore[reportPrivateUsage]
    fake.set_durations(240_000)
    sources = None
    if playlist:
        mine = fake.add_playlist(
            USER_A, "E2E Draft Climb", [f"spotify:track:{spotifycore.track_id(31)}"]
        )
        sources = [mine.id]
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider, sources=sources)
    sevens = load("composer_release_seven")["drafts"]["revised"]
    cenv.sql(
        "UPDATE class_revisions SET content = CAST(:c AS jsonb) WHERE id = :r",
        c=json.dumps(sevens),
        r=sketch,
    )
    stored = Brief.model_validate({**BRIEF, "length_min": 60}).stored()
    cenv.sql(
        "UPDATE classes SET brief = CAST(:b AS jsonb) WHERE id = :c",
        b=json.dumps(stored),
        c=class_id,
    )
    layer = Brief.model_validate(
        {
            **BRIEF,
            "length_min": 60,
            "shape": [{k: v for k, v in s.items() if k not in ("origin", "quote")} for s in SHAPE],
            "explicit": ["shape"],
        }
    )
    from classbeat.agent.stated_layer import layer_of

    cenv.sql(
        "UPDATE class_revisions SET provenance = provenance || CAST(:p AS jsonb) WHERE id = :r",
        p=json.dumps({"stated": layer_of(layer)}),
        r=sketch,
    )
    return rider, class_id, sketch


@pytest.mark.parametrize(
    ("name", "playlist"), [("composer_release_fill", False), ("composer_release_fill", True)]
)
async def test_the_fill_designs_seven_efforts_on_fifteen_songs_and_phase_two_keeps_them(
    cenv: ComposerEnv, name: str, playlist: bool
) -> None:
    rider, class_id, sketch = await seven_class(cenv, playlist=playlist)
    job_id, list_id, digest = await cenv.waiting(rider, class_id, load(name))
    content = cenv.content(list_id)
    assert len(content["songs"]) == 15
    if playlist:  # the reference playlist's song is ref 0 of the pool, the opening song
        assert content["songs"][0]["source"] == {"kind": "playlist", "playlist": 0}
    assert (content.get("unmet") or []) == []
    answer = await approve(rider, job_id, list_id, digest)
    assert answer.status_code == 200, answer.text
    done = await cenv.songenv.run(job_id)
    assert (done.state, done.error_category) == ("completed", None)
    draft = cenv.current(class_id)
    assert draft not in (None, sketch)
    saved = cenv.content(draft)
    assert (efforts(saved, 2), efforts(saved, 5)) == (7, 3)
    assert cenv.songenv.revision(draft).provenance["fit"] == "kept"


async def test_the_styled_reading_answers_the_music_question_of_the_plain_one(
    skenv: SketchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The e2e specs' confirm step (#1538): the plain reading has no hint of what to play, so the
    reading asks the closed music question and the page keeps "Design this class" off; the
    instructor types "rock", the request is read again with the styled script, and that reading
    has a taste, asks nothing and leaves the music decided, so the sketch can start."""
    from classbeat.design import interpret_admit

    async def admits(*_args: Any) -> bool:
        return True

    monkeypatch.setattr(interpret_admit, "song_picking", admits)
    ienv = skenv.ienv
    spotify = str(await ienv.connect_spotify())
    ienv.models(FakeProvider(script("composer_release_read")))
    made = await ienv.start(REQUEST, spotify=False, spotify_connection_id=spotify)
    assert made.status_code == 201, made.text
    await ienv.run(INTERPRET_REQUEST.name)
    class_id = UUID(made.json()["class_id"])
    plain = ienv.interpretation(class_id)
    assert [(q["topic"], q.get("code")) for q in plain["questions"]] == [("music", "which_music")]
    assert plain["music_decision"]["state"] == "undecided"

    body = {"interpret_job_id": plain["job_id"], "answers": [{"question_index": 0, "text": "rock"}]}
    again = await ienv.renv.browser.post(f"/api/classes/{class_id}/reread", body)
    assert again.status_code == 201, again.text
    ienv.models(FakeProvider(script("composer_release_read_styled")))
    await ienv.run(INTERPRET_REQUEST.name)
    styled = ienv.interpretation(class_id)
    assert styled["job_id"] == again.json()["job_id"]
    assert styled["brief"]["music_taste"]["genres"] == ["rock"]
    assert "questions" not in styled  # a hint: nothing is asked, and nothing is required
    assert (styled["music_decision"]["state"], styled["music_decision"]["choice"]) == (
        "decided",
        "request",
    )
    handed = await ienv.handoff(class_id, interpret_job_id=again.json()["job_id"])
    assert handed.status_code == 201, handed.text
