"""The class row the jobs API creates for a job that names a title (ADR 0005 section 2).

``user.create`` inserts it in the create transaction, under the owner lock, after every check and
``admit`` (a refusal creates no class). The duplicate route is the only other place a class is
inserted (``tests/test_insert_guard.py``).
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_NEW_CLASS = text("INSERT INTO classes (id, owner_id, title) VALUES (:id, :owner_id, :title)")


async def insert_class(db: AsyncSession, owner_id: UUID, class_id: UUID, title: str | None) -> None:
    await db.execute(_NEW_CLASS, {"id": class_id, "owner_id": owner_id, "title": title})
