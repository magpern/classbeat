"""``adopt_verified_mix`` looks up the job's class by job id AND owner (#477 review).

Row-level security also guards the lookup, so the test runs it as the superuser (RLS does not
apply), the way ``test_classes_swedebeat_match.py`` proves the owner filters of its reads.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from classbeat.swedebeat_handoff.adopt import adopt_verified_mix
from jobtools import new_job, new_user

pytestmark = pytest.mark.usefixtures("jobs_admin")


@pytest.fixture
async def around_rls(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as db:
            yield db
    finally:
        await engine.dispose()


async def test_another_owners_job_id_finds_no_class(
    jobs_admin: Connection, around_rls: AsyncSession
) -> None:
    mine, theirs = new_user(jobs_admin), new_user(jobs_admin)
    their_job = new_job(jobs_admin, theirs)
    # Self-check: with this session the other owner's job is visible.
    seen = await around_rls.execute(
        text("SELECT count(*) FROM jobs WHERE id = :j"), {"j": their_job}
    )
    assert seen.scalar_one() == 1
    fs: Any = SimpleNamespace(db=around_rls, lease=SimpleNamespace(owner_id=mine, job_id=their_job))
    with pytest.raises(NoResultFound):
        await adopt_verified_mix(fs, their_job, "mix-1", handoff_id=their_job)
