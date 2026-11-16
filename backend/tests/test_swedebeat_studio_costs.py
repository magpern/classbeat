"""What a studio song stage costs and how its approval stays bound (ADR 0036 section 3; SR C3):
the class's one credit (as ``design_with_songs``), passed on by undo, another and Swap anyway;
those actions under the per-class limit of Change songs (``revision_limit``); the
``Idempotency-Key`` of the studio routes; the approved phase's SwedeBeat generation check; and
the cap of 3 swap rounds. The fake Spotify, the fake gateway and ``FakeProvider``; no network."""

from collections.abc import Iterator
from uuid import UUID

import pytest

from classbeat.design import limits
from classbeat.design.limits import SettingRevisionPolicy
from ctx import make_context
from gatewayfake import store_bearer
from songtools import new_key, song_id
from spotifyfake import USER_A
from studiotools import StudioEnv, StudioRider, choose_turns, pool_turns, script, title
from test_swedebeat_studio_songs import FOUND_4, _swap_script  # pyright: ignore[reportPrivateUsage]

SHORT_BRIEF = {"length_min": 10, "intensity": "moderate"}  # three songs


def _credit(studio: StudioEnv, job_id: UUID) -> str:
    return studio.songs.sql("SELECT credit_state FROM jobs WHERE id = :id", id=job_id).scalar_one()


def _held(studio: StudioEnv, owner: UUID) -> int:
    """The user's credits that count against the month (``quota.ledger``'s held)."""
    return studio.songs.sql(
        "SELECT count(*) FROM credit_ledger WHERE owner_id = :o "
        "AND (state = 'committed' OR (state = 'reserved' AND job_id IS NOT NULL))",
        o=owner,
    ).scalar_one()


def _ledger(studio: StudioEnv, job_id: UUID) -> str | None:
    return studio.songs.sql(
        "SELECT state FROM credit_ledger WHERE job_id = :id", id=job_id
    ).scalar_one_or_none()


def _class(studio: StudioEnv, job_id: UUID) -> UUID:
    return studio.songs.row(job_id).class_id


async def _swapped(studio: StudioEnv) -> tuple[StudioRider, UUID, UUID, UUID]:
    """A waiting list with swaps (its first song a swap): rider, job, list, class."""
    studio.found(*FOUND_4, *range(20, 40))
    studio.songs.script = _swap_script()
    rider = await studio.rider()
    job_id, revision_id, _ = await studio.waiting(rider)
    return rider, job_id, revision_id, _class(studio, job_id)


@pytest.fixture
def song_change_limit() -> Iterator[None]:
    """One song change per class (``CLASSBEAT_REVISIONS_PER_CLASS = 1``), then the default."""
    before = limits.current()
    limits.configure(policy=SettingRevisionPolicy(1))
    try:
        yield
    finally:
        limits.configure(policy=before.policy)


async def test_a_class_takes_one_credit_that_its_actions_pass_on(studio: StudioEnv) -> None:
    rider, job_id, revision_id, class_id = await _swapped(studio)
    # The pick reserves the class's credit, as design_with_songs does.
    assert (_credit(studio, job_id), _ledger(studio, job_id)) == ("reserved", "reserved")
    assert _held(studio, rider.user_id) == 1
    answer = await rider.act(class_id, revision_id, "undo", 0)
    assert answer.status_code == 201, answer.text
    undo_id = UUID(answer.json()["job_id"])
    # The waiting job's credit is released in the same transaction as the new one's reservation.
    assert (_credit(studio, job_id), _ledger(studio, job_id)) == ("released", "released")
    assert (_credit(studio, undo_id), _ledger(studio, undo_id)) == ("reserved", "reserved")
    assert _held(studio, rider.user_id) == 1
    undone = await studio.run(undo_id)
    studio.songs.script = script(
        pool_turns(("g3",), [(ref, "warmup") for ref in range(6)]),
        choose_turns(
            [("pool", 0, "warmup")]
            + [
                ("kept", i, r)
                for i, r in enumerate(
                    ["build", "climb", "build", "sprint", "sprint", "climb", "cooldown"]
                )
            ]
        ),
    )
    answer = await rider.act(class_id, undone.approval_revision_id, "another", 0)
    another_id = UUID(answer.json()["job_id"])
    row = await studio.run(another_id)
    assert _held(studio, rider.user_id) == 1
    approved = await rider.browser.approve(
        another_id, row.approval_revision_id, bytes(row.approval_hash).hex()
    )
    assert approved.status_code == 200, approved.text
    done = await studio.run(another_id)
    assert done.state == "completed"
    # Committed once, by the approved list; every earlier job of the class released its credit.
    states = studio.songs.sql(
        "SELECT l.state FROM credit_ledger l JOIN jobs j ON j.id = l.job_id "
        "WHERE j.class_id = :c ORDER BY l.created_at",
        c=class_id,
    ).scalars()
    assert list(states)[:2] == ["released", "released"]
    assert _held(studio, rider.user_id) == 1


