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

pytestmark = pytest.mark.usefixtures("songenv")


def jobs_of(songenv: SongEnv, owner: Any) -> int:
    return songenv.sql("SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner).scalar_one()


@pytest.mark.parametrize("older", [1, CONSENT_VERSION - 1])
async def test_an_older_consent_is_asked_again_and_nothing_is_written_until_it_is(
    songenv: SongEnv, older: int
) -> None:
    rider = await songenv.rider()
    owner = rider.user_id
    before = songenv.pool(owner)
    songenv.sql(
        "UPDATE spotify_pool SET consent_version = :v WHERE owner_id = :o", v=older, o=owner
    )

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
        before.id, before.playlist_id, CONSENT_VERSION,
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
