"""The two Spotify writes through a job (ADR 0008 section 2; issue OP1, #178): the pool's create and
replace, the final playlist's create and fill, their outcomes under failure injection at every
request, and the owner check that sends no write to a playlist that is not the user's own private
one. Fakes only (``spotifycore``): no network, no real account.

Criteria of #178 covered here: the failure-injection matrix (refuse before send, drop after send,
answer without id, partial write; read-back mismatch), "a write to a playlist that is public,
collaborative, another user's, gone, or whose stored id was edited to another user's id sends no
write request", and a take-over resetting ``pending_at``, ``call_ended_at`` and the context.
"""

from datetime import datetime, timedelta
from typing import Any

import pytest

from classbeat.operations.kinds import ref_of
from classbeat.playlists import POOL_NAME, common
from classbeat.playlists import create as create_kind
from jobtools import new_class
from playlisttools import MANY, TRACKS, PlayEnv
from spotifyfake import USER_B

CREATE, REPLACE = "spotify_create_playlist", "spotify_replace_items"
ITEMS = r"/v1/playlists/[0-9A-Za-z]{22}/items"
ONE = r"/v1/playlists/[0-9A-Za-z]{22}"
MINE = "/v1/me/playlists"


def states(rows: list[Any]) -> list[tuple[str, str, str | None]]:
    return [(r.kind, r.state, r.resolved_by) for r in rows]


# --- the happy paths ---------------------------------------------------------------------------


