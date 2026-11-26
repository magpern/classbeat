"""A running class's reading stores no music decision (ADR 0038 amendment of 2026-11-24,
decision 5, #1495; #1510): the question about the music is cycling only. Through the job
(``interpret_kind.run``) with the composer on, against Postgres and ``FakeProvider``; the same
reading as a cycling class stores one. Never the network, a real account or a model."""

import json
from collections.abc import Iterator

import pytest

from classbeat.design import interpret_admit, sport
from sketchtools import SketchEnv

pytestmark = pytest.mark.usefixtures("jenv", "picking")


@pytest.fixture
def picking(monkeypatch: pytest.MonkeyPatch) -> None:
    async def admits(*_args: object) -> bool:
        return True

    monkeypatch.setattr(interpret_admit, "song_picking", admits)


@pytest.fixture
def running_on(skenv: SketchEnv) -> Iterator[None]:
    """The running switch on in web and worker, the fake covering, the admin flag ``on`` (as
    ``test_running_jobs``)."""
    sport.configure(sport.SportGate(running_enabled=True, fake_covers=True))
    skenv.sql("DELETE FROM app_settings WHERE key = 'running_enabled'")
    skenv.sql(
        "INSERT INTO app_settings (key, value) VALUES ('running_enabled', CAST(:v AS jsonb))",
        v=json.dumps("on"),
    )
    try:
        yield
    finally:
        sport.configure(None)
        skenv.sql("DELETE FROM app_settings WHERE key = 'running_enabled'")


@pytest.mark.usefixtures("running_on")
async def test_a_running_class_read_with_the_composer_gets_no_music_decision(
    skenv: SketchEnv,
) -> None:
    class_id, job_id = await skenv.ienv.interpreted(spotify=False, sport_override="running")
    assert skenv.ienv.stored_input(job_id)["composer"] is True  # the composer was on for the job
    stored = skenv.ienv.interpretation(class_id)
    assert stored["brief"]["sport"] == "running"
    assert stored.get("music_decision") is None


async def test_the_same_reading_as_a_cycling_class_gets_one(skenv: SketchEnv) -> None:
    class_id, _ = await skenv.ienv.interpreted(spotify=False)
    stored = skenv.ienv.interpretation(class_id)
    assert stored["brief"]["sport"] == "cycling"
    assert stored["music_decision"]["state"] == "undecided"
