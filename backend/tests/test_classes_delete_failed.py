"""Deleting a class whose design failed (issue #1134): a class with a ``failed`` job and no
version is the user's own and goes like any other; a queued job still refuses it
(``class_busy``); nobody else's class can be removed. Fakes only."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobapi import Browser
from jobtools import new_class, new_job

pytestmark = pytest.mark.usefixtures("jobs_admin")


async def delete(browser: Browser, class_id: object) -> httpx.Response:
    response = await browser.client.delete(
        f"/api/classes/{class_id}", headers=await browser.headers()
    )
    browser.client.cookies.clear()
    return response


def exists(admin: Connection, class_id: uuid.UUID) -> bool:
    row = admin.execute(text("SELECT 1 FROM classes WHERE id = :c"), {"c": class_id}).first()
    return row is not None


async def test_the_owner_deletes_a_class_whose_job_failed(
    browsers: Any, jobs_admin: Connection
) -> None:
    mine: Browser = await browsers(mowl=False)
    class_id = new_class(jobs_admin, mine.user_id)
    new_job(
        jobs_admin, mine.user_id, class_id, state="failed", error_category="internal",
        finished_at="2026-01-01",
    )  # fmt: skip
    answer = await delete(mine, class_id)
    assert answer.status_code == 200
    assert answer.json()["deleted"] is True
    assert not exists(jobs_admin, class_id)


async def test_a_class_with_a_job_still_queued_is_refused(
    browsers: Any, jobs_admin: Connection
) -> None:
    mine: Browser = await browsers(mowl=False)
    class_id = new_class(jobs_admin, mine.user_id)
    new_job(jobs_admin, mine.user_id, class_id)
    answer = await delete(mine, class_id)
    assert (answer.status_code, answer.json()) == (409, {"detail": {"reason": "class_busy"}})
    assert exists(jobs_admin, class_id)


async def test_another_users_failed_class_is_404_and_stays(
    browsers: Any, jobs_admin: Connection
) -> None:
    mine: Browser = await browsers(mowl=False)
    theirs: Browser = await browsers(mowl=False)
    class_id = new_class(jobs_admin, theirs.user_id)
    new_job(
        jobs_admin, theirs.user_id, class_id, state="failed", error_category="internal",
        finished_at="2026-01-01",
    )  # fmt: skip
    answer = await delete(mine, class_id)
    assert (answer.status_code, answer.json()) == (404, {"detail": {"reason": "not_found"}})
    assert exists(jobs_admin, class_id)
