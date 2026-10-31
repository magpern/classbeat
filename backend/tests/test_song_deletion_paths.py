"""The deletion paths of song picking are executable (ADR 0008 section 8; issue CL1, #183).

A real picked song list (``songenv``) plus a planted final playlist, final and pool operation rows
and a MOWL link; then each path - class delete, consent revoke, Disconnect and account deletion -
removes exactly what the ADR's table says, leaves the other user's rows alone, and makes **no
request** to Spotify, MOWL or a model (counted at the fakes). Playlists in the user's Spotify are
never touched: the fake still holds them.
"""

from typing import Any
from uuid import UUID

import pytest

from classbeat.db import RETENTION
from jobtools import digest
from songtools import Rider, SongEnv
from spotifyfake import USER_A
from test_classes_api import insert

pytestmark = pytest.mark.usefixtures("songenv")

FINAL = "0vvXsWCC9xrXsKd4FyS8kM"
STATUS = "/api/connections/spotify"
CONSENT = f"{STATUS}/pool-consent"


class Planted:
    def __init__(self, env: SongEnv, rider: Rider, job_id: UUID, list_rev: UUID) -> None:
        self.env, self.rider = env, rider
        self.owner = rider.user_id
        self.job_id, self.list_rev = job_id, list_rev
        self.class_id: UUID = env.sql(
            "SELECT class_id FROM jobs WHERE id = :j", j=job_id
        ).scalar_one()

    def count(self, table: str, where: str = "TRUE", **params: Any) -> int:
        statement = f"SELECT count(*) FROM {table} WHERE owner_id = :o AND {where}"  # noqa: S608
        return self.env.sql(statement, o=self.owner, **params).scalar_one()

    def ops(self, *, pool: bool) -> int:
        return self.count(
            "external_operations",
            "kind IN ('spotify_create_playlist', 'spotify_replace_items') AND class_id IS "
            + ("NULL" if pool else "NOT NULL"),
        )


async def planted(env: SongEnv) -> Planted:
    rider = await env.rider()
    job_id, list_rev, _ = await env.waiting(rider)
    # The job ended without approval, as a user's Ask for changes would leave it.
    assert (await rider.browser.post(f"/api/jobs/{job_id}/cancel")).status_code == 200
    found = Planted(env, rider, job_id, list_rev)
    admin = env.admin
    final_create = insert(
        admin, "external_operations", owner_id=rider.user_id, class_id=found.class_id,
        kind="spotify_create_playlist", idempotency_key=f"k{job_id}", request_hash=digest("c"),
        lease_version=1, state="succeeded", external_id=FINAL,
    )  # fmt: skip
    insert(
        admin, "external_operations", owner_id=rider.user_id, class_id=found.class_id,
        kind="spotify_replace_items", idempotency_key=f"f{job_id}", request_hash=digest("f"),
        lease_version=1, state="pending",
    )  # fmt: skip
    insert(
        admin, "class_playlists", owner_id=rider.user_id, class_id=found.class_id,
        revision_id=list_rev, playlist_id=FINAL, spotify_user_id=USER_A, name="Tuesday climb",
        state="verified", content_hash=digest("final"), track_count=8,
        create_operation_id=final_create,
    )  # fmt: skip
    # A finished job leaves nothing to wait for; the pick already wrote the pool rows.
    assert env.pool(rider.user_id) is not None
    assert found.ops(pool=True) > 0
    assert found.count("class_revisions", "kind = 'song_list'") == 1
    return found


async def call(rider: Rider, method: str, path: str) -> Any:
    headers = await rider.browser.headers()
    response = await rider.browser.client.request(method, path, headers=headers)
    rider.browser.client.cookies.clear()
    return response


def spotify_rows(found: Planted) -> dict[str, int]:
    return {
        "pool": found.count("spotify_pool"),
        "pool_ops": found.ops(pool=True),
        "final_ops": found.ops(pool=False),
        "live_playlists": found.count("class_playlists", "detached_at IS NULL"),
        "playlists": found.count("class_playlists"),
        "song_lists": found.count("class_revisions", "kind = 'song_list'"),
        "connection": found.count("connections", "provider = 'spotify'"),
    }


