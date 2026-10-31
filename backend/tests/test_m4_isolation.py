"""M4 done-when, isolation (ADR 0008 section 12, ADR 0009 section 13; issue SEC1, #185).

Two users on **different Client IDs** (``CLIENT_A`` / ``rider-one`` and ``CLIENT_B`` /
``rider-two``), each with their own Spotify tokens, MOWL token, music and playlists, run the whole
``design_with_songs`` flow at once through the real kind and the real jobs API: both picks run
concurrently to their wait, both are approved in their own browsers, and both second phases run
concurrently to the verified final playlist and the first design. The fake Spotify records the
``Authorization`` of every request and the Client ID and user its token was issued to, so the
checks are on what was sent, not on what classbeat believes it sent.

Then: every id of one user answers 404 to the other on every route that takes one; a stored
playlist id edited to the other user's playlist writes nothing to it (through a full job); a
playlist id in the browser's input is refused; and no playlist id is ever written to that the
user's own create did not return. Fakes only: no network, no real account.
"""

import asyncio
import uuid
from typing import Any
from uuid import UUID

import pytest

from classbeat.jobs.runner import RunConfig, execute
from optools import ACCOUNT_BASE, TOKEN_A, TOKEN_B
from songtools import CHOSEN, POOL, Rider, SongEnv, design_script, new_key
from spotifyfake import CLIENT_A, CLIENT_B, USER_A, USER_B

pytestmark = pytest.mark.usefixtures("songenv")

ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"
APP_OF = {USER_A: CLIENT_A, USER_B: CLIENT_B}
CREATE = ("POST", "/v1/me/playlists")


async def two_riders(songenv: SongEnv) -> tuple[Rider, Rider]:
    alice = await songenv.rider(client_id=CLIENT_A, spotify_user=USER_A, mowl_token=TOKEN_A)
    bob = await songenv.rider(client_id=CLIENT_B, spotify_user=USER_B, mowl_token=TOKEN_B)
    return alice, bob


async def run_at_once(songenv: SongEnv, job_ids: list[UUID]) -> list[Any]:
    """Claim every job of ``job_ids`` first, then run their attempts concurrently."""
    for job_id in job_ids:
        songenv.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
    executions = [await songenv.claim() for _ in job_ids]
    assert all(e is not None for e in executions)
    config = RunConfig(lease_seconds=songenv.spot.env.settings.job_lease_seconds)
    await asyncio.gather(
        *(
            execute(e, songenv.factory, songenv.registry(), config, asyncio.Event())
            for e in executions
            if e is not None
        )
    )
    return [songenv.row(j) for j in job_ids]


async def whole_flow_at_once(songenv: SongEnv, riders: tuple[Rider, ...]) -> list[UUID]:
    """Both picks at once to their wait, both approved, both second phases at once."""
    jobs = [await songenv.created(r) for r in riders]
    rows = await run_at_once(songenv, jobs)
    assert [r.state for r in rows] == ["waiting_for_approval"] * len(riders), [
        (r.state, r.error_category) for r in rows
    ]
    for rider, row in zip(riders, rows, strict=True):
        digest = bytes(row.approval_hash).hex()
        answer = await rider.approve(row.id, row.approval_revision_id, digest)
        assert answer.status_code == 200, answer.text
    songenv.script = design_script()
    rows = await run_at_once(songenv, jobs)
    assert [(r.state, r.error_category) for r in rows] == [("completed", None)] * len(riders)
    return jobs


def created_ids(songenv: SongEnv, user: str) -> set[str]:
    """The playlist ids the fake created for ``user`` (what a create returned to that user)."""
    return {pid for pid, p in songenv.fake.playlists.items() if p.owner == user}


