"""``classes.dismissed_rules`` and the dismissal route (ADR 0017 section 4; P1b, #393):
the migration, the column's guards, the owner-scoped dismiss and undo, the 404 matrix, the id
check, the cap, and the deletion of the column. Fakes only."""

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from authtools import session_cookie
from classbeat.classes import service
from classbeat.classes.service import MAX_DISMISSED
from classbeat.design.rules.enums import RULE_IDS
from jobapi import Browser
from jobtools import new_class, new_user

pytestmark = pytest.mark.usefixtures("jobs_admin")

NOT_FOUND = {"detail": {"reason": "not_found"}}
FIRST, SECOND = RULE_IDS[0], RULE_IDS[1]  # real ids; the enum may grow


def test_the_enum_fits_the_cap_and_holds_only_plain_ids() -> None:
    assert 0 < len(RULE_IDS) <= MAX_DISMISSED
    assert len(set(RULE_IDS)) == len(RULE_IDS)
    assert all(r.isascii() and r.replace("_", "").isalnum() and len(r) <= 40 for r in RULE_IDS)


def test_the_column_is_a_short_array_of_strings_and_the_app_role_may_update_it(
    jobs_admin: Connection,
) -> None:
    owner = new_user(jobs_admin)
    class_id = new_class(jobs_admin, owner)
    update = text("UPDATE classes SET dismissed_rules = CAST(:v AS jsonb) WHERE id = :c")
    jobs_admin.execute(update, {"v": json.dumps(["a"] * MAX_DISMISSED), "c": class_id})
    for bad in (["a"] * (MAX_DISMISSED + 1), {"a": 1}, "a", [1], ["a", None], [["a"]]):
        with pytest.raises(IntegrityError):
            jobs_admin.execute(update, {"v": json.dumps(bad), "c": class_id})
    with pytest.raises(IntegrityError):  # SQL NULL
        jobs_admin.execute(
            text("UPDATE classes SET dismissed_rules = NULL WHERE id = :c"), {"c": class_id}
        )
    privilege = text(
        "SELECT has_column_privilege('classbeat_app', 'public.classes', 'dismissed_rules', "
        "'UPDATE')"
    )
    assert jobs_admin.execute(privilege).scalar_one() is True


class Rider:
    def __init__(self, admin: Connection, browser: Browser) -> None:
        self.admin, self.browser = admin, browser
        self.class_id = new_class(admin, browser.user_id)

    def stored(self) -> Any:
        return self.admin.execute(
            text("SELECT dismissed_rules, updated_at FROM classes WHERE id = :c"),
            {"c": self.class_id},
        ).one()

    async def put(self, rule: str, class_id: object = None) -> httpx.Response:
        return await self.call("PUT", rule, class_id)

    async def undo(self, rule: str, class_id: object = None) -> httpx.Response:
        return await self.call("DELETE", rule, class_id)

    async def call(self, method: str, rule: str, class_id: object) -> httpx.Response:
        path = f"/api/classes/{class_id or self.class_id}/dismissed-rules/{rule}"
        response = await self.browser.client.request(
            method, path, headers=await self.browser.headers()
        )
        self.browser.client.cookies.clear()
        return response


@pytest.fixture
async def two(browsers: Any, jobs_admin: Connection) -> tuple[Rider, Rider]:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    return Rider(jobs_admin, a), Rider(jobs_admin, b)


async def test_dismiss_and_undo_are_idempotent_and_keep_the_order(two: tuple[Rider, Rider]) -> None:
    mine, _ = two
    before = mine.stored().updated_at
    for rule, expected in ((SECOND, [SECOND]), (FIRST, [SECOND, FIRST]), (SECOND, [SECOND, FIRST])):
        answer = await mine.put(rule)
        assert (answer.status_code, answer.json()) == (200, {"dismissed_rules": expected})
    assert mine.stored().dismissed_rules == [SECOND, FIRST]
    answer = await mine.undo(SECOND)
    assert (answer.status_code, answer.json()) == (200, {"dismissed_rules": [FIRST]})
    answer = await mine.undo(SECOND)  # not dismissed: nothing changes
    assert (answer.status_code, answer.json()) == (200, {"dismissed_rules": [FIRST]})
    assert mine.stored().updated_at == before, "a dismissal does not reorder the class list"


async def test_another_users_and_unknown_classes_are_404_and_untouched(
    two: tuple[Rider, Rider],
) -> None:
    mine, theirs = two
    await theirs.put(FIRST)
    for target in (theirs.class_id, uuid.uuid4(), "nope"):
        for rule in (SECOND, FIRST, "not_a_rule"):  # the class check comes first
            for call in (mine.put, mine.undo):
                got = await call(rule, target)
                assert (got.status_code, got.json()) == (404, NOT_FOUND), (target, rule)
    assert theirs.stored().dismissed_rules == [FIRST]
    assert mine.stored().dismissed_rules == []


async def test_an_unknown_rule_id_is_422_and_nothing_is_stored(two: tuple[Rider, Rider]) -> None:
    mine, _ = two
    await mine.put(FIRST)
    for rule in ("not_a_rule", "WARMUP_SHORT", "warmup_short%20", "a" * 300, "x'y"):
        for call in (mine.put, mine.undo):
            got = await call(rule)
            assert (got.status_code, got.json()) == (422, {"detail": {"reason": "invalid_input"}})
    assert mine.stored().dismissed_rules == [FIRST]


async def test_the_cap_refuses_a_33rd_id(two: tuple[Rider, Rider], monkeypatch: Any) -> None:
    mine, _ = two
    ids = [f"rule_{i:02d}" for i in range(MAX_DISMISSED)]
    monkeypatch.setattr(service, "is_rule_id", lambda value: True)  # the enum is below 32
    for rule in ids:
        assert (await mine.put(rule)).status_code == 200
    assert (await mine.put(ids[0])).status_code == 200  # already there: no growth
    full = await mine.put("one_too_many")
    assert (full.status_code, full.json()) == (409, {"detail": {"reason": "limit_exceeded"}})
    assert mine.stored().dismissed_rules == ids
    assert (await mine.undo(ids[0])).status_code == 200
    assert (await mine.put("one_too_many")).status_code == 200


async def test_the_writes_need_csrf_and_a_session(two: tuple[Rider, Rider]) -> None:
    mine, _ = two
    path = f"/api/classes/{mine.class_id}/dismissed-rules/{FIRST}"
    for method in ("PUT", "DELETE"):
        no_csrf = await mine.browser.client.request(
            method, path, headers=session_cookie(mine.browser.token)
        )
        mine.browser.client.cookies.clear()
        assert no_csrf.status_code == 403
        assert (await mine.browser.client.request(method, path)).status_code in (401, 403)
    assert mine.stored().dismissed_rules == []


async def test_the_class_delete_takes_the_dismissals_with_it(two: tuple[Rider, Rider]) -> None:
    mine, _ = two
    await mine.put(FIRST)
    headers = await mine.browser.headers()
    done = await mine.browser.client.delete(f"/api/classes/{mine.class_id}", headers=headers)
    mine.browser.client.cookies.clear()
    assert done.status_code == 200
    gone = mine.admin.execute(text("SELECT 1 FROM classes WHERE id = :c"), {"c": mine.class_id})
    assert gone.first() is None
