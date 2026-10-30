"""The pool's lifetime and the protocol seams of the Spotify writes (ADR 0008 section 2, ADR 0009
sections 5, 7 and 9; issue OP1, #178). Fakes only.

- **Pool lifetime**: after ``pool_missing`` the retry makes a new create row and playlist, never
  the deleted id; after a revoke and a new consent no request goes to the old pool id; an
  uncertain pool create is never offered to the user, and the next pick marks it ``failed``
  (``resolved_by = 'worker'``) and creates under its own key with a new ``ref``; the revoke and a
  Client ID change leave no pool operation row (final rows stay).
- **Crash mid-fill**: the next attempt never creates a second playlist; a stale call that may
  still be running blocks the next pool replace until ``actionable_at``; the supersede rule then
  marks the older uncertain pool replace ``failed``.
- **Between the job's start and the decide step**: a disconnect, reconnect, Client ID or account
  change or a revoked consent ends the job ``not_connected`` / ``consent_missing`` with no
  request to Spotify.
- **The class rule** for pool and final rows, and the row's ``ref`` handed to the call.
"""

import uuid
from typing import Any

import pytest
from mowl import spotify_web

from classbeat import connections
from classbeat.connections import ConnectionFailure, open_connection
from classbeat.operations import OperationRequest, request_hash
from classbeat.operations.kinds import ref_of
from classbeat.playlists import common, replace
from classbeat.playlists import pool as pool_module
from classbeat.spotify import spotify_call
from ctx import make_context
from jobtools import new_class
from playlisttools import MANY, TRACKS, PlayEnv
from spotifyfake import CLIENT_A, CLIENT_B, USER_A, USER_B

CREATE, REPLACE = "spotify_create_playlist", "spotify_replace_items"
ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"
MINE = "/v1/me/playlists"
OPS = "/api/external-operations"
# The reaped pick as it ends once its attempts are spent (``failed`` / ``lease_lost``).
FINISHED = (
    "UPDATE jobs SET state = 'failed', error_category = 'lease_lost', lease_expires_at = NULL, "
    "finished_at = now() WHERE id = :id"
)


async def first_pick(penv: PlayEnv, owner: Any, **given: Any) -> str:
    job_id = penv.job(owner, **given)
    assert (await penv.run(job_id)).state == "completed"
    return penv.pool(owner).playlist_id


def requests_to(penv: PlayEnv, playlist_id: str) -> list[Any]:
    return [s for s in penv.api() if playlist_id in s.path]


# --- pool lifetime -----------------------------------------------------------------------------


async def test_after_pool_missing_the_retry_creates_a_new_row_and_playlist(penv: PlayEnv) -> None:
    owner = await penv.user()
    old = await first_pick(penv, owner)
    [old_create, _] = penv.operations(owner)
    penv.fake.seen.clear()
    del penv.fake.playlists[old]  # the user deleted the working playlist in Spotify
    job_id = penv.job(owner, tracks=MANY)
    row = await penv.run(job_id)
    assert (row.state, row.attempt) == ("queued", 1)  # pool_missing is retried
    assert penv.pool(owner).playlist_id is None
    assert penv.operations(owner) == []  # every pool row forgotten with the id
    seen = len(penv.api())

    assert (await penv.run(job_id)).state == "completed"
    new = penv.pool(owner).playlist_id
    assert new not in (None, old)
    create, replace = penv.operations(owner)
    assert create.idempotency_key == f"{job_id}:pool-create"
    assert create.id != old_create.id
    assert create.request_context["ref"] == ref_of(create.id) != ref_of(old_create.id)
    assert replace.request_context["playlist_id"] == new
    [found_gone] = requests_to(penv, old)  # only the one owner check that found it gone
    assert (found_gone.method, found_gone.status) == ("GET", 404)
    assert not [s for s in penv.api()[seen:] if old in s.path]
    assert penv.entries(new) == [f"spotify:track:{t}" for t in MANY]


