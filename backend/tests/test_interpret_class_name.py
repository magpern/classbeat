"""A new class's short name from the interpreter (issue #1233), through the jobs API and the
worker side against Postgres as ``classbeat_app``, the fake MOWL and ``FakeProvider``: the reading's
cleaned ``name`` becomes the class's title only while the title is still the request's first
words; with no name, a name the cleaning drops, a title the user chose or any other title, the
title is left as it is. Never the network, a real account or a model."""

import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import INTERPRET_REQUEST, interpret_policy
from interprettools import READING, REQUEST, InterpretEnv, interpreter

pytestmark = pytest.mark.usefixtures("jenv")


def named(name: Any) -> dict[str, Any]:
    return {**READING, "name": name}


def title(ienv: InterpretEnv, class_id: UUID) -> str:
    return ienv.sql("SELECT title FROM classes WHERE id = :c", c=class_id).scalar_one()


async def test_a_named_reading_names_the_class(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted(named('"One climb, two sprints" \U0001f6b4'))
    assert title(ienv, class_id) == "One climb, two sprints"  # cleaned: no quotes, no emoji
    assert "name" not in json.dumps(ienv.interpretation(class_id))  # display text, not a reading


async def test_a_reading_without_a_name_keeps_the_request_title(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted()
    assert title(ienv, class_id) == interpret_policy.title_for(REQUEST)


@pytest.mark.parametrize("name", ["\U0001f525\U0001f525", '""', " - "])
async def test_a_name_the_cleaning_drops_keeps_the_request_title(
    ienv: InterpretEnv, name: str
) -> None:
    class_id, _ = await ienv.interpreted(named(name))
    assert title(ienv, class_id) == interpret_policy.title_for(REQUEST)


async def test_a_name_longer_than_the_limit_is_corrected_not_stored(ienv: InterpretEnv) -> None:
    # The schema refuses it, so the run is a correction round; the script then stops and the job
    # fails, storing nothing: no class of the user has the name as its title.
    ienv.models(interpreter(named("x" * 61)))
    made = await ienv.start()
    assert made.status_code == 201, made.text
    await ienv.run(INTERPRET_REQUEST.name)
    assert ienv.renv.denv.ops.row(UUID(made.json()["id"])).state == "failed"
    found = "SELECT count(*) FROM classes WHERE owner_id = :o AND title = :t"
    assert ienv.sql(found, o=ienv.owner, t="x" * 61).scalar_one() == 0


async def test_a_title_the_user_chose_is_never_overwritten(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted(named("One climb, two sprints"), title="Tuesday hills")
    assert title(ienv, class_id) == "Tuesday hills"


async def test_a_title_changed_before_the_reading_is_stored_is_never_overwritten(
    ienv: InterpretEnv,
) -> None:
    # Any other title (a duplicate's, one set after the start) is not the request's first words.
    ienv.models(interpreter(named("One climb, two sprints")))
    made = await ienv.start()
    assert made.status_code == 201, made.text
    class_id = UUID(made.json()["class_id"])
    ienv.sql("UPDATE classes SET title = 'Copy of hills' WHERE id = :c", c=class_id)
    await ienv.run(INTERPRET_REQUEST.name)
    assert ienv.renv.denv.ops.row(UUID(made.json()["id"])).state == "completed"
    assert title(ienv, class_id) == "Copy of hills"


async def test_only_the_jobs_own_class_is_renamed(ienv: InterpretEnv) -> None:
    first, _ = await ienv.interpreted()
    second, _ = await ienv.interpreted(named("One climb, two sprints"))
    assert title(ienv, second) == "One climb, two sprints"
    assert title(ienv, first) == interpret_policy.title_for(REQUEST)