async def test_class_delete_removes_the_picked_songs_and_playlist_rows_and_asks_nobody(
    songenv: SongEnv,
) -> None:
    mine = await planted(songenv)
    other = await planted(songenv)
    other_before = spotify_rows(other)
    before = songenv.counts()
    before_rows = spotify_rows(mine)
    answer = await call(mine.rider, "DELETE", f"/api/classes/{mine.class_id}")
    assert answer.status_code == 200, answer.text
    assert [p["name"] for p in answer.json()["spotify_playlists_left"]] == ["Tuesday climb"]
    assert songenv.counts() == before
    now = spotify_rows(mine)
    assert (now["song_lists"], now["playlists"], now["final_ops"]) == (0, 0, 0)
    # What belongs to the account, not the class, stays until its own path.
    assert (now["pool"], now["connection"]) == (1, 1)
    assert now["pool_ops"] == before_rows["pool_ops"] > 0
    assert spotify_rows(other) == other_before


async def test_a_consent_revoke_removes_the_pool_rows_and_keeps_the_class_ones(
    songenv: SongEnv,
) -> None:
    mine = await planted(songenv)
    other = await planted(songenv)
    other_before = spotify_rows(other)
    before_rows = spotify_rows(mine)
    before = songenv.counts()
    assert (await call(mine.rider, "DELETE", CONSENT)).status_code == 204
    now = spotify_rows(mine)
    assert (now["pool"], now["pool_ops"]) == (0, 0)
    assert {k: v for k, v in now.items() if k not in ("pool", "pool_ops")} == {
        k: v for k, v in before_rows.items() if k not in ("pool", "pool_ops")
    }
    assert songenv.counts() == before
    assert spotify_rows(other) == other_before


async def test_disconnect_leaves_no_live_spotify_row_and_the_detached_go_with_the_class(
    songenv: SongEnv,
) -> None:
    mine = await planted(songenv)
    other = await planted(songenv)
    other_before = spotify_rows(other)
    before = songenv.counts()
    assert (await call(mine.rider, "DELETE", STATUS)).status_code == 204
    now = spotify_rows(mine)
    assert (now["pool"], now["pool_ops"], now["final_ops"], now["connection"]) == (0, 0, 0, 0)
    assert (now["live_playlists"], now["playlists"]) == (0, 1)  # detached history only
    assert songenv.counts() == before
    assert spotify_rows(other) == other_before
    # The class then takes the detached row and the song list with it, still asking nobody.
    answer = await call(mine.rider, "DELETE", f"/api/classes/{mine.class_id}")
    assert answer.status_code == 200, answer.text
    assert [(p["name"], p["detached"]) for p in answer.json()["spotify_playlists_left"]] == [
        ("Tuesday climb", True)
    ]
    assert songenv.counts() == before
    assert (spotify_rows(mine)["playlists"], spotify_rows(mine)["song_lists"]) == (0, 0)


async def test_account_deletion_leaves_nothing_in_any_registered_table(songenv: SongEnv) -> None:
    mine = await planted(songenv)
    other = await planted(songenv)
    other_before = spotify_rows(other)
    before = songenv.counts()
    email = songenv.sql("SELECT email FROM users WHERE id = :u", u=mine.owner).scalar_one()
    answer = await mine.rider.browser.post("/api/account/delete", {"confirm_email": email})
    assert answer.status_code == 204, answer.text
    assert songenv.counts() == before
    for table, entry in RETENTION.items():
        if "account" not in entry.deleted_with:
            continue
        if table == "agent_runs":  # token counts only; gone with the user row's cascade too
            continue
        assert mine.count(table) == 0, table
    assert mine.count("agent_runs") == 0
    assert spotify_rows(other) == other_before
