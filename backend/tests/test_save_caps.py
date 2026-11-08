"""The caps on Save and Update per user (#577, ``classbeat.saving.caps``).

Neither kind takes a credit or a model call; without a cap a script with a stolen session could
keep writing programs to the user's MOWL account. Both kinds' ``admit`` count the user's
``save_workout`` and ``update_workout`` jobs created in the last hour and the last 24 hours
(rolling, by the database's clock, whatever became of them) and refuse at
``CLASSBEAT_SAVES_PER_HOUR`` or ``CLASSBEAT_SAVES_PER_DAY`` with 409 ``quota`` ``saves``: nothing is
created and MOWL is never called. Against the fake MOWL; never the network.
"""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from authtools import make_settings
from classbeat import saving
from classbeat.config import Settings
from classbeat.db import user_session
from classbeat.jobs.credits import QuotaRefused
from classbeat.quota import bounds
from classbeat.saving import kind as save_kind
from classbeat.saving import update as update_kind
from ctx import make_context
from jobtools import new_job
from optools import TOKEN_B
from savetools import FakeMowlPrograms, Saved, SaveEnv
from updatetools import UpdateEnv


@pytest.fixture
def caps(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def set_caps(hour: int, day: int) -> None:
        capped = bounds.Bounds(saves_per_hour=hour, saves_per_day=day)
        monkeypatch.setattr(bounds, "_bounds", capped)

    return set_caps


def plant(senv: SaveEnv, owner: UUID, kind: str, hours_ago: float = 0) -> UUID:
    """A finished job of ``kind`` created ``hours_ago`` (on a class of its own)."""
    job_id = new_job(
        senv.ops.admin,
        owner,
        kind=kind,
        state="cancelled",
        credit_state="none",
        finished_at=datetime.now(UTC),
    )
    age(senv, job_id, hours_ago)
    return job_id


def age(senv: SaveEnv, job_id: UUID, hours: float) -> None:
    senv.sql(
        "UPDATE jobs SET created_at = now() - make_interval(secs => :s) WHERE id = :id",
        s=hours * 3600,
        id=job_id,
    )


def jobs_of(senv: SaveEnv, owner: UUID) -> int:
    return senv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


def mowl_calls(fake: FakeMowlPrograms) -> int:
    return fake.write_count + len(fake.reads)


def test_the_defaults() -> None:
    fields = Settings.model_fields
    assert (fields["saves_per_hour"].default, fields["saves_per_day"].default) == (30, 100)
    assert (bounds.Bounds().saves_per_hour, bounds.Bounds().saves_per_day) == (30, 100)
    from_settings = bounds.Bounds.from_settings(
        make_settings("postgresql+psycopg://u@localhost/db", saves_per_hour=4, saves_per_day=9)
    )
    assert (from_settings.saves_per_hour, from_settings.saves_per_day) == (4, 9)


def test_the_refusal_body() -> None:
    refused = QuotaRefused("saves", limit=30, used=30, window="hour")
    assert refused.detail() == {"quota": "saves", "limit": 30, "used": 30, "window": "hour"}


async def refused_save(senv: SaveEnv, save_browser: Any, saved: Saved) -> dict[str, Any]:
    browser = await save_browser(saved)
    before = jobs_of(senv, saved.owner)
    answer = await browser.save()
    assert answer.status_code == 409, answer.text
    assert jobs_of(senv, saved.owner) == before  # nothing created
    return answer.json()["detail"]


async def test_save_at_the_hour_cap_is_409_and_creates_nothing(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, save_browser: Any, caps: Any
) -> None:
    caps(hour=2, day=10)
    saved = await senv.saved_class()
    plant(senv, saved.owner, "save_workout")
    plant(senv, saved.owner, "update_workout", hours_ago=0.9)  # Update counts with Save
    calls = mowl_calls(fake_programs)
    detail = await refused_save(senv, save_browser, saved)
    assert detail == {
        "reason": "quota",
        "quota": "saves",
        "limit": 2,
        "used": 2,
        "window": "hour",
    }
    assert mowl_calls(fake_programs) == calls


@pytest.fixture
def plan_caps(senv: SaveEnv) -> Iterator[Callable[..., None]]:
    """Set ``pro``'s own caps; ``plans`` is shared by the whole database, so reset them after."""

    def set_caps(hour: int | None, day: int | None) -> None:
        senv.sql(
            "UPDATE plans SET saves_per_hour = :h, saves_per_day = :d WHERE id = 'pro'",
            h=hour,
            d=day,
        )

    yield set_caps
    set_caps(None, None)


async def test_a_plan_value_tightens_the_setting(
    senv: SaveEnv, save_browser: Any, caps: Any, plan_caps: Any
) -> None:
    """The setting allows 10 an hour; the user's plan (``pro``) allows 1: the plan's value wins and
    the 409 body is the same shape, with the effective cap as ``limit`` (#806)."""
    caps(hour=10, day=10)
    saved = await senv.saved_class()
    plan_caps(hour=1, day=None)
    plant(senv, saved.owner, "save_workout")
    detail = await refused_save(senv, save_browser, saved)
    assert detail == {"reason": "quota", "quota": "saves", "limit": 1, "used": 1, "window": "hour"}


async def test_a_plan_day_value_tightens_the_setting(
    senv: SaveEnv, save_browser: Any, caps: Any, plan_caps: Any
) -> None:
    caps(hour=10, day=10)
    saved = await senv.saved_class()
    plan_caps(hour=None, day=2)
    plant(senv, saved.owner, "save_workout", hours_ago=3)
    plant(senv, saved.owner, "update_workout", hours_ago=4)
    detail = await refused_save(senv, save_browser, saved)
    assert (detail["limit"], detail["used"], detail["window"]) == (2, 2, "day")


async def test_a_plan_value_above_the_setting_does_not_loosen_it(
    senv: SaveEnv, save_browser: Any, caps: Any, plan_caps: Any
) -> None:
    caps(hour=1, day=10)
    saved = await senv.saved_class()
    plan_caps(hour=500, day=900)
    plant(senv, saved.owner, "save_workout")
    detail = await refused_save(senv, save_browser, saved)
    assert (detail["limit"], detail["window"]) == (1, "hour")


async def test_a_plan_without_a_value_uses_the_setting(
    senv: SaveEnv, save_browser: Any, caps: Any, plan_caps: Any
) -> None:
    caps(hour=2, day=10)
    saved = await senv.saved_class()
    plan_caps(hour=None, day=None)
    plant(senv, saved.owner, "save_workout")
    browser = await save_browser(saved)
    assert (await browser.save()).status_code == 201  # one used of two


def test_the_plan_columns_are_range_checked(senv: SaveEnv, plan_caps: Any) -> None:
    for column, bad in (("saves_per_hour", 0), ("saves_per_hour", 1001), ("saves_per_day", 10001)):
        with pytest.raises(Exception, match=f"plans_{column}"):
            senv.sql(f"UPDATE plans SET {column} = :v WHERE id = 'pro'", v=bad)  # noqa: S608


async def test_the_hour_rolls(senv: SaveEnv, save_browser: Any, caps: Any) -> None:
    caps(hour=1, day=10)
    saved = await senv.saved_class()
    earlier = plant(senv, saved.owner, "save_workout")
    await refused_save(senv, save_browser, saved)
    age(senv, earlier, 1.05)  # just over an hour ago
    browser = await save_browser(saved)
    assert (await browser.save()).status_code == 201


async def test_save_at_the_day_cap_is_409_and_the_day_rolls(
    senv: SaveEnv, save_browser: Any, caps: Any
) -> None:
    caps(hour=10, day=3)
    saved = await senv.saved_class()
    plant(senv, saved.owner, "save_workout", hours_ago=2)
    plant(senv, saved.owner, "update_workout", hours_ago=5)
    oldest = plant(senv, saved.owner, "save_workout", hours_ago=23.9)
    detail = await refused_save(senv, save_browser, saved)
    assert (detail["quota"], detail["limit"], detail["used"], detail["window"]) == (
        "saves",
        3,
        3,
        "day",
    )
    age(senv, oldest, 24.1)
    browser = await save_browser(saved)
    assert (await browser.save()).status_code == 201


async def test_other_kinds_and_other_users_do_not_count(
    senv: SaveEnv, save_browser: Any, caps: Any
) -> None:
    caps(hour=1, day=1)
    saved = await senv.saved_class()
    other = await senv.saved_class(TOKEN_B)
    for _ in range(3):
        plant(senv, saved.owner, "design_draft")
        plant(senv, other.owner, "save_workout")
    browser = await save_browser(saved)
    assert (await browser.save()).status_code == 201


async def test_a_replay_is_the_same_job_and_a_new_request_is_refused(
    senv: SaveEnv, save_browser: Any, caps: Any
) -> None:
    """The idempotency key is checked before ``admit``: a replay of a request created under the cap
    still answers its job; once that job ended, a new request counts it and is refused."""
    caps(hour=1, day=10)
    saved = await senv.saved_class()
    browser = await save_browser(saved)
    key = uuid.uuid4().hex
    first = await browser.save(key)
    assert first.status_code == 201
    again = await browser.save(key)
    assert (again.status_code, again.json()["id"]) == (200, first.json()["id"])
    cancelled = await browser.browser.post(f"/api/jobs/{first.json()['id']}/cancel", {})
    assert cancelled.status_code == 200, cancelled.text
    refused = await browser.save()
    assert refused.status_code == 409
    assert refused.json()["detail"]["quota"] == "saves"


async def test_update_is_capped_too(uenv: UpdateEnv, senv: SaveEnv, caps: Any) -> None:
    live = await uenv.live()  # one real Save job: it counts
    saved = live.saved
    ctx = make_context(saved.owner, connections=[("mowl", saved.connection)])
    update_in = saving.UpdateWorkoutInput.model_validate(live.input())
    save_in = saving.SaveWorkoutInput.model_validate(saved.input())
    caps(hour=2, day=10)
    async with user_session(senv.ops.factory, ctx) as db:
        assert isinstance(await update_kind.admit(db, ctx, update_in), update_kind.Admitted)
    plant(senv, saved.owner, "update_workout")
    for admit, parsed in ((update_kind.admit, update_in), (save_kind.admit, save_in)):
        async with user_session(senv.ops.factory, ctx) as db:
            with pytest.raises(QuotaRefused) as refused:
                await admit(db, ctx, parsed)
        assert refused.value.detail() == {"quota": "saves", "limit": 2, "used": 2, "window": "hour"}
