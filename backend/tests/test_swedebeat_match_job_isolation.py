# ruff: noqa: F811 - the fixtures are imported from test_swedebeat_match and named as parameters
"""ADR 0016 sections 5, 6 and 8, B5: the match job, the catalogue replace and the approval stop at
the owner, and nothing Spotify-derived is cached across classes or users. Fakes only.

The fixtures and helpers are ``test_swedebeat_match.py``'s (real song lists, real Swedebeat
connections, the fake catalogue over a fake transport).
"""

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from cataloguefake import CatalogueFake
from classbeat.classes import selection
from classbeat.db import user_session
from classbeat.jobs.kinds import Refused
from classbeat.swedebeat_match.replace import CatalogueReplace, decide
from classbeat.swedebeat_match.revision import Matched, MatchedSong, Wanted, build
from ctx import make_context
from swedebeatfake import EMAIL, EMAIL_B, PASSWORD, PASSWORD_B
from test_swedebeat_match import (  # noqa: F401 - the fixtures: env, admin, catalogue, configured
    Rider,
    _body,
    _plant,
    _post_job,
    _rider,
    _run,
    _songs,
    admin,
    catalogue,
    configured,
    env,
)

pytestmark = pytest.mark.usefixtures("swedebeat_api")


async def two(env: Any, make_actor: Any) -> tuple[Rider, Rider]:
    alice = await _rider(
        env, make_actor, account="alice@example.com", swedebeat_email=EMAIL, password=PASSWORD,
        start=1, bpm=120.0, class_title="Alice ride",
    )  # fmt: skip
    bob = await _rider(
        env, make_actor, account="bob@example.com", swedebeat_email=EMAIL_B, password=PASSWORD_B,
        start=11, bpm=100.0, class_title="Bob ride",
    )  # fmt: skip
    return alice, bob


def jobs_of(admin: Any, user: UUID) -> int:
    return int(
        admin.execute(
            text("SELECT count(*) FROM jobs WHERE owner_id = :o"), {"o": user}
        ).scalar_one()
    )


async def test_a_match_job_cannot_name_another_users_class_list_or_connection(
    env: Any, admin: Any, make_actor: Any, catalogue: CatalogueFake, configured: None
) -> None:
    alice, bob = await two(env, make_actor)
    own_body = _body(bob)
    swaps = {
        "alice's whole body": _body(alice),
        "alice's class and list, bob's connection": {
            **_body(alice),
            "input": {**_body(alice)["input"], "swedebeat_connection_id": str(bob.connection_id)},
        },
        "bob's class, alice's list": {
            **own_body,
            "input": {**own_body["input"], "song_list_revision_id": str(alice.revision_id)},
        },
        "bob's class and list, alice's connection": {
            **own_body,
            "input": {**own_body["input"], "swedebeat_connection_id": str(alice.connection_id)},
        },
    }
    for name, body in swaps.items():
        refused = await _post_job(bob.actor, body)
        assert 400 <= refused.status_code < 500, (name, refused.text)
        assert "Alice ride" not in refused.text
    assert jobs_of(admin, bob.user_id) == 0
    assert jobs_of(admin, alice.user_id) == 0
    assert catalogue.calls == []
    # The refusals did not lock Bob out: his own body is admitted and searched with his token.
    ok = await _post_job(bob.actor, own_body)
    assert ok.status_code == 201, ok.text
    await _run(env)
    assert catalogue.calls
    assert {call.token for call in catalogue.calls} == {bob.token}