async def test_studio_actions_count_against_the_song_change_limit(
    studio: StudioEnv, song_change_limit: None
) -> None:
    rider, _, revision_id, class_id = await _swapped(studio)
    view = await rider.browser.get(f"/api/classes/{class_id}/studio-songs/{revision_id}")
    assert view.json()["changes"] == {"count": 0, "limit": 1}  # SR C6a: shown before a refusal
    undo = await rider.act(class_id, revision_id, "undo", 0)
    assert undo.status_code == 201, undo.text  # the first change is within the limit
    undone = await studio.run(UUID(undo.json()["job_id"]))
    path = f"/api/classes/{class_id}/studio-songs/{undone.approval_revision_id}"
    assert (await rider.browser.get(path)).json()["changes"] == {"count": 1, "limit": 1}
    refused = await rider.act(class_id, undone.approval_revision_id, "another", 0)
    assert refused.status_code == 409
    assert refused.json()["detail"] == {"reason": "revision_limit", "limit": 1, "count": 1}
    # Refused as Change songs is, and the waiting list is left as it was.
    assert studio.songs.row(UUID(undo.json()["job_id"])).state == "waiting_for_approval"


async def test_a_repeated_key_answers_the_same_job_and_a_changed_body_is_refused(
    studio: StudioEnv,
) -> None:
    rider, job_id, revision_id, class_id = await _swapped(studio)
    key = new_key()
    first = await rider.act(class_id, revision_id, "undo", 0, key=key)
    assert first.status_code == 201, first.text
    again = await rider.act(class_id, revision_id, "undo", 0, key=key)
    assert (again.status_code, again.json()["job_id"]) == (200, first.json()["job_id"])
    undo_id = UUID(first.json()["job_id"])
    undone = await studio.run(undo_id)
    jobs = studio.songs.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=rider.user_id)
    before = jobs.scalar_one()
    # The same key with another body: refused, and the cancel of the waiting job rolled back.
    changed = await rider.act(class_id, undone.approval_revision_id, "another", 0, key=key)
    assert changed.status_code == 409
    assert changed.json()["detail"]["reason"] == "idempotency_mismatch"
    assert studio.songs.row(undo_id).state == "waiting_for_approval"
    assert _credit(studio, undo_id) == "reserved"
    assert (
        studio.songs.sql(
            "SELECT count(*) FROM jobs WHERE owner_id = :o", o=rider.user_id
        ).scalar_one()
        == before
    )
    assert studio.songs.row(job_id).state == "cancelled"


async def test_a_swedebeat_reconnect_after_the_approval_fails_the_approved_phase(
    studio: StudioEnv,
) -> None:
    studio.found(*range(14))
    rider = await studio.rider()
    job_id, revision_id, digest = await studio.waiting(rider)
    approved = await rider.browser.approve(job_id, revision_id, digest)
    assert approved.status_code == 200, approved.text
    generation = studio.songs.sql(
        "SELECT generation FROM connections WHERE id = :id", id=rider.swedebeat_connection_id
    ).scalar_one()
    # The user reconnects SwedeBeat: the same row, a new generation.
    ctx = make_context(rider.user_id)
    await store_bearer(studio.songs.factory, studio.songs.ring, ctx, studio.gateway)
    assert studio.songs.sql(
        "SELECT id, generation FROM connections WHERE owner_id = :o AND provider = 'swedebeat'",
        o=rider.user_id,
    ).one() == (rider.swedebeat_connection_id, generation + 1)
    row = await studio.run(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")


async def test_swaps_stop_after_three_rounds(studio: StudioEnv) -> None:
    """Of the playlist's six songs only the first is found. Round 1 finds one replacement of six;
    rounds 2 and 3 each send one new candidate, still missing (fewer than 3 sent: never
    suspected). Still short after the third round, the pick stops: no fourth round."""
    studio.found(0, 20)
    own = studio.songs.fake.add_playlist(
        USER_A, "Old ride", [f"spotify:track:{song_id(i)}" for i in range(6)]
    )
    builds = [(ref, "build") for ref in range(6)]
    studio.songs.script = script(
        pool_turns(("g2",), builds),  # 20 to 25: 20 found
        pool_turns(("g2",), [(ref, "build") for ref in (0, 1, 2, 3, 4, 6)]),  # 26 new
        pool_turns(("g2",), [(ref, "build") for ref in (0, 1, 2, 3, 4, 7)]),  # 27 new
        pool_turns(("g2",), [(ref, "build") for ref in (0, 1, 2, 3, 4, 8)]),  # never reached
    )
    rider = await studio.rider()
    job_id = await studio.created(rider, brief=SHORT_BRIEF, spotify_playlist_id=own.id)
    row = await studio.run(job_id)
    assert (row.state, row.error_category) == ("failed", "pool_too_small")
    # The first match and its retry, then each round's match and its retry: three rounds.
    sent = [len(m.body["tracks"]) for m in studio.matches()]
    assert sent == [6, 5, 6, 5, 1, 1, 1, 1]
    assert title(28) not in str([m.body for m in studio.matches()])
