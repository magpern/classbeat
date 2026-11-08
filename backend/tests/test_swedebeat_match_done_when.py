# ruff: noqa: F811 - the fixtures are imported from test_swedebeat_verify and named as parameters
"""ADR 0016 sections 5 to 8 and B5: the matching done-when suite, hand-off side. Fakes only.

Two users, their MOWL tokens, matches and hand-offs never mix (read, approve, hand-off, verify,
override), and the retention of a match and of a hand-off: what goes with the class, with Disconnect
MOWL, with the hourly purge and with the account. The job-side isolation (the catalogue token, the
replace and the approval reads) is ``test_swedebeat_match_job_isolation.py``; Playwright runs
path (b) end to end in ``e2e/tests/swedebeat-match.spec.ts``.
"""

from typing import Any
from uuid import UUID

import pytest

from classbeat import auth
from classbeat.connections.repository import disconnect
from classbeat.db.retention import RETENTION
from ctx import make_context
from jobapi import Browser, new_key
from mowlfake import FakeMowl
from optools import TOKEN_A, TOKEN_B
from preftools import use_swedebeat
from test_swedebeat_verify import (  # noqa: F401 - the fixtures: env, work, henv
    HandEnv,
    _check,
    _start,
    env,
    henv,
    plant,
    work,
)

pytestmark = pytest.mark.usefixtures("jenv", "mowl_api", "analysis_slot_by_default")

SAME = "shared-mix-b5-0001"


async def rider_of(browsers: Any, henv: HandEnv, token: str) -> Browser:
    browser: Browser = await browsers()
    await use_swedebeat(henv.ops.factory, browser.user_id)  # #525: the user's own switch
    henv.ops.app.app.dependency_overrides.clear()
    browser.connection_id = await henv.ops.connect(browser.user_id, token)
    return browser


def one(henv: HandEnv, statement: str, **params: Any) -> Any:
    return henv.sql(statement, **params).scalar_one()


def match_of(henv: HandEnv, class_id: UUID) -> UUID:
    return one(
        henv,
        "SELECT id FROM class_revisions WHERE class_id = :c AND kind = 'swedebeat_match'",
        c=class_id,
    )


def handoffs(henv: HandEnv, owner: UUID) -> int:
    return one(henv, "SELECT count(*) FROM swedebeat_handoffs WHERE owner_id = :o", o=owner)


# --- isolation (T111, ADR 0016 section 8) --------------------------------------------------------


