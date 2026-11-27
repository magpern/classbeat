"""A pool consent given for an older wording does not stand (#1540, ADR 0009 section 9).

Version 3 says MOWL does not analyse the songs and names spotdown, which gets the candidates' title,
artists and length. A user whose ``spotify_pool`` row says version 1 or 2 never agreed to that, so:
the status the pages read says the consent is not given (with the stored version), a pick is refused
``consent_missing`` before anything is written or sent, and giving the consent again (the same
``PUT``) brings the row to the current version on the same pool row, after which a pick is admitted.
The decide step of a pool write refuses an older consent too (``test_playlists_lifetime``,
``older_wording``). Fakes only.
"""

from typing import Any

import pytest

from classbeat.connections.spotify_consent_policy import CONSENT_VERSION
from songtools import SongEnv
from spotifytools import CONSENT, STATUS
from test_spotify_forget import POOL_PLAYLIST

pytestmark = pytest.mark.usefixtures("songenv")


def jobs_of(songenv: SongEnv, owner: Any) -> int:
    return songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


@pytest.mark.parametrize("older", [1, CONSENT_VERSION - 1])
async def test_an_older_consent_is_asked_again_and_nothing_is_written_until_it_is(
    songenv: SongEnv, older: int
) -> None:
    rider = await songenv.rider()
    owner = rider.user_id
    # An earlier pick made the pool playlist; re-consenting must keep it (and its row).
    songenv.sql(
        "UPDATE spotify_pool SET consent_version = :v, playlist_id = :p WHERE owner_id = :o",
        v=older,
        p=POOL_PLAYLIST,
        o=owner,
    )
    before = songenv.pool(owner)
    assert before.playlist_id == POOL_PLAYLIST

    status = (await rider.browser.get(STATUS)).json()
    assert (status["pool_consent"], status["consent_version"]) == (False, older)

    refused = await rider.create()
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "consent_missing")
    assert jobs_of(songenv, owner) == 0
    assert songenv.counts() == (0, 0, 0)  # no Spotify, MOWL or model request
    assert songenv.operations(owner) == []

    browser = rider.browser
    given = await browser.client.put(CONSENT, headers=await browser.headers())
    browser.client.cookies.clear()
    assert given.status_code == 204, given.text
    after = songenv.pool(owner)
    assert (after.id, after.playlist_id, after.consent_version) == (
        before.id, POOL_PLAYLIST, CONSENT_VERSION,
    )  # fmt: skip
    status = (await rider.browser.get(STATUS)).json()
    assert (status["pool_consent"], status["consent_version"]) == (True, CONSENT_VERSION)

    assert songenv.counts() == (0, 0, 0)  # giving the consent writes nothing to Spotify either
    await songenv.created(rider)
    assert jobs_of(songenv, owner) == 1


async def test_the_current_consent_is_given_and_admitted(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    assert songenv.pool(rider.user_id).consent_version == CONSENT_VERSION
    status = (await rider.browser.get(STATUS)).json()
    assert (status["pool_consent"], status["consent_version"]) == (True, CONSENT_VERSION)
    await songenv.created(rider)


async def test_a_consent_put_that_updates_no_row_is_refused(songenv: SongEnv) -> None:
    """A pool row of another Spotify account (which the connection lock rules out) is left
    untouched by the upsert, so the PUT must not answer 204 for it."""
    rider = await songenv.rider()
    owner = rider.user_id
    songenv.sql(
        "UPDATE spotify_pool SET spotify_user_id = 'another-spotify-user', consent_version = :v "
        "WHERE owner_id = :o",
        v=CONSENT_VERSION - 1,
        o=owner,
    )
    browser = rider.browser
    refused = await browser.client.put(CONSENT, headers=await browser.headers())
    browser.client.cookies.clear()
    assert (refused.status_code, refused.json()) == (409, {"detail": {"reason": "not_connected"}})
    row = songenv.pool(owner)
    assert (row.spotify_user_id, row.consent_version) == (
        "another-spotify-user", CONSENT_VERSION - 1,
    )  # fmt: skip
    status = (await browser.get(STATUS)).json()
    assert status["pool_consent"] is False
    kinds = songenv.sql(
        "SELECT kind FROM auth_events WHERE user_id = :o AND kind = 'spotify_pool_consent'",
        o=owner,
    ).all()
    assert kinds == []  # no consent event for a consent that was not recorded
