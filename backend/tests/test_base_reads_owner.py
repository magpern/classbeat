"""The revise and refit base reads and current-pointer swaps filter by the job's owner (#1453,
finding 5 of the #1457 review).

``test_user_context_guard`` checks how a ``UserContext`` is made, not the SQL: removing an
``owner_id = :owner_id`` clause from one of these statements would pass it. So each statement is
run here as the superuser (row-level security does not apply, the way
``test_swedebeat_adopt_owner.py`` proves an owner filter) against another owner's job, class and
draft, with this owner's id: it finds nothing. The control run with the class's own owner finds
the row, so the plant matches each statement and the empty result is the owner filter's doing.
A swap (``UPDATE``) runs in a transaction that is rolled back.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import TextClause, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from classbeat.classes import spotify_edit_draft
from classbeat.design import chat, refit, revise_base, spotdown_base
from classbeat.songs import revise_brief, revise_phase2
from jobtools import digest, new_class, new_job, new_user

pytestmark = pytest.mark.usefixtures("jobs_admin")

STATEMENTS: dict[str, TextClause] = {
    "refit._BASE": refit._BASE,  # pyright: ignore[reportPrivateUsage]
    "refit._BASE_MUSIC": refit._BASE_MUSIC,  # pyright: ignore[reportPrivateUsage]
    "refit._CAS": refit._CAS,  # pyright: ignore[reportPrivateUsage]
    "chat._BASE": chat._BASE,  # pyright: ignore[reportPrivateUsage]
    "chat._BASE_FOR_RUN": chat._BASE_FOR_RUN,  # pyright: ignore[reportPrivateUsage]
    "revise_base.CAS": revise_base.CAS,
    "spotdown_base._MUSIC": spotdown_base._MUSIC,  # pyright: ignore[reportPrivateUsage]
    "revise_brief._CLASS_BRIEF": revise_brief._CLASS_BRIEF,  # pyright: ignore[reportPrivateUsage]
    "revise_phase2._CAS": revise_phase2._CAS,  # pyright: ignore[reportPrivateUsage]
    "spotify_edit_draft._SELECT": spotify_edit_draft._SELECT,  # pyright: ignore[reportPrivateUsage]
    "spotify_edit_draft._MUSIC": spotify_edit_draft._MUSIC,  # pyright: ignore[reportPrivateUsage]
}


@pytest.fixture
async def around_rls(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as db:
            yield db
            await db.rollback()
    finally:
        await engine.dispose()


def plant(admin: Connection) -> dict[str, Any]:
    """Another owner's class with a current draft and a job on it: the statements' parameters."""
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    job_id = new_job(admin, owner, class_id)
    base = uuid.uuid4()
    admin.execute(
        text(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) VALUES (:id, :o, :c, 1, 'draft', '{}'::jsonb, "
            ":h, '{}'::jsonb, '{}'::jsonb)"
        ),
        {"id": base, "o": owner, "c": class_id, "h": digest("{}")},
    )
    admin.execute(
        text("UPDATE classes SET current_revision_id = :r WHERE id = :c"),
        {"r": base, "c": class_id},
    )
    return {
        "owner_id": owner,
        "job_id": job_id,
        "class_id": class_id,
        "base": base,
        "id": base,
        "revision_id": base,
        "music_source": json.dumps({"source": "none"}),
    }


def bound(statement: TextClause, values: dict[str, Any]) -> dict[str, Any]:
    names = statement._bindparams  # pyright: ignore[reportPrivateUsage]
    assert set(names) <= set(values), f"the plant lacks {set(names) - set(values)}"
    return {name: values[name] for name in names}


@pytest.mark.parametrize("name", sorted(STATEMENTS))
async def test_another_owners_rows_are_never_read(
    name: str, jobs_admin: Connection, around_rls: AsyncSession
) -> None:
    statement = STATEMENTS[name]
    theirs = plant(jobs_admin)
    mine = new_user(jobs_admin)
    found = await around_rls.execute(statement, bound(statement, {**theirs, "owner_id": mine}))
    assert found.first() is None
    control = await around_rls.execute(statement, bound(statement, theirs))
    assert control.first() is not None  # the plant matches: the owner filter is what refused