def _insert_match(admin: Any, rider: Rider) -> UUID:
    songs = [
        MatchedSong(
            position=n,
            status="missing" if n == 3 else "found",
            wanted=Wanted(title=f"Wanted {n}", artist="Band"),
            swedebeat=None if n == 3 else Matched(title=f"Wanted {n}", artist="Band", duration=200),
        )
        for n in range(1, 5)
    ]
    made = build(songs, song_list_id=rider.revision_id, song_list_hash=rider.list_hash)
    match_id = uuid4()
    admin.execute(
        text(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance, parent_id) VALUES (:id, :o, :c, 2, "
            "'swedebeat_match', CAST(:content AS jsonb), :hash, CAST(:music AS jsonb), "
            "CAST(:prov AS jsonb), :parent)"
        ),
        {
            "id": match_id,
            "o": rider.user_id,
            "c": rider.class_id,
            "content": json.dumps(made.content.model_dump(mode="json")),
            "hash": made.content_hash,
            "music": json.dumps(made.music.model_dump(mode="json")),
            "prov": json.dumps(made.provenance),
            "parent": rider.revision_id,
        },
    )
    draft_id = uuid4()
    admin.execute(
        text(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) VALUES (:id, :o, :c, 3, 'draft', '{}', :hash, "
            "CAST(:music AS jsonb), '{}')"
        ),
        {
            "id": draft_id,
            "o": rider.user_id,
            "c": rider.class_id,
            "hash": b"\x11" * 32,
            "music": json.dumps(
                {"source": "mowl_spotify_playlist", "song_list_id": str(rider.revision_id)}
            ),
        },
    )
    admin.execute(
        text("UPDATE classes SET current_revision_id = :r WHERE id = :c"),
        {"r": draft_id, "c": rider.class_id},
    )
    return match_id


async def test_the_replace_and_the_approval_read_only_the_owners_match(
    env: Any, admin: Any, make_actor: Any, configured: None
) -> None:
    alice, bob = await two(env, make_actor)
    match_id = _insert_match(admin, alice)
    for who in (alice, bob):
        assert who.class_id
        assert who.revision_id
    # The replace: Alice may; Bob, with Alice's ids and his own connection, finds nothing.
    async with user_session(env.factory, make_context(alice.user_id)) as db:
        mine = await decide(
            db, make_context(alice.user_id), class_id=alice.class_id,
            list_id=alice.revision_id, match_id=match_id, connection_id=alice.connection_id,
        )  # fmt: skip
    assert isinstance(mine, CatalogueReplace)
    assert mine.slots == [2]
    async with user_session(env.factory, make_context(bob.user_id)) as db:
        theirs = await decide(
            db, make_context(bob.user_id), class_id=alice.class_id,
            list_id=alice.revision_id, match_id=match_id, connection_id=bob.connection_id,
        )  # fmt: skip
    assert theirs == Refused("invalid_input")
    # The approval: the same.
    stolen = await selection.record(
        env.factory, make_context(bob.user_id), alice.class_id, match_id
    )
    assert (stolen.approved, stolen.reason) == (None, "not_found")
    own_class = await selection.record(
        env.factory, make_context(bob.user_id), bob.class_id, match_id
    )
    assert own_class.approved is None  # not_found, or stale_match for a class with no draft yet
    approved = admin.execute(
        text("SELECT count(*) FROM classes WHERE approved_swedebeat_match_id IS NOT NULL")
    ).scalar_one()
    assert approved == 0
    granted = await selection.record(
        env.factory, make_context(alice.user_id), alice.class_id, match_id
    )
    assert granted.reason is None
    assert granted.approved is not None
    assert granted.approved.revision_id == match_id


async def test_nothing_is_cached_across_classes_or_users(
    env: Any, admin: Any, make_actor: Any, catalogue: CatalogueFake, configured: None
) -> None:
    alice = await _rider(
        env, make_actor, account="alice@example.com", swedebeat_email=EMAIL, password=PASSWORD,
        start=1, bpm=120.0, class_title="First ride",
    )  # fmt: skip
    songs = _songs(1, 120.0)
    second_class, second_list, second_hash = await _plant(env, alice.user_id, "Second ride", songs)
    bob = await _rider(
        env, make_actor, account="bob@example.com", swedebeat_email=EMAIL_B, password=PASSWORD_B,
        start=1, bpm=120.0, class_title="Third ride",
    )  # fmt: skip
    body_two = {
        "kind": "swedebeat_match",
        "input": {
            "class_id": str(second_class),
            "song_list_revision_id": str(second_list),
            "swedebeat_connection_id": str(alice.connection_id),
        },
    }
    assert second_hash
    seen: list[int] = []
    for actor, body in (
        (alice.actor, _body(alice)),
        (alice.actor, body_two),
        (bob.actor, _body(bob)),
    ):
        created = await _post_job(actor, body)
        assert created.status_code == 201, created.text
        before = len(catalogue.calls)
        await _run(env)
        seen.append(len(catalogue.calls) - before)
    # The same three distinct songs are searched again for the second class and for the second user.
    assert seen == [3, 3, 3]
    assert [call.token for call in catalogue.calls] == [alice.token] * 6 + [bob.token] * 3
