"""The class API's song views, the playlist link and the failure sentences (ADR 0008 sections 5, 8
and 9; issue CL1, #183). The song lists are made by the real kind against the fakes (``songenv``).

Owner-scoped everywhere: another user's class, revision, job and approve are the same 404; no
Spotify id, track id, account id or fingerprint is in any body except the owner's own playlist
link; a superseded or expired list is reported as such.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from classbeat.classes.song_views import plain
from classbeat.jobs.sentences import SONG_SENTENCES, failure_sentence
from jobtools import new_class, new_job
from songtools import Rider, SongEnv, song_id
from spotifyfake import USER_A
from test_classes_api import content_digest, insert, make_current, revision

pytestmark = pytest.mark.usefixtures("songenv")

NOT_FOUND = {"detail": {"reason": "not_found"}}
PLAYLIST = "0vvXsWCC9xrXsKd4FyS8kM"
OTHER_PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"
VERIFIED = {"content_hash": content_digest({}), "track_count": 3}
TRACK_IDS = [song_id(i) for i in range(20)]


def forbidden(env: SongEnv, *more: str) -> tuple[str, ...]:
    pools = [r.playlist_id for r in env.sql("SELECT playlist_id FROM spotify_pool").all() if r[0]]
    return (*TRACK_IDS, USER_A, "spotify_id", "fingerprint", "spotify_user_id", *pools, *more)


def clean(env: SongEnv, response: Any, *more: str) -> None:
    for bad in forbidden(env, *more):
        assert bad not in response.text, bad


async def delete(rider: Rider, path: str) -> Any:
    response = await rider.browser.client.delete(path, headers=await rider.browser.headers())
    rider.browser.client.cookies.clear()
    return response


def class_of(env: SongEnv, job_id: UUID) -> UUID:
    return env.sql("SELECT class_id FROM jobs WHERE id = :j", j=job_id).scalar_one()


async def test_a_waiting_list_is_reported_on_the_class_and_shown_without_ids(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, revision_id, digest = await songenv.waiting(rider)
    class_id = class_of(songenv, job_id)
    shown = await rider.browser.get(f"/api/classes/{class_id}")
    assert shown.status_code == 200
    songs = shown.json()["songs"]
    assert songs["state"] == "waiting"
    assert (songs["job_id"], songs["revision_id"]) == (str(job_id), str(revision_id))
    assert (songs["content_hash"], songs["expires_at"] is not None) == (digest, True)
    assert (shown.json()["playlist"], shown.json()["spotify_playlists"]) == (None, [])
    clean(songenv, shown)

    got = await rider.browser.get(f"/api/classes/{class_id}/revisions/{revision_id}")
    assert got.status_code == 200, got.text
    body = got.json()
    stored = songenv.revision(revision_id).content
    assert (body["kind"], body["status"], body["content_hash"]) == ("song_list", "waiting", digest)
    assert (body["mode"], body["target_sec"], body["total_sec"]) == (
        stored["mode"], stored["target_sec"], stored["total_sec"],
    )  # fmt: skip
    assert body["job_id"] == str(job_id)
    assert [s["position"] for s in body["songs"]] == list(range(1, len(stored["songs"]) + 1))
    for shown_song, kept in zip(body["songs"], stored["songs"], strict=True):
        assert set(shown_song) == {
            "position", "title", "artist", "role", "bpm", "length_sec", "new"
        }  # fmt: skip
        assert (shown_song["title"], shown_song["new"]) == (kept["title"], kept["new"])
        assert kept["spotify_id"] not in got.text
    assert body["omitted_no_bpm"] == stored["omitted_no_bpm"]
    clean(songenv, got)


async def test_a_notes_markup_and_control_characters_arrive_as_plain_text(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, revision_id, _ = await songenv.waiting(rider)
    shown = (
        await rider.browser.get(f"/api/classes/{class_of(songenv, job_id)}/revisions/{revision_id}")
    ).json()
    messy = "line one" + chr(10) + chr(0) + "line" + chr(9) + "two " + chr(0x2028) + " end"
    assert plain(messy) == "line one line two end"
    assert chr(10) not in shown["note"]
    assert chr(0) not in shown["note"]


async def test_the_state_goes_waiting_approved_and_the_list_is_marked_approved(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, revision_id, digest = await songenv.waiting(rider)
    class_id = class_of(songenv, job_id)
    assert (await rider.approve(job_id, revision_id, digest)).status_code == 200
    songs = (await rider.browser.get(f"/api/classes/{class_id}")).json()["songs"]
    assert (songs["state"], songs["revision_id"]) == ("approved", str(revision_id))
    got = await rider.browser.get(f"/api/classes/{class_id}/revisions/{revision_id}")
    assert (got.json()["status"], got.json()["approved_at"] is not None) == ("approved", True)


async def test_an_expired_list_is_reported_as_expired(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    job_id, revision_id, _ = await songenv.waiting(rider)
    class_id = class_of(songenv, job_id)
    # Past its time but not yet settled by the worker's maintenance.
    songenv.sql("UPDATE jobs SET expires_at = now() - interval '1 minute' WHERE id = :j", j=job_id)
    path = f"/api/classes/{class_id}"
    assert (await rider.browser.get(path)).json()["songs"]["state"] == "expired"
    assert (await rider.browser.get(f"{path}/revisions/{revision_id}")).json()[
        "status"
    ] == "expired"
    # And once the job itself ended expired.
    songenv.sql(
        "UPDATE jobs SET state = 'expired', finished_at = now(), expires_at = now() "
        "WHERE id = :j", j=job_id,
    )  # fmt: skip
    assert (await rider.browser.get(path)).json()["songs"]["state"] == "expired"


async def test_a_cancelled_list_is_cancelled_and_an_older_one_is_superseded(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    first, first_rev, _ = await songenv.waiting(rider)
    class_id = class_of(songenv, first)
    path = f"/api/classes/{class_id}"
    assert (await rider.browser.post(f"/api/jobs/{first}/cancel")).status_code == 200
    songs = (await rider.browser.get(path)).json()["songs"]
    assert (songs["state"], songs["revision_id"]) == ("cancelled", str(first_rev))
    _second, second_rev, second_digest = await songenv.waiting(rider, class_id=str(class_id))
    assert second_rev != first_rev
    old = (await rider.browser.get(f"{path}/revisions/{first_rev}")).json()
    assert old["status"] == "superseded"
    now = (await rider.browser.get(path)).json()["songs"]
    assert (now["state"], now["revision_id"], now["content_hash"]) == (
        "waiting", str(second_rev), second_digest,
    )  # fmt: skip
    # The approve refusal of the old one is the jobs API's, unchanged.
    refused = await rider.approve(
        first, first_rev, bytes(songenv.revision(first_rev).content_hash).hex()
    )
    assert refused.status_code == 409


async def test_every_other_users_ids_are_the_same_404(songenv: SongEnv) -> None:
    mine = await songenv.rider()
    theirs = await songenv.rider()
    job_id, revision_id, digest = await songenv.waiting(theirs)
    class_id = class_of(songenv, job_id)
    own_class = new_class(songenv.admin, mine.user_id, "Mine")
    for path in (
        f"/api/classes/{class_id}",
        f"/api/classes/{class_id}/revisions/{revision_id}",
        f"/api/classes/{own_class}/revisions/{revision_id}",  # their revision under my class
        f"/api/jobs/{job_id}",
    ):
        got = await mine.browser.get(path)
        assert (got.status_code, got.json()) == (404, NOT_FOUND), path
    refused = await mine.approve(job_id, revision_id, digest)
    assert (refused.status_code, refused.json()) == (404, NOT_FOUND)
    deleted = await delete(mine, f"/api/classes/{class_id}")
    assert (deleted.status_code, deleted.json()) == (404, NOT_FOUND)
    # Theirs is untouched and still waits.
    assert songenv.row(job_id).state == "waiting_for_approval"
    assert (await theirs.browser.get(f"/api/classes/{class_id}")).status_code == 200


async def test_the_playlist_link_is_the_owners_own_and_only_once_verified(
    songenv: SongEnv,
) -> None:
    mine = await songenv.rider()
    theirs = await songenv.rider()
    job_id, list_rev, _ = await songenv.waiting(mine)
    class_id = class_of(songenv, job_id)
    admin = songenv.admin
    draft = revision(
        admin, mine.user_id, class_id, 5, music={"song_list_id": str(list_rev), "source": "x"}
    )
    make_current(admin, class_id, draft)
    row = insert(
        admin, "class_playlists", owner_id=mine.user_id, class_id=class_id,
        revision_id=list_rev, playlist_id=PLAYLIST, spotify_user_id=USER_A,
        name="Tuesday climb", state="created",
    )  # fmt: skip
    path = f"/api/classes/{class_id}"
    created = (await mine.browser.get(path)).json()
    assert created["playlist"] == {"name": "Tuesday climb", "link": None, "detached": False}
    admin.exec_driver_sql(
        "UPDATE class_playlists SET state = 'verified', content_hash = %s, track_count = 8 "
        "WHERE id = %s", (content_digest({}), row),
    )  # fmt: skip
    shown = await mine.browser.get(path)
    link = f"https://open.spotify.com/playlist/{PLAYLIST}"
    assert shown.json()["playlist"] == {"name": "Tuesday climb", "link": link, "detached": False}
    assert shown.json()["spotify_playlists"] == [shown.json()["playlist"]]
    # The id appears only inside the two links (current and list); other bodies never carry it.
    assert shown.text.count(PLAYLIST) == shown.text.count(link) == 2
    for other in (
        f"{path}/revisions/{list_rev}",
        f"{path}/revisions/{draft}",
        f"{path}/messages",
        f"{path}/revisions",
        "/api/classes",
        f"/api/jobs/{job_id}",
    ):
        assert PLAYLIST not in (await mine.browser.get(other)).text, other
    # The other user sees nothing of it.
    assert (await theirs.browser.get(path)).status_code == 404


async def test_class_delete_lists_the_playlists_left_in_spotify_and_asks_spotify_nothing(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    job_id, list_rev, _ = await songenv.waiting(rider)
    class_id = class_of(songenv, job_id)
    assert (await rider.browser.post(f"/api/jobs/{job_id}/cancel")).status_code == 200
    admin = songenv.admin
    for playlist, state, detached in (
        (PLAYLIST, "verified", None), (OTHER_PLAYLIST, "created", datetime.now(UTC)),
    ):  # fmt: skip
        insert(
            admin, "class_playlists", owner_id=rider.user_id, class_id=class_id,
            revision_id=list_rev, playlist_id=playlist, spotify_user_id=USER_A,
            name=f"list {state}", state=state, detached_at=detached,
            **(VERIFIED if state == "verified" else {}),
        )  # fmt: skip
    before = songenv.counts()
    deleted = await delete(rider, f"/api/classes/{class_id}")
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["spotify_playlists_left"] == [
        {"name": "list verified", "link": f"https://open.spotify.com/playlist/{PLAYLIST}",
         "detached": False},
        {"name": "list created", "link": None, "detached": True},
    ]  # fmt: skip
    assert "Spotify" in body["spotify_note"]
    assert OTHER_PLAYLIST not in deleted.text
    assert songenv.counts() == before  # nothing sent to Spotify, MOWL or a model
    for table in ("class_revisions", "class_playlists", "jobs", "external_operations"):
        left = songenv.sql(
            f"SELECT count(*) FROM {table} WHERE class_id = :c",  # noqa: S608
            c=class_id,
        ).scalar_one()
        assert left == 0, table


async def test_a_class_delete_without_playlists_says_nothing_about_spotify(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id = new_class(songenv.admin, rider.user_id)
    body = (await delete(rider, f"/api/classes/{class_id}")).json()
    assert (body["spotify_playlists_left"], body["spotify_note"]) == ([], None)


async def test_the_status_lists_what_a_disconnect_or_revoke_leaves_without_links(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    other = await songenv.rider()
    job_id, list_rev, _ = await songenv.waiting(rider)
    class_id = class_of(songenv, job_id)
    admin = songenv.admin
    status = "/api/connections/spotify"
    pool_id = songenv.pool(rider.user_id).playlist_id
    assert pool_id is not None  # the pick wrote its working playlist
    insert(
        admin, "class_playlists", owner_id=rider.user_id, class_id=class_id, revision_id=list_rev,
        playlist_id=PLAYLIST, spotify_user_id=USER_A, name="Tuesday climb", state="created",
    )  # fmt: skip
    got = await rider.browser.get(status)
    body = got.json()
    assert body["pool_playlist"] == "classbeat working copy"
    assert body["class_playlists"] == [
        {"class_id": str(class_id), "class_title": "Tuesday climb", "name": "Tuesday climb"}
    ]
    assert PLAYLIST not in got.text
    assert pool_id not in got.text
    assert "open.spotify.com" not in got.text
    # The other user sees none of it.
    seen = (await other.browser.get(status)).json()
    assert (seen["pool_playlist"], seen["class_playlists"]) == (None, [])


def test_failure_sentences_cover_the_new_categories_and_nothing_else() -> None:
    for category in (
        "consent_missing", "pool_unusable", "pool_missing", "pool_not_synced",
        "playlist_mismatch", "pool_too_small", "account_changed", "pool_busy",
    ):  # fmt: skip
        assert failure_sentence(category) == SONG_SENTENCES[category]
    assert "Spotify" in str(failure_sentence("invalid_grant"))
    assert "private" in str(failure_sentence("pool_unusable"))
    assert failure_sentence("upstream_unavailable") is None
    assert failure_sentence("internal") is None
    assert failure_sentence(None) is None


async def test_the_job_view_carries_the_category_and_its_plain_sentence(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    admin = songenv.admin
    failed = new_job(
        admin, rider.user_id, state="failed", error_category="pool_too_small",
        finished_at=datetime.now(UTC),
    )  # fmt: skip
    got = (await rider.browser.get(f"/api/jobs/{failed}")).json()
    assert got["error_category"] == "pool_too_small"
    assert got["error_message"] == SONG_SENTENCES["pool_too_small"]
    queued = new_job(admin, rider.user_id)
    assert (await rider.browser.get(f"/api/jobs/{queued}")).json()["error_message"] is None
