"""``interpret_request`` with the composer flow (ADR 0038 sections 2 and 3, #1185) through the jobs
API and the worker side, against Postgres as ``classbeat_app``, the fake MOWL and
``FakeProvider``: with ``composer_flow`` off (the default) a request with attached music is refused
``feature_off`` and the reading is stored exactly as before, whatever the model reports; with it on
the attached music is admitted and the pinned songs and questions are stored and read back.
``GET /api/connections`` carries the effective flag. Never the network, a real account or a
model."""

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import composer, music_kind
from interprettools import REQUEST, InterpretEnv, interpreter
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")

PLAYLIST = "0123456789abcdefABCDEF"
SOURCES = [{"kind": "spotify_playlist", "id": PLAYLIST}]
COMPOSED: dict[str, Any] = {
    "climbs": {"value": 1, "origin": "inferred"},
    "sprints": {"value": 2, "origin": "inferred"},
    "pinned_songs": [{"song": "one climb", "at": "two sprints", "origin": "explicit"}],
    "questions": [
        {
            "topic": "length",
            "text": "How long?",
            "options": ["30 minutes", "45 minutes"],
            "assumed_index": 1,
        },
        {
            "topic": "other",
            "text": "Games on the bike?",
            "options": ["No games", "Add one game"],
            "assumed_index": 0,
        },
    ],
}


def flag(renv: ReviseEnv, value: Any) -> None:
    """The stored ``composer_flow`` (JSON), or no row for ``None``."""
    renv.sql("DELETE FROM app_settings WHERE key = 'composer_flow'")
    if value is not None:
        renv.sql(
            "INSERT INTO app_settings (key, value) VALUES ('composer_flow', CAST(:v AS jsonb))",
            v=json.dumps(value),
        )


@pytest.fixture(autouse=True)
def composer_off(renv: ReviseEnv) -> Iterator[None]:
    """The composer off, the soundless switch off and the web's ``fake`` covering the user's plan
    (#1484: the composer is available when its pair is on **and** the plan covers the sketch; the
    switch is not read)."""
    composer.configure(None)
    music_kind.configure(music_kind.SoundlessGate(enabled=False, fake_covers=True))
    flag(renv, None)
    try:
        yield
    finally:
        composer.configure(None)
        music_kind.configure(None)
        flag(renv, None)


def turn_on(renv: ReviseEnv) -> None:
    composer.configure(composer.ComposerGate(enabled=True))
    flag(renv, "on")


async def shown(ienv: InterpretEnv) -> Any:
    got = await ienv.renv.browser.get("/api/connections")
    assert got.status_code == 200, got.text
    return got.json()["composer_flow"]


@pytest.mark.parametrize(
    ("environment_on", "stored", "expected"),
    [(False, "on", False), (True, None, False), (True, "off", False), (True, "on", True)],
)
async def test_connections_carry_the_effective_flag(
    ienv: InterpretEnv, environment_on: bool, stored: Any, expected: bool
) -> None:
    composer.configure(composer.ComposerGate(enabled=environment_on))
    flag(ienv.renv, stored)
    assert await shown(ienv) is expected


async def test_the_pair_on_without_the_plans_coverage_is_not_available(ienv: InterpretEnv) -> None:
    """#1484: the operator's pair is on, the user's plan covers nothing: ``composer_flow`` is
    false and attached music is refused ``feature_off``, whatever the soundless switch says."""
    for switch in (True, False):
        turn_on(ienv.renv)
        music_kind.configure(music_kind.SoundlessGate(enabled=switch, fake_covers=False))
        assert await shown(ienv) is False
        refused = await ienv.start(music_sources=SOURCES)
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["reason"] == "feature_off"