async def test_a_pool_write_creates_once_then_replaces_by_id_and_reads_back(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    job_id = penv.job(owner, tracks=MANY)
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    [written] = penv.results
    pool = penv.pool(owner)
    assert (pool.playlist_id, written.created) == (written.playlist_id, True)
    assert bytes(pool.last_hash) == common.ids_hash(MANY)
    assert pool.last_written_at is not None
    playlist = penv.fake.playlists[written.playlist_id]
    assert (playlist.owner, playlist.public, playlist.collaborative) == ("rider-one", False, False)
    assert playlist.name == POOL_NAME
    assert penv.entries(written.playlist_id) == [f"spotify:track:{t}" for t in MANY]
    create, replace = penv.operations(owner)
    assert states([create, replace]) == [
        (CREATE, "succeeded", "worker"), (REPLACE, "succeeded", "worker")
    ]  # fmt: skip
    assert (create.class_id, replace.class_id) == (None, None)
    assert create.idempotency_key == f"{job_id}:pool-create"
    assert replace.idempotency_key == f"{job_id}:pool:{common.ids_hash(MANY).hex()[:16]}"
    ref = ref_of(create.id)
    assert create.request_context == {"purpose": "pool", "pool_id": str(pool.id), "ref": ref}
    assert f"classbeat ref {ref}" in playlist.description
    assert replace.request_context["playlist_id"] == written.playlist_id
    assert replace.request_context["count"] == 120
    assert replace.request_context["bound_seconds"] == 30 * (3 + 2 * 2 + 3)
    assert replace.result["count"] == 120
    # one create, the owner check, a PUT of 100, a POST of 20, three pages read back
    assert [(s.method, s.path.rsplit("/", 1)[-1]) for s in penv.api()] == [
        ("POST", "playlists"), ("GET", written.playlist_id), ("PUT", "items"),
        ("POST", "items"), ("GET", "items"), ("GET", "items"), ("GET", "items"),
    ]  # fmt: skip
    assert all(s.token_user == "rider-one" for s in penv.api())

    # A second pick of the user: no create, the same pool id, overwritten.
    again = penv.job(owner, tracks=TRACKS)
    assert (await penv.run(again)).state == "completed"
    assert (penv.results[-1].playlist_id, penv.results[-1].created) == (written.playlist_id, False)
    assert len(penv.writes()) == 3 + 1  # one create in all, then one PUT
    assert penv.entries(written.playlist_id) == [f"spotify:track:{t}" for t in TRACKS]
    assert len(penv.fake.playlists) == 1


async def test_a_final_playlist_is_created_for_its_class_then_filled_and_verified(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    job_id = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    assert (await penv.run(job_id)).state == "completed"
    [made] = penv.results
    [row] = penv.class_playlists(owner)
    assert (row.id, row.playlist_id, row.class_id, row.revision_id) == (
        made.id,
        made.playlist_id,
        class_id,
        revision_id,
    )
    assert (row.state, row.spotify_user_id, row.name) == ("created", "rider-one", "Tuesday climb")
    [create] = penv.operations(owner)
    assert (create.class_id, create.idempotency_key) == (class_id, f"{job_id}:final-create")
    assert row.create_operation_id == create.id
    assert create.request_context["purpose"] == "final"

    fill = penv.job(owner, class_id, action="final_fill", class_playlist_id=str(row.id))
    assert (await penv.run(fill)).state == "completed"
    [row] = penv.class_playlists(owner)
    assert (row.state, bytes(row.content_hash), row.track_count) == (
        "verified",
        common.ids_hash(TRACKS),
        len(TRACKS),
    )
    assert penv.entries(made.playlist_id) == [f"spotify:track:{t}" for t in TRACKS]
    [_, replace] = penv.operations(owner)
    assert (replace.class_id, replace.idempotency_key) == (class_id, f"{fill}:final-fill")


# --- failure injection: the create -------------------------------------------------------------


async def test_a_create_refused_before_it_was_sent_is_failed_and_the_retry_takes_it_over(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    job_id = penv.job(owner)
    penv.fake.inject("refuse", method="POST", path=MINE)
    row = await penv.run(job_id)
    assert (row.state, row.attempt) == ("queued", 1)  # retried (a retryable category)
    [create] = penv.operations(owner)
    assert (create.state, create.resolved_by) == ("failed", "worker")
    assert create.call_ended_at >= create.pending_at
    assert penv.fake.playlists == {}
    first = create

    penv.age(first.id, 5)  # so the take-over's new timestamps are visibly later
    first = penv.operations(owner)[0]
    assert (await penv.run(job_id)).state == "completed"
    create, _ = penv.operations(owner)
    assert create.id == first.id  # the same row: the same ref, since nothing was created
    assert (create.state, create.resolved_by) == ("succeeded", "worker")
    assert create.pending_at > first.pending_at
    assert create.call_ended_at >= create.pending_at
    assert create.request_context == {
        "purpose": "pool", "pool_id": str(penv.pool(owner).id), "ref": ref_of(create.id)
    }  # fmt: skip
    assert len(penv.fake.playlists) == 1


@pytest.mark.parametrize(
    ("injection", "options"),
    [
        ("status", {"status": 400}),
        ("status", {"status": 403, "message": "not yours"}),
        ("status", {"status": 401}),
    ],
)
async def test_a_create_spotify_refused_is_failed_and_creates_nothing(
    penv: PlayEnv, injection: str, options: dict[str, Any]
) -> None:
    owner = await penv.user()
    job_id = penv.job(owner)
    penv.fake.inject(injection, method="POST", path=MINE, **options)
    row = await penv.run(job_id)
    [create] = penv.operations(owner)
    assert create.state == "failed"
    assert penv.fake.playlists == {}
    assert row.state in ("failed", "queued")
    assert penv.pool(owner).playlist_id is None


@pytest.mark.parametrize("injection", ["drop", "noid"])
async def test_a_create_dropped_or_answered_without_an_id_is_uncertain_and_found_by_its_ref(
    penv: PlayEnv, injection: str
) -> None:
    owner = await penv.user()
    job_id = penv.job(owner)
    penv.fake.inject(injection, method="POST", path=MINE)
    row = await penv.run(job_id)
    assert row.state == "completed"
    create, replace = penv.operations(owner)
    assert (create.state, create.resolved_by) == ("succeeded", "worker")
    [made] = penv.fake.playlists
    assert create.external_id == made == penv.pool(owner).playlist_id
    states_seen = [e["state"] for e in penv.events(job_id) if e["kind"] == CREATE]
    assert states_seen == ["pending", "uncertain", "succeeded"]  # recorded uncertain first
    assert replace.state == "succeeded"


async def test_a_create_answered_5xx_stays_uncertain_when_the_listing_shows_nothing(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    job_id = penv.job(owner)
    penv.fake.inject("status", method="POST", path=MINE, status=503)
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")  # never retried
    [create] = penv.operations(owner)
    assert create.state == "uncertain"
    assert len(penv.api(MINE, "GET")) == 1  # the one automatic reconciliation
    assert penv.pool(owner).playlist_id is None


async def test_a_create_whose_reconciliation_cannot_list_stays_uncertain(penv: PlayEnv) -> None:
    owner = await penv.user()
    job_id = penv.job(owner)
    penv.fake.inject("drop", method="POST", path=MINE)
    penv.fake.inject("status", method="GET", path=MINE, status=500)
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    [create] = penv.operations(owner)
    assert create.state == "uncertain"
    assert len(penv.fake.playlists) == 1  # it did happen; nobody points at it yet


class _Later(datetime):
    """The create reconciler's clock, an hour ahead: the listing lag has surely passed."""

    @classmethod
    def now(cls, tz: Any = None) -> Any:
        return datetime.now(tz) + timedelta(hours=1)


async def test_the_jobs_own_reconciliation_never_fails_a_create_on_the_listing_lag(
    penv: PlayEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#198 item 1: "none listed after the lag" is no proof, so the job's own reconciliation
    leaves the row ``uncertain`` (the job fails ``uncertain_write``) instead of ``failed``, which
    the job's next attempt would take over and send again under the same ``ref``. The user's
    Check again still applies the lag (ADR 0008 section 2's table); its row is never sent again."""
    monkeypatch.setattr(create_kind, "datetime", _Later)
    current = common.runtime()
    common.configure(common.Runtime(current.http, current.margin_seconds, listing_lag_seconds=60))
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    job_id = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    penv.fake.inject("status", method="POST", path=MINE, status=503)  # may have been applied
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")  # not retried
    [create] = penv.operations(owner)
    assert (create.state, create.resolved_by) == ("uncertain", None)
    assert len(penv.api(MINE, "POST")) == 1
    assert len(penv.api(MINE, "GET")) == 1  # the reconciliation ran, and found nothing

    actor = await penv.spot.browser(owner)
    checked = await actor.post(f"/api/external-operations/{create.id}/check-again")
    assert (checked.status_code, checked.json()["state"]) == (200, "failed")
    [create] = penv.operations(owner)
    assert (create.state, create.resolved_by) == ("failed", "user")
    assert len(penv.api(MINE, "POST")) == 1


@pytest.mark.parametrize("purpose", ["pool", "final"])
@pytest.mark.parametrize("resolved_by", ["user", "admin"])
async def test_a_failed_create_its_call_did_not_record_is_never_taken_over(
    penv: PlayEnv, resolved_by: str, purpose: str
) -> None:
    """#198 items 1 and 17a: only a ``failed`` row the call's own record wrote (a refusal:
    ``resolved_by = 'worker'``) is taken over, through ``pool_request`` and ``final_request``
    alike. One the user resolved (Check again, Do it anyway) may have been created: the next
    attempt ends ``uncertain_write`` and sends nothing."""
    owner = await penv.user()
    if purpose == "pool":
        job_id = penv.job(owner)
    else:
        class_id = new_class(penv.admin, owner)
        revision_id, content_hash = penv.song_list(owner, class_id)
        job_id = penv.job(
            owner, class_id, action="final_create", revision_id=str(revision_id),
            content_hash=content_hash.hex(),
        )  # fmt: skip
    penv.fake.inject("refuse", method="POST", path=MINE)
    assert (await penv.run(job_id)).state == "queued"  # a refusal: retried
    [create] = penv.operations(owner)
    assert (create.state, create.resolved_by) == ("failed", "worker")
    penv.sql(
        "UPDATE external_operations SET resolved_by = :by WHERE id = :id",
        by=resolved_by,
        id=create.id,
    )
    penv.fake.seen.clear()
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    assert penv.api() == []  # not even the create
    [after] = penv.operations(owner)
    assert (after.state, after.resolved_by, after.pending_at) == (
        "failed", resolved_by, create.pending_at
    )  # fmt: skip
    assert penv.fake.playlists == {}
    assert penv.class_playlists(owner) == []


# --- failure injection: the replace (pool and final fill) --------------------------------------


async def made_pool(penv: PlayEnv, owner: Any) -> str:
    """A pool playlist made by a first pick; the fake's request log starts empty after it."""
    first = penv.job(owner)
    assert (await penv.run(first)).state == "completed"
    penv.fake.seen.clear()
    return penv.pool(owner).playlist_id


# (injection, options, track list, outcome row state, resolved_by, job state, its category (a
#  queued job, retried, keeps none),
#  the playlist afterwards: "new" = the new list, "old" = unchanged)
REPLACE_CASES = [
    # The owner check (the first request): a refusal before sending or a 5xx is uncertain; the
    # one reconciliation reads the playlist (unchanged, the call over): failed, so the job retries.
    ("refuse", {"method": "GET", "path": ONE}, MANY, "failed", "worker", "queued",
     None, "old"),
    ("status", {"method": "GET", "path": ONE, "status": 502}, MANY, "failed", "worker", "queued",
     None, "old"),
    ("drop", {"method": "GET", "path": ONE}, MANY, "failed", "worker", "queued",
     None, "old"),
    # The PUT: refused before sending, uncertain; the reconciliation proves it unchanged.
    ("refuse", {"method": "PUT", "path": ITEMS}, MANY, "failed", "worker", "queued",
     None, "old"),
    # The PUT applied, its answer dropped: the POST never went, so the read shows a partial list.
    ("drop", {"method": "PUT", "path": ITEMS}, MANY, "failed", "worker", "queued",
     None, "partial"),
    # The PUT applied and answered without a snapshot id: the read-back still proves it.
    ("noid", {"method": "PUT", "path": ITEMS}, MANY, "succeeded", "worker", "completed", None,
     "new"),
    # A partial PUT answered 200: the read-back differs, a definite mismatch.
    ("partial", {"method": "PUT", "path": ITEMS, "keep": 3}, MANY, "failed", "worker", "failed",
     "playlist_mismatch", "partial"),
    # A partial PUT answered 500: uncertain; the reconciliation reads a different list.
    ("partial", {"method": "PUT", "path": ITEMS, "keep": 3, "status": 500}, MANY, "failed",
     "worker", "queued", None, "partial"),
    # The POST applied, its answer dropped: uncertain; the reconciliation finds the full list.
    ("drop", {"method": "POST", "path": ITEMS}, MANY, "succeeded", "worker", "completed", None,
     "new"),
    ("refuse", {"method": "POST", "path": ITEMS}, MANY, "failed", "worker", "queued",
     None, "partial"),
    # The read-back fails: uncertain; the reconciliation reads it again and finds it equal.
    ("refuse", {"method": "GET", "path": ITEMS}, MANY, "succeeded", "worker", "completed", None,
     "new"),
    ("status", {"method": "GET", "path": ITEMS, "status": 500}, MANY, "succeeded", "worker",
     "completed", None, "new"),
    # The read-back shows another list (a duplicate, a local file, a null item): a mismatch.
    ("duplicate", {"method": "GET", "path": ITEMS}, TRACKS, "failed", "worker", "failed",
     "playlist_mismatch", "new"),
    ("local", {"method": "GET", "path": ITEMS}, TRACKS, "failed", "worker", "failed",
     "playlist_mismatch", "new"),
    ("null", {"method": "GET", "path": ITEMS}, TRACKS, "failed", "worker", "failed",
     "playlist_mismatch", "new"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("injection", "options", "tracks", "state", "by", "job_state", "category", "after"),
    REPLACE_CASES,
)
async def test_failure_injection_at_every_request_of_a_pool_replace(
    penv: PlayEnv,
    injection: str,
    options: dict[str, Any],
    tracks: list[str],
    state: str,
    by: str,
    job_state: str,
    category: str | None,
    after: str,
) -> None:
    owner = await penv.user()
    playlist_id = await made_pool(penv, owner)
    before = penv.entries(playlist_id)
    job_id = penv.job(owner, tracks=tracks)
    penv.fake.inject(injection, **options)
    row = await penv.run(job_id)
    assert (row.state, row.error_category) == (job_state, category)
    replace = penv.operations(owner, REPLACE)[-1]
    assert (replace.state, replace.resolved_by) == (state, by)
    assert replace.call_ended_at is not None
    wanted = [f"spotify:track:{t}" for t in tracks]
    now = penv.entries(playlist_id)
    if after == "new":
        assert now == wanted
    elif after == "old":
        assert now == before
    else:
        assert now not in (wanted, before)
    assert len(penv.fake.playlists) == 1  # never a second playlist
    assert not penv.api(MINE, "POST")
    if job_state == "queued":  # proved unchanged: the retry replaces again and succeeds
        retried = await penv.run(job_id)
        assert retried.state == "completed"
        assert penv.entries(playlist_id) == wanted
        assert not penv.api(MINE, "POST")


async def test_a_final_fill_whose_read_back_differs_is_a_mismatch_and_not_retried(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    made = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    assert (await penv.run(made)).state == "completed"
    [row] = penv.class_playlists(owner)
    fill = penv.job(owner, class_id, action="final_fill", class_playlist_id=str(row.id))
    penv.fake.inject("partial", method="PUT", path=ITEMS, keep=2)
    job = await penv.run(fill)
    assert (job.state, job.error_category) == ("failed", "playlist_mismatch")
    [_, replace] = penv.operations(owner)
    assert (replace.state, replace.error_category) == ("failed", "playlist_mismatch")
    [row] = penv.class_playlists(owner)
    assert (row.state, row.content_hash) == ("created", None)


async def test_a_final_fill_dropped_after_the_put_is_reconciled_from_the_read(
    penv: PlayEnv,
) -> None:
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    made = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    assert (await penv.run(made)).state == "completed"
    [row] = penv.class_playlists(owner)
    fill = penv.job(owner, class_id, action="final_fill", class_playlist_id=str(row.id))
    penv.fake.inject("drop", method="PUT", path=ITEMS)
    assert (await penv.run(fill)).state == "completed"
    [_, replace] = penv.operations(owner)
    assert [e["state"] for e in penv.events(fill)] == ["pending", "uncertain", "succeeded"]
    assert replace.state == "succeeded"
    [row] = penv.class_playlists(owner)
    assert row.state == "verified"


# --- the owner check: no write request to a playlist that is not the user's private one ----------


def foreign_playlist(penv: PlayEnv, *, visible: bool) -> str:
    if visible:
        penv.fake.foreign = "visible"
    return penv.fake.add_playlist(USER_B, "Not yours", ["spotify:track:" + TRACKS[0]]).id


@pytest.mark.parametrize(
    ("make", "category"),
    [
        (lambda penv, mine: penv.fake.playlists[mine].__setattr__("public", True), "pool_unusable"),
        (lambda penv, mine: penv.fake.playlists[mine].__setattr__("collaborative", True),
         "pool_unusable"),
        (lambda penv, mine: penv.fake.playlists.pop(mine), "pool_missing"),
    ],
)  # fmt: skip
async def test_no_write_reaches_a_pool_playlist_that_is_public_collaborative_or_gone(
    penv: PlayEnv, make: Any, category: str
) -> None:
    owner = await penv.user()
    playlist_id = await made_pool(penv, owner)
    make(penv, playlist_id)
    job_id = penv.job(owner, tracks=MANY)
    row = await penv.run(job_id)
    assert not [s for s in penv.writes() if playlist_id in s.path]  # nothing written to it
    if category == "pool_missing":
        # Retried; the pool's id and every pool operation row are forgotten (one transaction).
        assert (row.state, penv.pool(owner).playlist_id) == ("queued", None)
        assert penv.operations(owner) == []
    else:
        [replace] = penv.operations(owner, REPLACE)[1:]
        assert (row.state, row.error_category) == ("failed", category)
        assert (replace.state, replace.error_category) == ("failed", category)
        assert penv.pool(owner).playlist_id == playlist_id  # never flipped back by classbeat


@pytest.mark.parametrize("visible", [False, True])
async def test_a_pool_id_edited_to_another_users_playlist_sends_no_write(
    penv: PlayEnv, visible: bool
) -> None:
    owner = await penv.user()
    await made_pool(penv, owner)
    theirs = foreign_playlist(penv, visible=visible)
    penv.sql("UPDATE spotify_pool SET playlist_id = :p WHERE owner_id = :o", p=theirs, o=owner)
    job_id = penv.job(owner, tracks=TRACKS)
    row = await penv.run(job_id)
    if visible:  # its metadata is readable: another owner
        assert (row.state, row.error_category) == ("failed", "pool_unusable")
    else:  # 404 to us: gone as far as this account can tell; forgotten, and retried
        assert (row.state, penv.pool(owner).playlist_id) == ("queued", None)
    assert not penv.writes(theirs)
    assert penv.entries(theirs) == ["spotify:track:" + TRACKS[0]]
    assert all(s.token_user == "rider-one" for s in penv.api())


@pytest.mark.parametrize("visible", [False, True])
async def test_a_class_playlist_id_edited_to_another_users_playlist_sends_no_write(
    penv: PlayEnv, visible: bool
) -> None:
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    made = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    assert (await penv.run(made)).state == "completed"
    [row] = penv.class_playlists(owner)
    theirs = foreign_playlist(penv, visible=visible)
    penv.sql("UPDATE class_playlists SET playlist_id = :p WHERE id = :id", p=theirs, id=row.id)
    fill = penv.job(owner, class_id, action="final_fill", class_playlist_id=str(row.id))
    job = await penv.run(fill)
    assert (job.state, job.error_category) == ("failed", "forbidden" if visible else "not_found")
    assert not penv.writes(theirs)
    assert penv.entries(theirs) == ["spotify:track:" + TRACKS[0]]
