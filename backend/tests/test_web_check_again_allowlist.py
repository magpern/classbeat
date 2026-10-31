"""SR-03 (#353): the web process's urllib allowlist (``egress.WEB_URLLIB_ALLOWLIST``) must not
break the user's Check again. These run the web route of Check again with the web opener over the
same fake network the worker-side tests use: a Spotify playlist create (reads Spotify through the
library) and a MOWL save (reads MOWL)."""

import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

from classbeat import egress
from classbeat.egress import WEB_URLLIB_ALLOWLIST, build_allowlist_opener
from jobtools import new_class
from optools import TOKEN_A
from playlisttools import PlayEnv
from savetools import FakeMowlPrograms, SaveEnv

OPS = "/api/external-operations"
MINE = "/v1/me/playlists"
CREATE = "spotify_create_playlist"


@pytest.fixture
def web_only(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Swaps the fake network's opener for the web's allowlist over the same fake handler."""

    def swap() -> None:
        current = urllib.request._opener  # pyright: ignore[reportAttributeAccessIssue]
        [network] = [h for h in current.handlers if hasattr(h, "https_open")]
        web = build_allowlist_opener(WEB_URLLIB_ALLOWLIST, https_handler=network)
        monkeypatch.setattr(urllib.request, "_opener", web)
        assert egress.WEB_URLLIB_ALLOWLIST.hosts == {
            "api.mowl.com",
            "api.spotify.com",
            "www.swedebeat.se",
        }

    return swap


async def test_check_again_of_an_uncertain_spotify_create_works_under_the_web_opener(
    penv: PlayEnv, web_only: Callable[[], None]
) -> None:
    owner = await penv.user()
    class_id = new_class(penv.admin, owner)
    revision_id, content_hash = penv.song_list(owner, class_id)
    job_id = penv.job(
        owner, class_id, action="final_create", revision_id=str(revision_id),
        content_hash=content_hash.hex(),
    )  # fmt: skip
    penv.fake.inject("drop", method="POST", path=MINE)
    penv.fake.inject("status", method="GET", path=MINE, status=500)
    assert (await penv.run(job_id)).error_category == "uncertain_write"
    [row] = penv.operations(owner)
    actor = await penv.spot.browser(owner)
    web_only()  # from here on, only the web's hosts
    checked = await actor.post(f"{OPS}/{row.id}/check-again")
    assert (checked.status_code, checked.json()["state"]) == (200, "succeeded")
    [made] = penv.class_playlists(owner)
    assert (made.playlist_id, made.create_operation_id) == (next(iter(penv.fake.playlists)), row.id)


async def test_check_again_of_an_uncertain_mowl_save_works_under_the_web_opener(
    senv: SaveEnv, fake_programs: FakeMowlPrograms, make_actor: Any, web_only: Callable[[], None]
) -> None:
    from test_save_workout_writes import WAIT, crash_mid_call

    saved = await senv.saved_class()
    job_id = senv.job(saved)
    await crash_mid_call(senv, job_id, 4)
    senv.ready(job_id)
    await senv.run_next()
    [op] = senv.operations(saved.owner)
    assert op.state == "uncertain"
    senv.age(op.id, WAIT + 1)
    actor = await make_actor(saved.owner)
    web_only()
    answer = await actor.post(f"{OPS}/{op.id}/check-again")
    assert answer.status_code == 200, answer.text
    [op] = senv.operations(saved.owner)
    [program] = fake_programs.owned(TOKEN_A)
    assert (op.state, op.resolved_by, op.external_id) == ("succeeded", "user", str(program.id))
