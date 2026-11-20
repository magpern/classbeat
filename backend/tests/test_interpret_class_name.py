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


async def test_a_name_too_long_once_cleaned_is_dropped_without_a_correction(
    ienv: InterpretEnv,
) -> None:
    # #1237: the bound applies after the cleaning; a name over it is dropped, the job completes on
    # its one scripted turn and the title stays the request's.
    class_id, _ = await ienv.interpreted(named("x" * 61))
    assert title(ienv, class_id) == interpret_policy.title_for(REQUEST)


async def test_a_name_over_the_limit_only_by_quotes_and_emoji_is_kept(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted(named('"' + "x" * 60 + '" \U0001f525'))
    assert title(ienv, class_id) == "x" * 60


async def test_a_link_like_name_is_never_the_title(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted(named("Ride https://pwned.example/win"))
    assert title(ienv, class_id) == interpret_policy.title_for(REQUEST)


async def test_a_title_the_user_chose_is_never_overwritten(ienv: InterpretEnv) -> None:
    class_id, _ = await ienv.interpreted(named("One climb, two sprints"), title="Tuesday hills")
    assert title(ienv, class_id) == "Tuesday hills"


async def test_the_workers_guard_alone_keeps_a_title_the_user_chose(ienv: InterpretEnv) -> None:
    # #1237: the user sent a title of their own; the class's title is then set to the request's
    # first words before the worker runs, so only the worker's ``given.title == from_request``
    # guard (not the store's title condition) stands between the name and the title.
    ienv.models(interpreter(named("One climb, two sprints")))
    made = await ienv.start(title="Tuesday hills")
    assert made.status_code == 201, made.text
    class_id = UUID(made.json()["class_id"])
    request_title = interpret_policy.title_for(REQUEST)
    ienv.sql("UPDATE classes SET title = :t WHERE id = :c", t=request_title, c=class_id)
    await ienv.run(INTERPRET_REQUEST.name)
    assert ienv.renv.denv.ops.row(UUID(made.json()["id"])).state == "completed"
    assert title(ienv, class_id) == request_title


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