async def test_two_users_verify_the_same_mix_id_each_in_their_own_account(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    alice = await rider_of(browsers, henv, TOKEN_A)
    bob = await rider_of(browsers, henv, TOKEN_B)
    a_class = plant(henv, alice.user_id, [150])
    b_class = plant(henv, bob.user_id, [150])
    mowl_api.add_mix("Old A", token=TOKEN_A, mix_id="old-a", listed_seconds=150)
    mowl_api.add_mix("Old B", token=TOKEN_B, mix_id="old-b", listed_seconds=150)
    assert (await _start(alice, a_class)).status_code == 200
    assert (await _start(bob, b_class)).status_code == 200
    # One id, two accounts, two lengths: Alice's mix fits her selection, Bob's is short.
    mowl_api.add_mix(
        "Alice mix", token=TOKEN_A, mix_id=SAME, listed_seconds=150, audio_seconds=150, link="beats"
    )
    mowl_api.add_mix(
        "Bob mix", token=TOKEN_B, mix_id=SAME, listed_seconds=60, audio_seconds=60, link="beats"
    )
    assert (await _check(alice, a_class)).status_code == 201
    assert (await _check(bob, b_class)).status_code == 201
    await henv.run_verify()
    await henv.run_verify()
    a_view = (await alice.get(f"/api/classes/{a_class}/swedebeat-handoff")).json()
    b_view = (await bob.get(f"/api/classes/{b_class}/swedebeat-handoff")).json()
    assert (a_view["verdict"], a_view["mix_id"]) == ("ok", SAME)
    assert (b_view["verdict"], b_view["mix_id"]) == ("too_short", SAME)
    # Each attach went through the owner's own token and nobody else's.
    assert mowl_api.attaches == [SAME, SAME]
    a_mix = next(m for m in mowl_api.mixes[TOKEN_A] if m.id == SAME)
    b_mix = next(m for m in mowl_api.mixes[TOKEN_B] if m.id == SAME)
    assert set(a_mix.playlist_id) == {TOKEN_A}
    assert set(b_mix.playlist_id) == {TOKEN_B}
    maps = {
        row.owner_id: row.name
        for row in henv.sql("SELECT owner_id, name FROM swedebeat_mix_maps").all()
    }
    assert maps == {alice.user_id: "Alice mix", bob.user_id: "Bob mix"}

    # `ok` verifies Alice's row by itself; Bob's short mix waits for his word, and his override
    # changes his row only.
    def states() -> dict[UUID, str]:
        rows = henv.sql("SELECT owner_id, state FROM swedebeat_handoffs").all()
        return {row.owner_id: row.state for row in rows}

    assert states() == {alice.user_id: "verified", bob.user_id: "checked"}
    overridden = await bob.post(
        f"/api/classes/{b_class}/swedebeat-handoff/override", {"mix_id": SAME}
    )
    assert overridden.status_code == 200, overridden.text
    assert states() == {alice.user_id: "verified", bob.user_id: "verified"}
    assert mowl_api.attaches == [SAME, SAME]  # the override attached nothing


async def test_another_users_class_is_a_404_on_every_hand_off_and_match_route(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    alice = await rider_of(browsers, henv, TOKEN_A)
    bob = await rider_of(browsers, henv, TOKEN_B)
    mowl_api.add_mix("Alice mix", token=TOKEN_A, mix_id="alice-mix", listed_seconds=30)
    a_class = plant(henv, alice.user_id, [30])
    b_class = plant(henv, bob.user_id, [30])
    assert (await _start(alice, a_class)).status_code == 200
    a_match = match_of(henv, a_class)
    b_approved = one(
        henv, "SELECT approved_swedebeat_match_id FROM classes WHERE id = :c", c=b_class
    )
    henv.sql("UPDATE classes SET approved_swedebeat_match_id = NULL WHERE id = :c", c=a_class)
    before = henv.sql(
        "SELECT state, checked_at, mix_id, expires_at, mixes_before FROM swedebeat_handoffs "
        "WHERE owner_id = :o",
        o=alice.user_id,
    ).one()
    lists, jobs = mowl_api.list_calls, one(henv, "SELECT count(*) FROM jobs")
    key = {"Idempotency-Key": new_key()}
    base = f"/api/classes/{a_class}"
    answers = [
        await bob.get(f"{base}/swedebeat-handoff"),
        await bob.post(f"{base}/swedebeat-handoff", {}),
        await bob.post(f"{base}/swedebeat-handoff/check", {}, key),
        await bob.post(f"{base}/swedebeat-handoff/override", {"mix_id": "alice-mix"}),
        await bob.post(f"{base}/swedebeat-handoff/design", {}, {"Idempotency-Key": new_key()}),
        await bob.post(f"{base}/swedebeat-handoff/not-now", {}),
        await bob.post(f"{base}/swedebeat-match", {"revision_id": str(a_match)}),
        await bob.get(f"{base}/revisions/{a_match}"),
    ]
    for answer in answers:
        assert answer.status_code == 404, answer.request.url
        assert answer.json()["detail"]["reason"] != "feature_off"
        assert "Song 1" not in answer.text
    # Bob's own class and Alice's match: the pair is not Bob's.
    own = await bob.post(f"/api/classes/{b_class}/swedebeat-match", {"revision_id": str(a_match)})
    assert own.status_code == 404
    # Nothing was written for either, and MOWL was not asked anything for Bob.
    assert (
        one(henv, "SELECT approved_swedebeat_match_id FROM classes WHERE id = :c", c=a_class)
        is None
    )
    assert (
        one(henv, "SELECT approved_swedebeat_match_id FROM classes WHERE id = :c", c=b_class)
        == b_approved
    )
    after = henv.sql(
        "SELECT state, checked_at, mix_id, expires_at, mixes_before FROM swedebeat_handoffs "
        "WHERE owner_id = :o",
        o=alice.user_id,
    ).one()
    assert tuple(after) == tuple(before)
    assert handoffs(henv, bob.user_id) == 0
    assert mowl_api.list_calls == lists
    assert one(henv, "SELECT count(*) FROM jobs") == jobs
    assert mowl_api.attaches == []


async def test_a_mix_id_of_another_users_account_is_refused_before_any_attach(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    alice = await rider_of(browsers, henv, TOKEN_A)
    bob = await rider_of(browsers, henv, TOKEN_B)
    mowl_api.add_mix(
        "Alice only", token=TOKEN_A, mix_id="alice-only-mix", listed_seconds=30, audio_seconds=30,
        link="beats",
    )  # fmt: skip
    mowl_api.add_mix("Bob mix", token=TOKEN_B, mix_id="bob-mix", listed_seconds=30)
    b_class = plant(henv, bob.user_id, [30])
    assert alice.user_id != bob.user_id
    assert (await _start(bob, b_class)).status_code == 200
    attaches, media = list(mowl_api.attaches), list(mowl_api.media.requests)
    refused = await bob.post(
        f"/api/classes/{b_class}/swedebeat-handoff/override", {"mix_id": "alice-only-mix"}
    )
    assert refused.status_code in (409, 422), refused.text
    checked = await _check(bob, b_class, "alice-only-mix")
    assert checked.status_code == 201, checked.text
    job_id = await henv.run_verify()
    row = henv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "invalid_input")
    assert mowl_api.attaches == attaches
    assert mowl_api.media.requests == media
    left = henv.sql(
        "SELECT state, mix_id FROM swedebeat_handoffs WHERE owner_id = :o", o=bob.user_id
    ).one()
    assert (left.state, left.mix_id) == ("open", None)
    assert one(henv, "SELECT count(*) FROM swedebeat_mix_maps") == 0


async def test_an_analysed_mix_that_left_the_users_listing_cannot_be_confirmed(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    bob = await rider_of(browsers, henv, TOKEN_B)
    mowl_api.add_mix("Old B", token=TOKEN_B, mix_id="old-b", listed_seconds=150)
    b_class = plant(henv, bob.user_id, [150])
    assert (await _start(bob, b_class)).status_code == 200
    mowl_api.add_mix(
        "Bob mix", token=TOKEN_B, mix_id=SAME, listed_seconds=60, audio_seconds=60, link="beats"
    )
    assert (await _check(bob, b_class)).status_code == 201
    await henv.run_verify()
    view = (await bob.get(f"/api/classes/{b_class}/swedebeat-handoff")).json()
    assert (view["verdict"], view["mix_id"]) == ("too_short", SAME)
    # The id is now only in somebody else's account: Bob's word does not make it his.
    mowl_api.mixes[TOKEN_B] = [m for m in mowl_api.mixes[TOKEN_B] if m.id != SAME]
    mowl_api.add_mix("Alice mix", token=TOKEN_A, mix_id=SAME, listed_seconds=150)
    refused = await bob.post(f"/api/classes/{b_class}/swedebeat-handoff/override", {"mix_id": SAME})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["reason"] == "invalid_input"
    state = one(henv, "SELECT state FROM swedebeat_handoffs WHERE owner_id = :o", o=bob.user_id)
    assert state == "checked"


# --- retention (ADR 0016 sections 5 and 7, guard 10) ----------------------------------------------


def test_the_registry_puts_the_handoff_and_the_match_with_their_class_and_account() -> None:
    handoff = RETENTION["swedebeat_handoffs"]
    assert {"class", "account", "mowl_connection"} == set(handoff.deleted_with)
    assert "mix_id" in handoff.columns
    # The match is a revision of its class: no deletion path of its own, and never a cache.
    assert {"class", "account"} <= set(RETENTION["class_revisions"].deleted_with)
    assert "class" not in RETENTION["swedebeat_mix_maps"].deleted_with
    assert "mowl_connection" in RETENTION["swedebeat_mix_maps"].deleted_with


async def test_deleting_a_class_takes_its_match_and_hand_off_and_nobody_elses(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    alice = await rider_of(browsers, henv, TOKEN_A)
    bob = await rider_of(browsers, henv, TOKEN_B)
    mowl_api.add_mix("A", token=TOKEN_A, mix_id="a-mix", listed_seconds=30)
    mowl_api.add_mix("B", token=TOKEN_B, mix_id="b-mix", listed_seconds=30)
    a_class = plant(henv, alice.user_id, [30])
    b_class = plant(henv, bob.user_id, [30])
    assert (await _start(alice, a_class)).status_code == 200
    assert (await _start(bob, b_class)).status_code == 200
    deleted = await alice.client.delete(f"/api/classes/{a_class}", headers=await alice.headers())
    assert deleted.status_code == 200, deleted.text
    assert handoffs(henv, alice.user_id) == 0
    assert one(henv, "SELECT count(*) FROM class_revisions WHERE class_id = :c", c=a_class) == 0
    assert handoffs(henv, bob.user_id) == 1
    assert match_of(henv, b_class)


async def test_disconnecting_mowl_drops_hand_offs_maps_and_links_but_not_the_match(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    alice = await rider_of(browsers, henv, TOKEN_A)
    bob = await rider_of(browsers, henv, TOKEN_B)
    a_class = plant(henv, alice.user_id, [150])
    b_class = plant(henv, bob.user_id, [150])
    for who, token, klass in ((alice, TOKEN_A, a_class), (bob, TOKEN_B, b_class)):
        mowl_api.add_mix("Old", token=token, mix_id=f"old-{token[:4]}", listed_seconds=150)
        assert (await _start(who, klass)).status_code == 200
        mowl_api.add_mix(
            "New", token=token, mix_id=f"new-{token[:4]}", listed_seconds=150, audio_seconds=150,
            link="beats",
        )  # fmt: skip
        assert (await _check(who, klass)).status_code == 201
        await henv.run_verify()
    assert one(henv, "SELECT count(*) FROM swedebeat_mix_maps") == 2
    assert one(henv, "SELECT count(*) FROM mowl_playlist_links WHERE source = 'swedebeat'") == 2
    assert await disconnect(henv.ops.factory, make_context(alice.user_id), "mowl")
    counts = {
        "swedebeat_handoffs": "SELECT count(*) FROM swedebeat_handoffs WHERE owner_id = :o",
        "swedebeat_mix_maps": "SELECT count(*) FROM swedebeat_mix_maps WHERE owner_id = :o",
        "mowl_playlist_links": "SELECT count(*) FROM mowl_playlist_links WHERE owner_id = :o",
    }
    for statement in counts.values():
        assert one(henv, statement, o=alice.user_id) == 0
        assert one(henv, statement, o=bob.user_id) == 1
    # The match belongs to the class (Spotify-derived, ADR 0016 section 5), not to the connection.
    assert match_of(henv, a_class)


async def test_reconnecting_as_another_mowl_account_drops_hand_offs_maps_and_links(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    """The account-change path (a reconnect in place) shares ``_FORGET_MOWL`` with Disconnect."""
    alice = await rider_of(browsers, henv, TOKEN_A)
    bob = await rider_of(browsers, henv, TOKEN_B)
    a_class = plant(henv, alice.user_id, [150])
    b_class = plant(henv, bob.user_id, [150])
    for who, token, klass in ((alice, TOKEN_A, a_class), (bob, TOKEN_B, b_class)):
        mowl_api.add_mix("Old", token=token, mix_id=f"old-{token[:4]}", listed_seconds=150)
        assert (await _start(who, klass)).status_code == 200
        mowl_api.add_mix(
            "New", token=token, mix_id=f"new-{token[:4]}", listed_seconds=150, audio_seconds=150,
            link="beats",
        )  # fmt: skip
        assert (await _check(who, klass)).status_code == 201
        await henv.run_verify()
    statements = (
        "SELECT count(*) FROM swedebeat_handoffs WHERE owner_id = :o",
        "SELECT count(*) FROM swedebeat_mix_maps WHERE owner_id = :o",
        "SELECT count(*) FROM mowl_playlist_links WHERE owner_id = :o",
    )
    for statement in statements:
        assert one(henv, statement, o=alice.user_id) == 1  # self-check
    # The same connection row, another MOWL account's token.
    await henv.ops.connect(alice.user_id, "cccccccc-0000-1111-2222-333333333333")
    for statement in statements:
        assert one(henv, statement, o=alice.user_id) == 0
        assert one(henv, statement, o=bob.user_id) == 1
    assert match_of(henv, a_class)


async def test_the_hourly_purge_drops_hand_offs_expired_a_week_and_keeps_the_rest(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    alice = await rider_of(browsers, henv, TOKEN_A)
    bob = await rider_of(browsers, henv, TOKEN_B)
    classes = {
        name: plant(henv, alice.user_id, [30]) for name in ("live", "recent", "old", "older")
    }
    bobs = plant(henv, bob.user_id, [30])
    mowl_api.add_mix("Any", token=TOKEN_A, mix_id="any-mix", listed_seconds=30)
    mowl_api.add_mix("Any", token=TOKEN_B, mix_id="any-mix", listed_seconds=30)
    for who, klass in [(alice, c) for c in classes.values()] + [(bob, bobs)]:
        assert (await _start(who, klass)).status_code == 200
    ages = {"recent": 6, "old": 8, "older": 30}
    for name, days in ages.items():
        henv.sql(
            "UPDATE swedebeat_handoffs SET created_at = now() - make_interval(days => :d + 2), "
            "expires_at = now() - make_interval(days => :d) WHERE class_id = :c",
            d=days,
            c=classes[name],
        )
    # Bob's row is as old as Alice's oldest: it goes too, in Bob's own session.
    henv.sql(
        "UPDATE swedebeat_handoffs SET created_at = now() - interval '12 days', "
        "expires_at = now() - interval '10 days' WHERE class_id = :c",
        c=bobs,
    )
    counts = await auth.purge_expired(henv.ops.factory, henv.ops.app.settings)
    assert counts["swedebeat_handoffs"] == 3
    kept = {row.class_id for row in henv.sql("SELECT class_id FROM swedebeat_handoffs").all()}
    assert kept == {classes["live"], classes["recent"]}
    # The classes and their matches stay: only the finished manual step is dropped.
    assert one(henv, "SELECT count(*) FROM classes") == 5


async def test_deleting_the_account_takes_every_match_and_hand_off_of_that_user_only(
    browsers: Any, henv: HandEnv, mowl_api: FakeMowl
) -> None:
    alice = await rider_of(browsers, henv, TOKEN_A)
    bob = await rider_of(browsers, henv, TOKEN_B)
    mowl_api.add_mix("A", token=TOKEN_A, mix_id="a-mix", listed_seconds=30)
    mowl_api.add_mix("B", token=TOKEN_B, mix_id="b-mix", listed_seconds=30)
    a_class = plant(henv, alice.user_id, [30])
    b_class = plant(henv, bob.user_id, [30])
    assert (await _start(alice, a_class)).status_code == 200
    assert (await _start(bob, b_class)).status_code == 200
    email = one(henv, "SELECT email FROM users WHERE id = :u", u=alice.user_id)
    erased = await alice.post("/api/account/delete", {"confirm_email": email})
    assert erased.status_code == 204, (
        erased.text
    )  # the real route: typed confirmation, then cascade
    assert (
        one(henv, "SELECT count(*) FROM class_revisions WHERE owner_id = :u", u=alice.user_id) == 0
    )
    assert handoffs(henv, alice.user_id) == 0
    assert handoffs(henv, bob.user_id) == 1
    assert match_of(henv, b_class)
