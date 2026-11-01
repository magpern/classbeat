"""ADR 0017 section 4 (P5, #397): the revision ``GET`` carries pattern notes computed on read from
the stored brief and the CURRENT profile, minus the dismissed rules of the class, with a flag when
the revision's recorded digest differs. Nothing is stored per revision. Synthetic data only."""

import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.agent.schemas import Brief
from classbeat.classes import pattern_notes
from classbeat.design.rules import Registry, default_registry
from classbeat.design.rules import profile as profile_module
from jobapi import Browser
from jobtools import new_class
from test_classes_api import NOT_FOUND, make_current, revision

pytestmark = pytest.mark.usefixtures("jobs_admin")

PROFILE = "cycling.plain.30"
BRIEF = {"length_min": 30}
KEYS = {"rule", "severity", "metric", "value", "bounds", "explained_by_brief"}
SET_BRIEF = "UPDATE classes SET brief = CAST(:b AS jsonb) WHERE id = :c"
SET_DISMISSED = "UPDATE classes SET dismissed_rules = CAST(:v AS jsonb) WHERE id = :c"
EMPTY = {"hits": [], "flag": False}


class Rider:
    def __init__(self, admin: Connection, browser: Browser) -> None:
        self.admin, self.browser = admin, browser
        self.class_id = new_class(admin, browser.user_id)
        self.sql(SET_BRIEF, b=json.dumps(BRIEF))
        self.r1 = self.add(1)
        make_current(admin, self.class_id, self.r1)

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), {"c": self.class_id, **params})

    def add(self, number: int, digest: str | None = None) -> UUID:
        made = revision(self.admin, self.browser.user_id, self.class_id, number)
        if digest is not None:
            seen = {"rules": {"profile": PROFILE, "version": 1, "digest": digest}}
            self.sql(
                "UPDATE class_revisions SET provenance = provenance || CAST(:p AS jsonb) "
                "WHERE id = :r",
                p=json.dumps(seen),
                r=made,
            )
        return made

    async def get(self, revision_id: UUID | None = None, class_id: Any = None) -> Any:
        return await self.browser.get(
            f"/api/classes/{class_id or self.class_id}/revisions/{revision_id or self.r1}"
        )

    async def notes(self, revision_id: UUID | None = None) -> dict[str, Any]:
        body = (await self.get(revision_id)).json()
        return {"hits": body["rule_hits"], "flag": body["notes_use_current_rules"]}

    async def rules(self, revision_id: UUID | None = None) -> list[str]:
        return [h["rule"] for h in (await self.notes(revision_id))["hits"]]


@pytest.fixture
async def two(browsers: Any, jobs_admin: Connection) -> tuple[Rider, Rider]:
    a: Browser = await browsers(mowl=False)
    b: Browser = await browsers(mowl=False)
    return Rider(jobs_admin, a), Rider(jobs_admin, b)


def use_registry(monkeypatch: pytest.MonkeyPatch, registry: Registry) -> None:
    monkeypatch.setattr(profile_module, "default_registry", lambda: registry)
    monkeypatch.setattr(pattern_notes, "default_registry", lambda: registry)


async def test_a_revision_shows_the_notes_of_its_stored_brief(two: tuple[Rider, Rider]) -> None:
    mine, _ = two
    seen = await mine.notes()
    assert 0 < len(seen["hits"]) <= 5
    assert all(set(h) == KEYS for h in seen["hits"])
    assert "warmup_short" in await mine.rules()
    assert seen["flag"] is False


async def test_the_notes_come_from_the_stored_brief_only(two: tuple[Rider, Rider]) -> None:
    mine, _ = two
    mine.sql(SET_BRIEF, b=json.dumps({**BRIEF, "text": "ride it hard"}))
    hits = {h["rule"]: h for h in (await mine.notes())["hits"]}
    assert hits["warmup_short"]["explained_by_brief"] is True
    assert hits["warmup_short"]["severity"] == "info"  # lowered, never hidden
    counted = mine.sql("SELECT count(*) FROM class_revisions WHERE class_id = :c")
    assert counted.scalar_one() == 1  # nothing was stored for the view


