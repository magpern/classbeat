"""The composer's reading window (ADR 0038 section 2, #1206) against Postgres: while a class has no
interpretation yet, ``composer`` in the class answer follows its ``interpret_request`` job's input
(set at admit) and the ``composer_flow`` gate, so the workspace shows from the first second. The
old rules hold: a class whose job input has no composer marker, a class with the flag off, a
"No music" class, a job that ended without a reading and every class with a stored interpretation.
Never the network or a model."""

import json
import secrets
import uuid
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import composer
from composertools import ComposerEnv, composer_flag, digest
from songtools import Rider

pytestmark = pytest.mark.usefixtures("jenv")


async def reading(
    cenv: ComposerEnv,
    rider: Rider,
    *,
    state: str = "running",
    extra: dict[str, Any] | None = None,
    composing: bool | None = True,
) -> UUID:
    """A class whose reading is in progress: no interpretation, no revision, one job."""
    class_id = uuid.uuid4()
    cenv.sql(
        "INSERT INTO classes (id, owner_id, title) VALUES (:c, :o, 'New class')",
        c=class_id,
        o=rider.user_id,
    )
    given: dict[str, Any] = {"request": "A 30 minute class", **(extra or {})}
    if composing is not None:
        given["composer"] = composing
    cenv.sql(
        "INSERT INTO jobs (id, owner_id, kind, state, class_id, input, idempotency_key, "
        "request_hash, credit_state, leased_by, lease_expires_at, error_category, finished_at) "
        "VALUES (:j, :o, 'interpret_request', :s, :c, CAST(:i AS jsonb), :k, :h, 'none', "
        "CASE WHEN :s = 'running' THEN 'worker' END, "
        "CASE WHEN :s = 'running' THEN now() + interval '1 minute' END, "
        "CASE WHEN :s = 'failed' THEN 'internal' END, "
        "CASE WHEN :s IN ('failed', 'cancelled', 'expired') THEN now() END)",
        j=uuid.uuid4(),
        o=rider.user_id,
        s=state,
        c=class_id,
        i=json.dumps(given),
        k=secrets.token_urlsafe(18),
        h=digest("reading"),
    )
    return class_id


async def answer(rider: Rider, class_id: UUID) -> dict[str, Any]:
    got = await rider.browser.get(f"/api/classes/{class_id}")
    assert got.status_code == 200, got.text
    return got.json()


@pytest.mark.parametrize("state", ["queued", "running"])
async def test_composer_is_true_while_the_reading_runs(cenv: ComposerEnv, state: str) -> None:
    rider = await cenv.songenv.rider()
    class_id = await reading(cenv, rider, state=state)
    seen = await answer(rider, class_id)
    assert (seen["composer"], seen["fill_songs"]) == (True, None)


async def test_composer_is_false_with_the_flag_off(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id = await reading(cenv, rider)
    composer_flag(cenv.songenv, "off")
    assert (await answer(rider, class_id))["composer"] is False
    composer_flag(cenv.songenv, "on")
    composer.configure(composer.ComposerGate(enabled=False))  # the environment's switch
    assert (await answer(rider, class_id))["composer"] is False
    composer.configure(composer.ComposerGate(enabled=True))
    assert (await answer(rider, class_id))["composer"] is True  # control: both on


@pytest.mark.parametrize("composing", [False, None])
async def test_a_job_without_the_composer_marker_is_not_a_composer_class(
    cenv: ComposerEnv, composing: bool | None
) -> None:
    rider = await cenv.songenv.rider()
    class_id = await reading(cenv, rider, composing=composing)  # None: admitted before #1185
    assert (await answer(rider, class_id))["composer"] is False


async def test_a_class_with_no_interpret_job_is_not_a_composer_class(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    made = await rider.create()
    assert (await answer(rider, made.json()["class_id"]))["composer"] is False


@pytest.mark.parametrize("state", ["failed", "cancelled", "expired"])
async def test_a_reading_that_ended_without_an_interpretation_is_as_before(
    cenv: ComposerEnv, state: str
) -> None:
    rider = await cenv.songenv.rider()
    class_id = await reading(cenv, rider, state=state)
    assert (await answer(rider, class_id))["composer"] is False


async def test_a_no_music_class_is_not_a_composer_class_in_the_window(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id = await reading(cenv, rider, extra={"music_override": {"source": "none"}})
    assert (await answer(rider, class_id))["composer"] is False


async def test_the_window_is_owner_scoped(cenv: ComposerEnv) -> None:
    mine = await cenv.songenv.rider()
    other = await cenv.songenv.rider()
    class_id = await reading(cenv, mine)
    assert (await other.browser.get(f"/api/classes/{class_id}")).status_code == 404


async def test_once_the_interpretation_is_stored_the_job_input_decides_as_before(
    cenv: ComposerEnv,
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)  # a completed composer job, interpretation stored
    assert (await answer(rider, class_id))["composer"] is True
    composer_flag(cenv.songenv, "off")  # the stored reading keeps its answer: not the flag
    assert (await answer(rider, class_id))["composer"] is True
    cenv.sql(
        "UPDATE jobs SET input = jsonb_set(input, '{composer}', 'false') "
        "WHERE class_id = :c AND kind = 'interpret_request'",
        c=class_id,
    )
    assert (await answer(rider, class_id))["composer"] is False
