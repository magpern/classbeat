"""The owner and class filter of ``design.chat._STATED`` (#907 note, #1023): the walk up the
``parent_id`` chain for the nearest ``provenance.stated`` reads only rows of the asked owner and
class. Postgres, run as the superuser so row-level security does not hide the other rows and only
the statement's own filters are under test; the class is built through the real revise path.

Not covered: the owner filter of the recursive step. ``class_revisions_parent_fkey`` is
``(owner_id, parent_id)``, so a revision cannot have a parent of another owner and no row can
reach it; the filter is defence in depth behind the foreign key and row-level security."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classbeat.design.chat import stated_layer
from revisetools import ReviseEnv
from test_duplicate_stated import revised_to_25
from test_revise_stated import provenance, stated_class

pytestmark = pytest.mark.usefixtures("jenv")


@pytest.fixture
async def anyone(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def layer(
    anyone: async_sessionmaker[AsyncSession], owner: UUID, class_id: UUID, base: UUID
) -> dict[str, Any] | None:
    async with anyone() as db:
        return await stated_layer(db, owner, class_id, base)


def child_of(renv: ReviseEnv, parent: Any, class_id: UUID, number: int) -> UUID:
    """A revision of ``class_id`` (the same owner's) whose parent is ``parent``, in another class
    if ``class_id`` differs. It records no layer of its own."""
    child = uuid4()
    own = {k: v for k, v in provenance(parent).items() if k not in ("stated", "interpretation")}
    renv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
        "music, provenance, job_id, parent_id) SELECT :id, owner_id, :class_id, :number, kind, "
        "content, content_hash, music, CAST(:p AS jsonb), job_id, id FROM class_revisions "
        "WHERE id = :parent",
        id=child,
        class_id=class_id,
        number=number,
        p=json.dumps(own),
        parent=parent.id,
    )
    return child


async def test_the_layer_is_found_for_the_owner_and_class_and_walks_up_to_it(
    renv: ReviseEnv, anyone: async_sessionmaker[AsyncSession]
) -> None:
    class_id, second = await revised_to_25(renv)
    assert (await layer(anyone, renv.owner, class_id, second.id)) == provenance(second)["stated"]
    refit = child_of(renv, second, class_id, 50)  # records no layer: the walk goes to its parent
    found = await layer(anyone, renv.owner, class_id, refit)
    assert found is not None
    assert found["length_min"] == 25


async def test_another_owner_reads_no_layer(
    renv: ReviseEnv, anyone: async_sessionmaker[AsyncSession]
) -> None:
    class_id, second = await revised_to_25(renv)
    stranger = await renv.denv.ops.user()
    assert await layer(anyone, stranger, class_id, second.id) is None
    refit = child_of(renv, second, class_id, 50)
    assert await layer(anyone, stranger, class_id, refit) is None


async def test_another_class_of_the_owner_reads_no_layer(
    renv: ReviseEnv, anyone: async_sessionmaker[AsyncSession]
) -> None:
    class_id, second = await revised_to_25(renv)
    other_class, _ = await stated_class(renv)
    assert other_class != class_id
    assert await layer(anyone, renv.owner, other_class, second.id) is None


async def test_the_walk_does_not_cross_into_another_class(
    renv: ReviseEnv, anyone: async_sessionmaker[AsyncSession]
) -> None:
    """A revision of class B whose ``parent_id`` points into class A (the foreign key allows it:
    it only ties the owner) does not inherit A's layer."""
    class_id, second = await revised_to_25(renv)
    other_class, _ = await stated_class(renv)
    stray = child_of(renv, second, other_class, 90)
    assert await layer(anyone, renv.owner, other_class, stray) is None
    assert await layer(anyone, renv.owner, class_id, second.id) is not None