async def test_with_the_flag_off_attached_music_is_refused_before_anything_is_made(
    ienv: InterpretEnv,
) -> None:
    composer.configure(composer.ComposerGate(enabled=True))  # the switch alone is not enough
    before = ienv.counts()
    refused = await ienv.start(music_sources=SOURCES, music_override={"source": "pick"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "feature_off"
    assert ienv.counts() == before  # no class, no job, no message, no credit


async def test_with_the_flag_off_the_questions_are_stored_and_the_pins_are_not(
    ienv: InterpretEnv,
) -> None:
    """The model may report pins and questions; a job admitted with the flag off stores its
    questions (both flows ask, the Architect on #1536, 2026-11-26) and no pins."""
    class_id, job_id = await ienv.interpreted(COMPOSED)
    assert ienv.stored_input(job_id)["composer"] is False
    stored = ienv.interpretation(class_id)
    assert set(stored) == {"v", "job_id", "brief", "music", "items", "refusal", "questions"}
    view = (await ienv.renv.browser.get(f"/api/classes/{class_id}/interpretation")).json()
    assert view["pinned_songs"] == []
    assert [q["topic"] for q in view["questions"]] == [q["topic"] for q in stored["questions"]]
    assert [q["topic"] for q in stored["questions"]][-2:] == ["length", "other"]


async def test_attached_music_and_no_music_are_refused_invalid_input(ienv: InterpretEnv) -> None:
    turn_on(ienv.renv)
    before = ienv.counts()
    refused = await ienv.start(music_sources=SOURCES, music_override={"source": "none"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["reason"] == "invalid_input"
    assert ienv.counts() == before


async def test_composer_is_a_server_field(ienv: InterpretEnv) -> None:
    refused = await ienv.start(composer=True)
    assert refused.status_code == 422, refused.text


async def test_with_the_flag_on_pins_and_questions_are_stored_and_read_back(
    ienv: InterpretEnv,
) -> None:
    turn_on(ienv.renv)
    class_id, job_id = await ienv.interpreted(
        COMPOSED, music_sources=SOURCES, music_override={"source": "pick"}
    )
    given = ienv.stored_input(job_id)
    assert (given["composer"], given["music_sources"]) == (True, SOURCES)
    stored = ienv.interpretation(class_id)
    assert stored["pinned_songs"] == [{"title": "one climb", "at": "two sprints"}]
    assert [(q["topic"], q["text"], q["assumed_index"]) for q in stored["questions"]] == [
        ("length", "How long?", 1),
        ("other", "Games on the bike?", 0),
    ]
    assert (stored["brief"]["length_min"], stored["music"]) == (45, {"source": "pick"})
    view = (await ienv.renv.browser.get(f"/api/classes/{class_id}/interpretation")).json()
    assert [(p["title"], p["at"]) for p in view["pinned_songs"]] == [("one climb", "two sprints")]
    assert [q["topic"] for q in view["questions"]] == ["length", "other"]
    assert REQUEST.find("one climb") >= 0  # the pin is the instructor's own words


async def test_the_interpreter_is_told_how_many_playlists_are_attached(
    ienv: InterpretEnv,
) -> None:
    turn_on(ienv.renv)
    fake = interpreter(COMPOSED)
    ienv.models(fake)
    made = await ienv.start(music_sources=SOURCES, music_override={"source": "pick"})
    assert made.status_code == 201, made.text
    await ienv.run("interpret_request")
    message = fake.sent[0].spec.first_message
    assert json.loads(message)["attached_playlists"] == 1
    assert PLAYLIST not in message  # never an id
    assert UUID(made.json()["class_id"])


async def test_interpret_again_keeps_the_attached_music(ienv: InterpretEnv) -> None:
    """#1188: a composer request retried after a stored ``not_connected`` keeps ``music_sources``
    (it was dropped before: the restart copied the browser fields without it)."""
    turn_on(ienv.renv)
    class_id, _ = await ienv.interpreted(
        COMPOSED, music_sources=SOURCES, music_override={"source": "pick"}
    )
    stored = ienv.interpretation(class_id)
    stored["refusal"] = {"reason": "not_connected", "counts": {}}
    ienv.renv.sql(
        "UPDATE classes SET interpretation = CAST(:v AS jsonb) WHERE id = :c",
        v=json.dumps(stored),
        c=class_id,
    )
    made = await ienv.renv.browser.post(f"/api/classes/{class_id}/interpret-again", None)
    assert made.status_code == 201, made.text
    given = ienv.stored_input(UUID(made.json()["job_id"]))
    assert (given["music_sources"], given["composer"]) == (SOURCES, True)