async def test_another_users_revision_and_class_are_404(two: tuple[Rider, Rider]) -> None:
    mine, theirs = two
    for rev, cls in (
        (theirs.r1, theirs.class_id),
        (theirs.r1, mine.class_id),
        (mine.r1, theirs.class_id),
    ):
        got = await mine.get(rev, cls)
        assert (got.status_code, got.json()) == (404, NOT_FOUND)


async def test_a_dismissed_rule_stays_hidden_on_the_next_revision(
    two: tuple[Rider, Rider],
) -> None:
    mine, _ = two
    assert "warmup_short" in await mine.rules()
    mine.sql(SET_DISMISSED, v='["warmup_short"]')
    second = mine.add(2)
    for rev in (mine.r1, second):
        assert "warmup_short" not in await mine.rules(rev)
    mine.sql(SET_DISMISSED, v="[]")
    assert "warmup_short" in await mine.rules(second)


async def test_a_dismissal_makes_room_under_the_cap(two: tuple[Rider, Rider]) -> None:
    mine, _ = two
    first = await mine.rules()
    assert len(first) == 5
    mine.sql(SET_DISMISSED, v=json.dumps(first[:1]))
    after = await mine.rules()
    assert len(after) == 5
    assert first[0] not in after


async def test_a_changed_profile_changes_old_notes_and_sets_the_flag(
    two: tuple[Rider, Rider], monkeypatch: pytest.MonkeyPatch
) -> None:
    mine, _ = two
    real = default_registry()
    old = mine.add(2, digest=real.digests[PROFILE])
    assert (await mine.notes(old))["flag"] is False
    assert "warmup_short" in await mine.rules(old)
    profile = real.profiles[PROFILE]
    kept = [r for r in profile.rules if r.rule != "warmup_short"]
    changed = profile.model_copy(update={"rules": kept})
    digests = {**real.digests, PROFILE: "f" * 64}
    use_registry(monkeypatch, Registry({**real.profiles, PROFILE: changed}, digests, 1))
    seen = await mine.notes(old)
    assert seen["flag"] is True
    assert "warmup_short" not in await mine.rules(old)
    assert (await mine.notes())["flag"] is False  # no recorded digest: nothing differs


async def test_no_profile_gives_no_notes_and_no_flag(two: tuple[Rider, Rider]) -> None:
    mine, _ = two
    old = mine.add(2, digest="a" * 64)
    mine.sql(SET_BRIEF, b=json.dumps({"length_min": 90}))  # no profile for this length
    assert await mine.notes(old) == EMPTY
    mine.sql("UPDATE classes SET brief = NULL WHERE id = :c")
    assert await mine.notes(old) == EMPTY


async def test_a_profile_that_no_longer_exists_gives_no_notes_and_no_flag(
    two: tuple[Rider, Rider], monkeypatch: pytest.MonkeyPatch
) -> None:
    mine, _ = two
    old = mine.add(2, digest=default_registry().digests[PROFILE])
    use_registry(monkeypatch, Registry({}, {}, 0))
    assert await mine.notes(old) == EMPTY


def test_the_cap_is_applied_to_what_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation guard: the response holds at most MAX_HITS, even if the engine yields more."""
    spec = {
        "segments": [
            {"name": "a", "kind": "main", "intervals": [{"sec": 60, "zone": 7, "standing": True}]}
        ]
    }
    full = pattern_notes.pattern_notes(spec, Brief(length_min=30), [], {})
    assert len(full.hits) == 5
    monkeypatch.setattr(pattern_notes, "MAX_HITS", 2)
    monkeypatch.setattr(pattern_notes, "evaluate", lambda *a, **k: _many())
    assert len(pattern_notes.pattern_notes(spec, Brief(length_min=30), [], {}).hits) == 2


def _many() -> list[Any]:
    from classbeat.design.rules import evaluate

    spec = {
        "segments": [
            {"name": "a", "kind": "main", "intervals": [{"sec": 60, "zone": 7, "standing": True}]}
        ]
    }
    return evaluate(spec, Brief(length_min=30), default_registry().profiles[PROFILE])