async def test_two_users_on_different_client_ids_run_the_whole_flow_at_once_and_never_mix(
    songenv: SongEnv,
) -> None:
    alice, bob = await two_riders(songenv)
    jobs = await whole_flow_at_once(songenv, (alice, bob))
    users = {alice.user_id: USER_A, bob.user_id: USER_B}
    tokens = {alice.user_id: TOKEN_A, bob.user_id: TOKEN_B}

    seen = songenv.api()
    assert seen
    # Every request carried a token, and every token is one user's own on that user's own app.
    assert {(s.token_app, s.token_user) for s in seen} == {(CLIENT_A, USER_A), (CLIENT_B, USER_B)}
    # Nothing reached the token endpoint (no refresh was due), and no Client Secret anywhere.
    assert songenv.spot.token_requests() == []
    assert not any(s.client_secret for s in songenv.fake.requests())

    for owner, user in users.items():
        mine = created_ids(songenv, user)
        pool = songenv.pool(owner)
        [final] = songenv.class_playlists(owner)
        assert {pool.playlist_id, final.playlist_id} == mine  # two creates, both the user's
        for pid in mine:
            playlist = songenv.fake.playlists[pid]
            assert (playlist.owner, playlist.public, playlist.collaborative) == (user, False, False)
            # Every request that named the playlist carried its own user's token and app.
            named = [s for s in seen if pid in s.path]
            assert named
            assert {(s.token_app, s.token_user) for s in named} == {(APP_OF[user], user)}
        assert songenv.fake.playlists[pool.playlist_id].entries == [
            f"spotify:track:{i}" for i in POOL
        ]
        assert songenv.fake.playlists[final.playlist_id].entries == [
            f"spotify:track:{i}" for i in CHOSEN
        ]
        # Each user's creates went out with that user's token only.
        creates = [s for s in seen if (s.method, s.path) == CREATE and s.token_user == user]
        assert len(creates) == 2
        # The rows: each user's own, with the user's own Spotify account and Client ID.
        connection = songenv.sql(
            "SELECT public FROM connections WHERE owner_id = :o AND provider = 'spotify'", o=owner
        ).one()
        assert connection.public == {"client_id": APP_OF[user], "spotify_user_id": user}
        base = ACCOUNT_BASE[tokens[owner]]
        for op in songenv.operations(owner):
            if op.external_id is None:
                continue
            if op.kind.startswith("spotify_"):
                assert op.external_id in mine
            else:  # a MOWL attach: a playlist of the user's own MOWL account
                assert base < int(op.external_id) < base + 1000, op.kind
        # MOWL wrapped each playlist with its own user's MOWL token.
        attached = {sid for token, sid in songenv.mowl.attaches if token == tokens[owner]}
        assert attached == mine

    # No write request ever named a playlist id that the writer's own create did not return.
    for s in seen:
        if s.method in ("PUT", "POST") and s.path != "/v1/me/playlists":
            assert s.token_user is not None
            assert any(pid in s.path for pid in created_ids(songenv, s.token_user)), s.path
    # The two jobs are each their owner's.
    owners = [songenv.row(j).owner_id for j in jobs]
    assert owners == [alice.user_id, bob.user_id]


async def delete(rider: Rider, path: str) -> Any:
    headers = await rider.browser.headers()
    response = await rider.browser.client.request("DELETE", path, headers=headers)
    rider.browser.client.cookies.clear()
    return response


