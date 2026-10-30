"""The reconcilers of the two Spotify writes (ADR 0008 section 2; issue OP1, #178), called directly
with an operation row as the protocol hands it, against the fake Spotify. Read-only: no reconciler
ever writes to Spotify.

- ``spotify_create_playlist``: exactly one owned, non-public playlist naming the row's ``ref``
  -> ``succeeded`` with its id; none with every page read and the listing lag off -> inconclusive,
  on and passed since the call returned -> ``failed``; two, a failed listing or 20 full pages ->
  inconclusive; a same-named playlist without the ``ref`` is never adopted.
- ``spotify_replace_items``: equal -> ``succeeded``; readable and different -> ``failed`` only once
  the call is over (it returned, or ``actionable_at`` passed); unreadable -> inconclusive.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from classbeat.operations import Operation, ReconcileInput, Reconciliation
from classbeat.operations.kinds import ref_of
from classbeat.playlists import POOL_NAME, common
from classbeat.playlists import create as create_kind
from classbeat.playlists import replace as replace_kind
from ctx import make_context
from playlisttools import TRACKS, PlayEnv
from spotifyfake import USER_A, USER_B

MINE = "/v1/me/playlists"
ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"


@pytest.fixture
def lag() -> Any:
    """Set the listing lag for one test (the runtime is restored by ``penv``)."""

    def set_lag(seconds: float | None) -> None:
        current = common.runtime()
        common.configure(
            common.Runtime(current.http, current.margin_seconds, listing_lag_seconds=seconds)
        )

    return set_lag


def create_row(
    *, returned_ago: float | None = 1800.0, ref: str | None = None, final: bool = False
) -> Operation:
    operation_id = uuid.uuid4()
    job = uuid.uuid4()
    now = datetime.now(UTC)  # at build time: a long run must not age the row
    pending_at = now - timedelta(hours=1)
    return Operation(
        id=operation_id,
        kind=create_kind.KIND,
        idempotency_key=f"{job}:{'final' if final else 'pool'}-create",
        state="uncertain",
        external_id=None,
        job_id=job,
        class_id=uuid.uuid4() if final else None,
        context={
            "purpose": "final" if final else "pool",
            "pool_id": str(uuid.uuid4()),
            "ref": ref if ref is not None else ref_of(operation_id),
        },
        pending_at=pending_at,
        call_ended_at=None if returned_ago is None else now - timedelta(seconds=returned_ago),
    )


async def reconcile_create(penv: PlayEnv, owner: Any, operation: Operation) -> Reconciliation:
    check = ReconcileInput(penv.factory, penv.keyring, make_context(owner), operation)
    return await create_kind._reconcile(check)  # pyright: ignore[reportPrivateUsage]


def ours(penv: PlayEnv, operation: Operation, **kw: Any) -> str:
    """A playlist as the create would have made it for ``operation``."""
    about = common.description(ref_of(operation.id), "pool")
    return penv.fake.add_playlist(kw.pop("owner", USER_A), POOL_NAME, description=about, **kw).id


INCONCLUSIVE = Reconciliation("inconclusive")
FAILED = Reconciliation("failed")


async def test_create_exactly_one_match_is_succeeded_with_its_id(penv: PlayEnv) -> None:
    owner = await penv.user()
    operation = create_row()
    penv.fake.add_playlist(USER_A, POOL_NAME)  # same name, no ref: not ours
    penv.fake.add_playlist(USER_A, "Other", description="classbeat ref 0123456789ab")
    found = ours(penv, operation)
    assert await reconcile_create(penv, owner, operation) == Reconciliation("succeeded", found)
    assert not penv.writes()  # read-only


async def test_create_none_found_is_inconclusive_unless_the_lag_is_on_and_passed(
    penv: PlayEnv, lag: Any
) -> None:
    owner = await penv.user()
    for i in range(60):  # more than one page, every page read
        penv.fake.add_playlist(USER_A, f"Mine {i}")
    operation = create_row(returned_ago=1800)
    assert await reconcile_create(penv, owner, operation) == INCONCLUSIVE  # lag unset (default)
    lag(60)
    assert await reconcile_create(penv, owner, operation) == FAILED
    lag(3600)  # not yet passed since the call returned
    assert await reconcile_create(penv, owner, operation) == INCONCLUSIVE
    lag(60)
    not_returned = create_row(returned_ago=None)  # the call never returned: no proof
    assert await reconcile_create(penv, owner, not_returned) == INCONCLUSIVE
    assert len(penv.api(MINE, "GET")) == 2 * 4  # two pages per reconciliation


async def test_a_same_named_playlist_without_the_ref_is_never_adopted(
    penv: PlayEnv, lag: Any
) -> None:
    owner = await penv.user()
    operation = create_row()
    penv.fake.add_playlist(USER_A, POOL_NAME)
    penv.fake.add_playlist(USER_A, POOL_NAME, description="classbeat ref")
    other = create_row()  # another row's ref
    ours(penv, other)
    assert await reconcile_create(penv, owner, operation) == INCONCLUSIVE
    lag(60)
    assert await reconcile_create(penv, owner, operation) == FAILED  # absent, never adopted


async def test_create_two_matches_are_inconclusive(penv: PlayEnv, lag: Any) -> None:
    owner = await penv.user()
    lag(60)
    operation = create_row()
    ours(penv, operation)
    ours(penv, operation)
    assert await reconcile_create(penv, owner, operation) == INCONCLUSIVE


async def test_create_a_public_or_another_owners_playlist_with_the_ref_is_not_ours(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    operation = create_row()
    ours(penv, operation, public=True)
    theirs = ours(penv, operation, owner=USER_B)
    penv.fake.playlists[theirs].collaborators.add(USER_A)  # listed for A, owned by B
    assert await reconcile_create(penv, owner, operation) == INCONCLUSIVE
    found = ours(penv, operation)
    assert await reconcile_create(penv, owner, operation) == Reconciliation("succeeded", found)


async def test_create_a_failed_listing_is_inconclusive(penv: PlayEnv, lag: Any) -> None:
    owner = await penv.user()
    lag(60)
    operation = create_row()
    ours(penv, operation)
    penv.fake.inject("status", method="GET", path=MINE, status=500)
    assert await reconcile_create(penv, owner, operation) == INCONCLUSIVE
    penv.fake.inject("next_unlisted", method="GET", path=MINE)  # a next URL is never followed
    assert await reconcile_create(penv, owner, operation) == Reconciliation(
        "succeeded", next(iter(penv.fake.playlists))
    )
    assert all(s.host == "api.spotify.com" for s in penv.fake.seen)


@pytest.mark.parametrize("count", [1000, 1001])
async def test_create_twenty_full_pages_are_inconclusive(
    penv: PlayEnv, lag: Any, count: int
) -> None:
    owner = await penv.user()
    lag(60)
    operation = create_row()
    for i in range(count):
        penv.fake.add_playlist(USER_A, f"Mine {i}")
    assert await reconcile_create(penv, owner, operation) == INCONCLUSIVE


async def test_create_a_row_whose_ref_is_not_its_own_is_inconclusive(penv: PlayEnv) -> None:
    owner = await penv.user()
    operation = create_row(ref="0123456789ab")
    penv.fake.add_playlist(USER_A, POOL_NAME, description="classbeat ref 0123456789ab")
    assert await reconcile_create(penv, owner, operation) == INCONCLUSIVE
    assert not penv.api()  # nothing read for a row that is not consistent


async def test_create_a_final_row_is_reconciled_the_same_way(penv: PlayEnv) -> None:
    owner = await penv.user()
    operation = create_row(final=True)
    about = common.description(ref_of(operation.id), "final")
    found = penv.fake.add_playlist(USER_A, "Tuesday climb", description=about).id
    assert await reconcile_create(penv, owner, operation) == Reconciliation("succeeded", found)


# --- the replace reconciler --------------------------------------------------------------------


def replace_row(
    playlist_id: str,
    tracks: list[str],
    *,
    returned: bool,
    pending_ago: float = 60.0,
    bound: float = 120.0,
) -> Operation:
    pending_at = datetime.now(UTC) - timedelta(seconds=pending_ago)  # at build time
    return Operation(
        id=uuid.uuid4(),
        kind=replace_kind.KIND,
        idempotency_key=f"{uuid.uuid4()}:pool:{common.ids_hash(tracks).hex()[:16]}",
        state="uncertain",
        external_id=None,
        job_id=uuid.uuid4(),
        class_id=None,
        context={
            "purpose": "pool",
            "pool_id": str(uuid.uuid4()),
            "playlist_id": playlist_id,
            "uris_hash": common.ids_hash(tracks).hex(),
            "count": len(tracks),
            "bound_seconds": bound,
        },
        pending_at=pending_at,
        call_ended_at=pending_at + timedelta(seconds=1) if returned else None,
    )


async def reconcile_replace(penv: PlayEnv, owner: Any, operation: Operation) -> Reconciliation:
    check = ReconcileInput(penv.factory, penv.keyring, make_context(owner), operation)
    return await replace_kind._reconcile(check)  # pyright: ignore[reportPrivateUsage]


def uris(tracks: list[str]) -> list[str]:
    return [f"spotify:track:{t}" for t in tracks]


async def test_replace_equal_is_succeeded(penv: PlayEnv) -> None:
    owner = await penv.user()
    playlist_id = penv.fake.add_playlist(USER_A, POOL_NAME, uris(TRACKS)).id
    operation = replace_row(playlist_id, TRACKS, returned=False)  # even while it may run
    assert await reconcile_replace(penv, owner, operation) == Reconciliation(
        "succeeded", playlist_id
    )
    assert not penv.writes()


async def test_replace_readable_and_different_is_failed_only_once_the_call_is_over(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    playlist_id = penv.fake.add_playlist(USER_A, POOL_NAME, uris(TRACKS[:2])).id
    returned = replace_row(playlist_id, TRACKS, returned=True)
    assert await reconcile_replace(penv, owner, returned) == FAILED
    running = replace_row(playlist_id, TRACKS, returned=False, pending_ago=60, bound=120)
    assert await reconcile_replace(penv, owner, running) == INCONCLUSIVE  # before actionable_at
    stale = replace_row(playlist_id, TRACKS, returned=False, pending_ago=151, bound=120)
    assert await reconcile_replace(penv, owner, stale) == FAILED  # past bound + 30 s
    # A playlist holding a marker (a local file) is readable and different as well.
    penv.fake.playlists[playlist_id].entries = [*uris(TRACKS), "spotify:local:a:b:c:1"]
    assert await reconcile_replace(penv, owner, returned) == FAILED


@pytest.mark.parametrize(
    ("spoil", "options"),
    [
        ("status", {"method": "GET", "path": ITEMS, "status": 500}),
        ("refuse", {"method": "GET", "path": ITEMS}),
        ("status", {"method": "GET", "path": r"/v1/playlists/[0-9A-Za-z]{22}", "status": 404}),
    ],
)
async def test_replace_unreadable_is_inconclusive(
    penv: PlayEnv, spoil: str, options: dict[str, Any]
) -> None:
    owner = await penv.user()
    playlist_id = penv.fake.add_playlist(USER_A, POOL_NAME, uris(TRACKS[:2])).id
    penv.fake.inject(spoil, **options)
    operation = replace_row(playlist_id, TRACKS, returned=True)
    assert await reconcile_replace(penv, owner, operation) == INCONCLUSIVE


@pytest.mark.parametrize("change", ["public", "collaborative", "owner"])
async def test_replace_a_playlist_no_longer_owned_and_private_is_inconclusive(
    penv: PlayEnv, change: str
) -> None:
    owner = await penv.user()
    penv.fake.foreign = "visible"
    playlist = penv.fake.add_playlist(USER_A, POOL_NAME, uris(TRACKS[:2]))
    if change == "owner":
        playlist.owner = USER_B
    else:
        setattr(playlist, change, True)
    operation = replace_row(playlist.id, TRACKS, returned=True)
    assert await reconcile_replace(penv, owner, operation) == INCONCLUSIVE
    assert not penv.api(ITEMS)  # its items are not even read