async def test_after_a_revoke_and_a_new_consent_no_request_goes_to_the_old_pool(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    old = await first_pick(penv, owner)
    ctx = make_context(owner)
    actor = await penv.spot.browser(owner)
    revoked = await actor.call("DELETE", "/api/connections/spotify/pool-consent")
    assert revoked.status_code in (200, 204), revoked.text
    assert penv.operations(owner) == []
    assert penv.pool(owner) is None
    assert await connections.give_pool_consent(penv.factory, ctx, 1)
    penv.fake.seen.clear()
    job_id = penv.job(owner)
    assert (await penv.run(job_id)).state == "completed"
    new = penv.pool(owner).playlist_id
    assert new != old
    assert not requests_to(penv, old)
    assert penv.entries(old) == [f"spotify:track:{t}" for t in TRACKS]  # left alone in Spotify


async def test_a_client_id_change_forgets_the_pool_rows_and_keeps_the_final_ones(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    old = await first_pick(penv, owner)
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    final = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    assert (await penv.run(final)).state == "completed"
    [final_row] = [o for o in penv.operations(owner) if o.class_id is not None]
    ctx = make_context(owner)
    await penv.spot.seed(ctx, CLIENT_B, USER_A)  # the same account under another Client ID
    assert [o.id for o in penv.operations(owner)] == [final_row.id]
    assert penv.pool(owner) is None
    assert len(penv.class_playlists(owner)) == 1
    assert await connections.give_pool_consent(penv.factory, ctx, 1)
    penv.fake.seen.clear()
    assert (await penv.run(penv.job(owner))).state == "completed"
    assert penv.pool(owner).playlist_id != old
    assert not requests_to(penv, old)
    assert {s.token_app for s in penv.api()} == {CLIENT_B}


async def test_an_uncertain_pool_create_is_never_offered_and_the_next_pick_supersedes_it(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    first = penv.job(owner)
    penv.fake.inject("status", method="POST", path=MINE, status=503)
    row = await penv.run(first)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    [stuck] = penv.operations(owner)
    assert (stuck.kind, stuck.state, stuck.class_id) == (CREATE, "uncertain", None)

    actor = await penv.spot.browser(owner)
    listed = await actor.get(OPS)
    assert listed.json() == {"operations": []}
    assert (await actor.get(f"{OPS}?job_id={first}")).json() == {"operations": []}
    for path in (f"{OPS}/{stuck.id}",):
        assert (await actor.get(path)).status_code == 404
    for action in ("check-again", "do-it-anyway"):
        answer = await actor.post(f"{OPS}/{stuck.id}/{action}")
        assert (answer.status_code, answer.json()) == (404, {"detail": {"reason": "not_found"}})
    [same] = penv.operations(owner)
    assert (same.state, same.request_context) == ("uncertain", stuck.request_context)

    second = penv.job(owner)
    assert (await penv.run(second)).state == "completed"
    old, new, _ = penv.operations(owner)
    assert (old.id, old.state, old.resolved_by) == (stuck.id, "failed", "worker")
    assert new.idempotency_key == f"{second}:pool-create"
    assert new.request_context["ref"] == ref_of(new.id) != stuck.request_context["ref"]
    [playlist] = penv.fake.playlists.values()
    assert f"classbeat ref {ref_of(new.id)}" in playlist.description


async def test_an_orphaned_pending_pool_create_is_superseded_by_the_next_pick(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    first = penv.job(owner)
    await penv.crash_during(first, "POST", MINE)  # it did create the playlist
    [orphan] = penv.operations(owner)
    assert (orphan.state, orphan.call_ended_at) == ("pending", None)
    second = penv.job(owner)
    row = await penv.run(second)  # the first pick is still queued: not touched, requeued
    assert (row.state, row.attempt) == ("queued", 0)
    assert penv.operations(owner)[0].state == "pending"
    penv.sql(FINISHED,
             id=first)  # fmt: skip
    assert (await penv.run(second)).state == "completed"
    old, new, _ = penv.operations(owner)
    assert (old.state, old.resolved_by) == ("failed", "worker")
    assert new.idempotency_key == f"{second}:pool-create"
    assert len(penv.fake.playlists) == 2  # the stray empty one is the accepted risk
    stray = [p for p in penv.fake.playlists.values() if p.id != penv.pool(owner).playlist_id]
    assert [p.entries for p in stray] == [[]]
    assert not [s for s in penv.writes() if s.path.endswith("/items") and stray[0].id in s.path]


# --- crash mid-fill and the stale call ---------------------------------------------------------


async def test_a_crash_mid_fill_never_creates_twice_and_blocks_the_next_replace_until_actionable(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    playlist_id = await first_pick(penv, owner)
    job_id = penv.job(owner, tracks=MANY)
    await penv.crash_during(job_id, "PUT", ITEMS)
    replace = penv.operations(owner, REPLACE)[-1]
    assert (replace.state, replace.call_ended_at) == ("pending", None)

    row = await penv.run(job_id)  # the next attempt: no create, no reconciliation, no request
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    replace = penv.operations(owner, REPLACE)[-1]
    assert replace.state == "uncertain"
    assert len(penv.operations(owner, CREATE)) == 1
    assert len(penv.api(MINE, "POST")) == 1
    assert len(penv.fake.playlists) == 1

    penv.fake.seen.clear()
    pick = penv.job(owner, tracks=TRACKS)
    row = await penv.run(pick)  # the stale call may still be sending: requeued, nothing sent
    assert (row.state, row.attempt) == ("queued", 0)
    assert penv.api() == []
    bound = replace.request_context["bound_seconds"]
    penv.age(replace.id, bound + 31)  # actionable_at has passed
    assert (await penv.run(pick)).state == "completed"
    old = next(o for o in penv.operations(owner, REPLACE) if o.id == replace.id)
    assert (old.state, old.resolved_by) == ("failed", "worker")
    assert penv.pool(owner).playlist_id == playlist_id
    assert penv.entries(playlist_id) == [f"spotify:track:{t}" for t in TRACKS]
    assert not penv.api(MINE, "POST")


async def test_an_older_uncertain_pool_replace_is_superseded_by_the_next_pick(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    playlist_id = await first_pick(penv, owner)
    job_id = penv.job(owner, tracks=MANY)
    penv.fake.inject("drop", method="PUT", path=ITEMS)
    penv.fake.inject("status", method="GET", path=ITEMS, status=500, times=2)  # unreadable
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    stuck = penv.operations(owner, REPLACE)[-1]
    assert stuck.state == "uncertain"
    pick = penv.job(owner, tracks=TRACKS)
    assert (await penv.run(pick)).state == "completed"  # its call returned: actionable at once
    old = next(o for o in penv.operations(owner, REPLACE) if o.id == stuck.id)
    assert (old.state, old.resolved_by) == ("failed", "worker")
    assert penv.entries(playlist_id) == [f"spotify:track:{t}" for t in TRACKS]


async def test_supersede_never_touches_a_final_row_or_another_users_pool_row(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    other = await penv.user(CLIENT_B, USER_B)
    penv.fake.inject("status", method="POST", path=MINE, status=503)
    assert (await penv.run(penv.job(other))).error_category == "uncertain_write"
    [theirs] = penv.operations(other)
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    final = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    penv.fake.inject("status", method="POST", path=MINE, status=503)
    assert (await penv.run(final)).error_category == "uncertain_write"
    [final_row] = penv.operations(owner)
    assert (await penv.run(penv.job(owner))).state == "completed"
    assert penv.operations(other)[0].state == theirs.state == "uncertain"
    assert next(o for o in penv.operations(owner) if o.id == final_row.id).state == "uncertain"


# --- between the job's start and the decide step -----------------------------------------------


async def disconnect(penv: PlayEnv, owner: Any) -> None:
    await connections.disconnect(penv.factory, make_context(owner), "spotify")


async def reconnect(penv: PlayEnv, owner: Any) -> None:
    await penv.spot.seed(make_context(owner), CLIENT_A, USER_A)  # generation + 1


async def other_client(penv: PlayEnv, owner: Any) -> None:
    await penv.spot.seed(make_context(owner), CLIENT_B, USER_A)


async def other_account(penv: PlayEnv, owner: Any) -> None:
    await penv.spot.seed(make_context(owner), CLIENT_A, USER_B)


async def revoke(penv: PlayEnv, owner: Any) -> None:
    assert await connections.revoke_pool_consent(penv.factory, make_context(owner))


CHANGES = [
    (disconnect, "consent_missing"),  # the consent row goes with the connection
    (reconnect, "not_connected"),  # the decide step's hold: another generation
    (other_client, "consent_missing"),
    (other_account, "consent_missing"),
    (revoke, "consent_missing"),
]


@pytest.mark.parametrize(("change", "category"), CHANGES)
async def test_a_change_before_the_pool_write_ends_the_job_with_no_request(
    penv: PlayEnv, change: Any, category: str
) -> None:
    owner = await penv.user()
    playlist_id = await first_pick(penv, owner)
    penv.fake.seen.clear()

    async def hook(_: Any) -> None:
        await change(penv, owner)

    penv.hook = hook
    row = await penv.run(penv.job(owner))
    assert (row.state, row.error_category) == ("failed", category)
    assert penv.api() == []
    assert len(penv.operations(owner, REPLACE)) <= 1  # only the first pick's, if kept
    assert penv.entries(playlist_id) == [f"spotify:track:{t}" for t in TRACKS]


@pytest.mark.parametrize(("change", "category"), [*CHANGES[:4], (revoke, "consent_missing")])
async def test_a_change_after_the_pool_read_is_caught_by_the_decide_step(
    penv: PlayEnv, monkeypatch: pytest.MonkeyPatch, change: Any, category: str
) -> None:
    """The change lands after the job read the pool row, before the operation's decide step:
    the decide step's own share lock, generation, account and pool re-read refuse it."""
    owner = await penv.user()
    await first_pick(penv, owner)
    penv.fake.seen.clear()
    read = pool_module.current_pool

    async def then_change(run: Any, opened: Any) -> Any:
        found = await read(run, opened)
        await change(penv, owner)
        return found

    monkeypatch.setattr(pool_module, "current_pool", then_change)
    row = await penv.run(penv.job(owner))
    assert row.state == "failed"
    assert row.error_category in ("not_connected", "consent_missing")
    if change is revoke:
        assert row.error_category == "consent_missing"
    if change in (reconnect, disconnect, other_client, other_account):
        assert row.error_category == "not_connected"  # the hold fails first
    assert penv.api() == []


@pytest.mark.parametrize("change", [disconnect, reconnect, other_client, other_account])
async def test_a_change_before_a_final_create_ends_the_job_not_connected(
    penv: PlayEnv, change: Any
) -> None:
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)

    async def hook(_: Any) -> None:
        await change(penv, owner)

    penv.hook = hook
    job_id = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert penv.api() == []
    assert penv.operations(owner) == []
    assert penv.class_playlists(owner) == []


# --- final rows: offered to the user, and orphaned ones resolved --------------------------------


async def test_an_uncertain_final_create_is_offered_with_check_again(penv: PlayEnv) -> None:
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    job_id = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    penv.fake.inject("drop", method="POST", path=MINE)
    penv.fake.inject("status", method="GET", path=MINE, status=500)  # the reconciliation fails
    assert (await penv.run(job_id)).error_category == "uncertain_write"
    [row] = penv.operations(owner)
    actor = await penv.spot.browser(owner)
    listed = (await actor.get(OPS)).json()["operations"]
    assert [(o["id"], o["kind"], o["class_id"]) for o in listed] == [
        (str(row.id), CREATE, str(class_id))
    ]
    assert listed[0]["actionable_at"] is None  # one request: actionable at once
    checked = await actor.post(f"{OPS}/{row.id}/check-again")
    assert (checked.status_code, checked.json()["state"]) == (200, "succeeded")
    [made] = penv.class_playlists(owner)
    assert (made.playlist_id, made.create_operation_id) == (next(iter(penv.fake.playlists)), row.id)


async def test_an_orphaned_pending_final_write_becomes_uncertain_for_the_user(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    job_id = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    await penv.crash_during(job_id, "POST", MINE)
    penv.sql(FINISHED,
             id=job_id)  # fmt: skip
    actor = await penv.spot.browser(owner)
    [shown] = (await actor.get(OPS)).json()["operations"]
    assert (shown["kind"], shown["state"]) == (CREATE, "uncertain")


# --- the class rule and the ref ----------------------------------------------------------------


def spotify_request(kind: str, key: str, purpose: str, **kw: Any) -> OperationRequest:
    values: dict[str, Any] = {
        "kind": kind,
        "idempotency_key": key,
        "request_hash": request_hash(kind, x="y"),
        "timeout_seconds": 30,
        "context": {"purpose": purpose},
        **kw,
    }
    if kind == REPLACE:
        values.setdefault("bound_seconds", 120.0)
    else:
        values.setdefault("single_request", True)
        values.setdefault("with_ref", True)
    return OperationRequest(**values)


def test_a_pool_row_has_no_class_and_a_final_row_has_its_class() -> None:
    job, klass = uuid.uuid4(), uuid.uuid4()
    h16 = "0123456789abcdef"
    spotify_request(CREATE, f"{job}:pool-create", "pool")
    spotify_request(CREATE, f"{job}:final-create", "final", class_id=klass)
    spotify_request(REPLACE, f"{job}:pool:{h16}", "pool")
    spotify_request(REPLACE, f"{job}:final-fill", "final", class_id=klass)
    bad = [
        (CREATE, f"{job}:pool-create", "pool", {"class_id": klass}),
        (CREATE, f"{job}:final-create", "final", {}),
        (REPLACE, f"{job}:pool:{h16}", "pool", {"class_id": klass}),
        (REPLACE, f"{job}:final-fill", "final", {}),
    ]
    for kind, key, purpose, kw in bad:
        with pytest.raises(ValueError, match="final-playlist write names its class"):
            spotify_request(kind, key, purpose, **kw)
    for kind, key in [
        (CREATE, f"{job}:pool"), (CREATE, f"{job}:final-fill"), (REPLACE, f"{job}:pool-create"),
        (REPLACE, f"{job}:pool:XYZ"), (REPLACE, "spotify:abc"), (CREATE, "pool-create"),
    ]:  # fmt: skip
        with pytest.raises(ValueError, match="key"):
            spotify_request(kind, key, "pool")
    with pytest.raises(ValueError, match="purpose"):  # the context names the key's purpose
        spotify_request(CREATE, f"{job}:pool-create", "final")
    with pytest.raises(ValueError, match="one request"):
        spotify_request(CREATE, f"{job}:pool-create", "pool", with_ref=False)
    with pytest.raises(ValueError, match="several requests needs its bound"):
        spotify_request(REPLACE, f"{job}:final-fill", "final", class_id=klass, bound_seconds=None)


def test_the_ref_is_stored_by_the_protocol_from_the_rows_own_id() -> None:
    job = uuid.uuid4()
    made = spotify_request(CREATE, f"{job}:pool-create", "pool")
    row = uuid.uuid4()
    assert made.stored_context(row) == {"purpose": "pool", "ref": row.hex[:12]}
    assert ref_of(row) == row.hex[:12]
    with pytest.raises(ValueError, match="stored by the protocol"):
        spotify_request(
            CREATE, f"{job}:pool-create", "pool", context={"purpose": "pool", "ref": "x"}
        )


def test_the_bound_of_a_replace_counts_its_requests() -> None:
    assert replace.bound_seconds(0) == 30 * (3 + 2 + 1)
    assert replace.bound_seconds(120) == 30 * (3 + 2 * 2 + 3)
    assert replace.bound_seconds(1000) == 30 * (3 + 2 * 10 + 20)
    assert replace.bound_seconds(10**6) == 1800.0  # capped
    assert common.ids_hash(["a"]) != common.ids_hash(["a", "a"])


async def test_the_decide_step_compares_the_spotify_account_too(penv: PlayEnv) -> None:
    """A row that names another Spotify user than the one opened never holds, even at the same
    generation (defence in depth: an account change also bumps the generation)."""
    owner = await penv.user()
    await first_pick(penv, owner)
    penv.fake.seen.clear()

    async def hook(_: Any) -> None:
        penv.sql(
            "UPDATE connections SET public = jsonb_set(public, '{spotify_user_id}', '\"x\"') "
            "WHERE owner_id = :o AND provider = 'spotify'",
            o=owner,
        )

    penv.hook = hook
    row = await penv.run(penv.job(owner))
    assert (row.state, row.error_category) == ("failed", "not_connected")
    assert penv.api() == []
    assert len(penv.operations(owner)) == 2  # refused at the decide step: no row was written


async def test_a_call_after_the_decide_step_goes_out_only_on_the_held_connection(
    penv: PlayEnv,
) -> None:
    """``spotify_call(expect=...)``: once the connection is reconnected (another generation) or
    names another account, nothing is sent with it, refreshed or not."""
    owner = await penv.user()
    ctx = make_context(owner)
    held = await open_connection(penv.factory, penv.keyring, ctx, "spotify")
    assert held.account == USER_A
    assert (await spotify_call(penv.factory, penv.keyring, ctx, spotify_web.me, expect=held,
                               http=penv.spot.http))["id"] == USER_A  # fmt: skip
    await reconnect(penv, owner)
    penv.fake.seen.clear()
    with pytest.raises(ConnectionFailure) as refused:
        await spotify_call(
            penv.factory, penv.keyring, ctx, spotify_web.me, expect=held, http=penv.spot.http
        )
    assert (refused.value.category, refused.value.not_applied) == ("not_connected", True)
    assert penv.fake.seen == []