async def test_every_id_of_the_other_user_is_not_found(songenv: SongEnv) -> None:
    alice, bob = await two_riders(songenv)
    [job_a, _] = await whole_flow_at_once(songenv, (alice, bob))
    row = songenv.row(job_a)
    class_a, list_a = row.class_id, row.approval_revision_id
    draft_a = songenv.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_a
    ).scalar_one()
    ops_a = [o.id for o in songenv.operations(alice.user_id)]
    assert ops_a
    playlists_before = {pid: list(p.entries) for pid, p in songenv.fake.playlists.items()}
    requests_before = len(songenv.fake.requests())
    digest = bytes(row.approval_hash).hex()

    gets = [
        f"/api/jobs/{job_a}",
        f"/api/jobs/{job_a}/revision",
        f"/api/classes/{class_a}",
        f"/api/classes/{class_a}/revisions",
        f"/api/classes/{class_a}/revisions/{list_a}",
        f"/api/classes/{class_a}/revisions/{draft_a}",
        f"/api/classes/{class_a}/messages",
        *(f"/api/external-operations/{o}" for o in ops_a),
    ]
    for path in gets:
        response = await bob.browser.get(path)
        assert response.status_code == 404, (path, response.status_code)
    posts: list[tuple[str, Any]] = [
        (f"/api/jobs/{job_a}/cancel", None),
        (f"/api/jobs/{job_a}/approve", {"revision_id": str(list_a), "content_hash": digest}),
        (
            f"/api/classes/{class_a}/current",
            {"revision_id": str(draft_a), "content_hash": "0" * 64},
        ),
        *((f"/api/external-operations/{o}/check-again", None) for o in ops_a),
        *((f"/api/external-operations/{o}/do-it-anyway", None) for o in ops_a),
    ]
    for path, body in posts:
        response = await bob.browser.post(path, body)
        assert response.status_code == 404, (path, response.status_code, response.text)
    response = await delete(bob, f"/api/classes/{class_a}")
    assert response.status_code == 404, response.text

    # Jobs aimed at Alice's class, her approved list or her connections.
    aimed = [
        bob.input(class_id=str(class_a)),
        bob.input(
            class_id=str(class_a), mode="resume", resume={"song_list_revision_id": str(list_a)}
        ),
    ]
    for values in aimed:
        body = {"kind": "design_with_songs", "input": values}
        response = await bob.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
        assert response.status_code == 404, (values, response.status_code, response.text)
    # A connection id is compared with the user's own current one only (the jobs API's documented
    # 409 ``not_connected``): Alice's ids get exactly the answer of ids that exist nowhere.
    for provider, theirs in (
        ("spotify", alice.spotify_connection_id),
        ("mowl", alice.mowl_connection_id),
    ):
        answers = []
        for value in (theirs, uuid.uuid4()):
            body = {
                "kind": "design_with_songs",
                "input": bob.input(**{f"{provider}_connection_id": str(value)}),
            }
            response = await bob.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
            answers.append((response.status_code, response.json()))
        assert answers[0] == answers[1] == (409, {"detail": {"reason": "not_connected"}})

    # The lists of Bob's own things show none of Alice's.
    listed = (await bob.browser.get("/api/classes")).text + (
        await bob.browser.get("/api/external-operations")
    ).text
    for value in [str(class_a), str(job_a), *(str(o) for o in ops_a)]:
        assert value not in listed
    # Nothing of Alice's changed and nothing more reached Spotify.
    assert songenv.row(job_a).state == "completed"
    assert {pid: list(p.entries) for pid, p in songenv.fake.playlists.items()} == playlists_before
    assert len(songenv.fake.requests()) == requests_before


@pytest.mark.parametrize("visible", [False, True])
async def test_a_pool_id_edited_to_the_other_users_pool_writes_nothing_to_it(
    songenv: SongEnv, visible: bool
) -> None:
    """Through a full pick: Bob's stored pool id is Alice's pool id. If Alice's playlist is
    invisible to Bob's token (Spotify's 404), it is gone as far as Bob's account can tell: the id
    is forgotten and the retry makes Bob a pool of his own. If it is readable, it is another
    owner's: ``pool_unusable``. Either way nothing is sent to Alice's playlist."""
    alice, bob = await two_riders(songenv)
    await songenv.waiting(alice)
    theirs = songenv.pool(alice.user_id).playlist_id
    alice_entries = list(songenv.fake.playlists[theirs].entries)
    await songenv.waiting(bob)
    songenv.sql(
        "UPDATE spotify_pool SET playlist_id = :p WHERE owner_id = :o", p=theirs, o=bob.user_id
    )
    if visible:
        songenv.fake.foreign = "visible"
    job_id = await songenv.created(bob)
    row = await songenv.run(job_id)
    if visible:
        assert (row.state, row.error_category) == ("failed", "pool_unusable")
        assert songenv.pool(bob.user_id).playlist_id == theirs  # never adopted or rewritten
    else:
        # Retried: the id and every pool operation row are forgotten.
        assert row.state == "queued", (row.state, row.error_category)
        assert songenv.pool(bob.user_id).playlist_id is None
        row = await songenv.run(job_id)
        assert row.state == "waiting_for_approval"
        assert songenv.pool(bob.user_id).playlist_id in created_ids(songenv, USER_B)
    named = [s for s in songenv.api() if theirs in s.path]
    assert all(s.method == "GET" for s in named if s.token_user == USER_B)
    assert all(s.token_user == USER_A for s in named if s.method != "GET")
    assert songenv.fake.playlists[theirs].entries == alice_entries


