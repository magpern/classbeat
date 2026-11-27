"""A pick queued under an older pool consent contacts no one (#1540, ADR 0009 section 9).

The consent of version 3 names spotdown, so a job admitted under it whose consent is then of an
older wording (a pick queued before the deploy of the new wording) must fail ``consent_missing``
at its start: before the pool model call, any Spotify search, a pinned song's lookup and
spotdown's match. ``write_pool``'s decide step keeps its own check (``test_playlists_lifetime``).
Fakes only.
"""

from uuid import UUID

import pytest

from classbeat.connections.spotify_consent_policy import CONSENT_VERSION
from composertools import ComposerEnv, fill_script
from songtools import SongEnv
from spotdownfake import FakeSpotdown

pytestmark = pytest.mark.usefixtures("jenv")


def older_wording(songenv: SongEnv, owner: UUID) -> None:
    changed = songenv.sql(
        "UPDATE spotify_pool SET consent_version = :v WHERE owner_id = :o RETURNING id",
        v=CONSENT_VERSION - 1,
        o=owner,
    )
    assert changed.first() is not None


async def test_a_queued_pick_under_an_older_consent_fails_before_any_call(
    songenv: SongEnv, spotdown: FakeSpotdown
) -> None:
    rider = await songenv.rider()
    job_id = await songenv.created(rider)
    older_wording(songenv, rider.user_id)

    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("failed", "consent_missing")
    assert songenv.counts() == (0, 0, 0)  # no Spotify, MOWL or model request
    assert spotdown.seen == []  # spotdown never got a title
    assert songenv.operations(rider.user_id) == []


async def test_a_queued_composer_fill_under_an_older_consent_fails_before_any_call(
    cenv: ComposerEnv, spotdown: FakeSpotdown
) -> None:
    songenv = cenv.songenv
    rider = await songenv.rider()
    # A pinned song: the fill would look it up on Spotify before its pool's model call.
    class_id, _ = await cenv.sketched(
        rider, pinned=[{"title": "Ride Song 3 alpha", "at": "the climb"}]
    )
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
    older_wording(songenv, rider.user_id)

    songenv.script = fill_script()
    row = await songenv.run(UUID(made.json()["job_id"]))
    assert (row.state, row.error_category) == ("failed", "consent_missing")
    assert songenv.counts() == (0, 0, 0)  # no Spotify, MOWL or model request
    assert spotdown.seen == []
    assert songenv.operations(rider.user_id) == []