@pytest.mark.parametrize("visible", [False, True])
async def test_a_final_playlist_id_edited_to_the_other_users_is_never_written(
    songenv: SongEnv, visible: bool
) -> None:
    """Through a full second phase: Bob's create succeeded and his fill was refused before it was
    sent (retried); before the retry his stored final id is edited to Alice's final playlist. The
    retry takes the id from its own succeeded create, never from the edited row: Bob's own
    playlist is filled and becomes the class's music, and no request of Bob's names Alice's."""
    alice, bob = await two_riders(songenv)
    await whole_flow_at_once(songenv, (alice,))
    [alice_final] = songenv.class_playlists(alice.user_id)
    theirs = alice_final.playlist_id
    alice_entries = list(songenv.fake.playlists[theirs].entries)

    job_b, _ = await songenv.approved(bob)
    songenv.fake.inject("refuse", method="PUT", path=ITEMS)
    row = await songenv.run(job_b)
    assert row.state == "queued", (row.state, row.error_category)
    [bob_final] = songenv.class_playlists(bob.user_id)
    own = bob_final.playlist_id
    songenv.sql(
        "UPDATE class_playlists SET playlist_id = :p WHERE id = :id", p=theirs, id=bob_final.id
    )
    if visible:
        songenv.fake.foreign = "visible"
    row = await songenv.run(job_b)
    assert (row.state, row.error_category) == ("completed", None)
    assert not [s for s in songenv.api() if theirs in s.path and s.token_user == USER_B]
    assert songenv.fake.playlists[theirs].entries == alice_entries
    assert songenv.fake.playlists[own].entries == [f"spotify:track:{i}" for i in CHOSEN]
    verified = [p for p in songenv.class_playlists(bob.user_id) if p.state == "verified"]
    assert [p.playlist_id for p in verified] == [own]
    music = songenv.sql(
        "SELECT music_source FROM classes WHERE id = :c", c=row.class_id
    ).scalar_one()
    assert music["spotify_playlist_id"] == own


@pytest.mark.parametrize(
    "extra",
    [
        {"playlist_id": "0" * 22},
        {"spotify_playlist_id": "0" * 22},
        {"pool_playlist_id": "0" * 22},
        {"spotify_user_id": USER_B},
        {"client_id": CLIENT_B},
    ],
)
async def test_a_playlist_or_account_in_the_browsers_input_is_refused(
    songenv: SongEnv, extra: dict[str, str]
) -> None:
    rider = await songenv.rider()
    before = songenv.counts()
    body = {"kind": "design_with_songs", "input": rider.input() | extra}
    response = await rider.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})
    assert response.status_code == 422, response.text
    assert (
        songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=rider.user_id).scalar_one()
        == 0
    )
    assert songenv.counts() == before


async def test_an_approval_carrying_a_playlist_id_is_refused(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision_id, digest = await songenv.waiting(rider)
    before = songenv.counts()
    body = {"revision_id": str(revision_id), "content_hash": digest, "playlist_id": "0" * 22}
    response = await rider.browser.post(f"/api/jobs/{job_id}/approve", body)
    assert response.status_code == 422, response.text
    assert songenv.row(job_id).approved_at is None
    assert songenv.counts() == before
