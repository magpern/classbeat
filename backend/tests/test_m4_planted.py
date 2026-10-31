"""M4 done-when, the planted-value test over the whole flow (ADR 0008 sections 2, 3 and 12, ADR 0006
section 7; issue SEC1, #185).

``test_agent_pick_boundary.py`` plants values in what the two song stages read. This one runs the
whole ``design_with_songs`` flow (the pick, its pool write with a lost answer so the reconciler
lists the user's playlists, Approve, the final playlist and the design) and plants a value in every
place the flow touches: the user's email, the Spotify display name, a playlist of the user's own
with a planted name, the access and refresh tokens, the Client ID, the Spotify user id, the MOWL
token. It then collects the values the flow made (playlist ids, the MOWL playlist ids, the
operations' ``ref``, the track, album and artist ids, the album image URLs) and checks that none
reaches what must never hold them:

- everything sent to a model, in both phases (titles and artists do appear);
- the job's events (what the progress stream shows);
- every log record, raw and as the production formatter writes it;
- the jobs API's answers (the job and its revision) and the class's revisions list.

The final playlist's id is allowed on the owner's class page only (``music_source``); the pool's is
never shown. Fakes only: no network, no real account.
"""

import io
import json
import logging
import re
from collections.abc import Iterator

import pytest

from classbeat.log import JsonFormatter
from classbeat.operations.kinds import ref_of
from optools import TOKEN_A
from pickfake import everything_sent
from songtools import CHOSEN, POOL, SongEnv, design_script
from spotifycore import album_images
from spotifyfake import CLIENT_A, USER_A

pytestmark = pytest.mark.usefixtures("songenv")

PLANTED_NAME = "Planted Display Name 4471"
PLANTED_PLAYLIST = "Planted Private Playlist 9302"


class Everything(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.out = io.StringIO()
        self.fmt = JsonFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        self.out.write(self.fmt.format(record) + repr(vars(record)) + record.getMessage() + "\n")


@pytest.fixture
def logs() -> Iterator[Everything]:
    handler = Everything()
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


def leaked(values: list[str], text: str) -> list[str]:
    """The values found in ``text``. A value of digits only (a fake MOWL id such as ``1001``)
    counts only as a number of its own: not inside a longer number or a decimal, such as the
    ``created`` timestamp of a log record."""
    return [v for v in values if v and _found(v, text)]


def _found(value: str, text: str) -> bool:
    if not value.isdigit():
        return value in text
    return re.search(rf"(?<!\d)(?<!\d\.){value}(?!\d)(?!\.\d)", text) is not None


async def test_no_planted_or_made_value_leaves_its_place_over_the_whole_flow(
    songenv: SongEnv, logs: Everything
) -> None:
    songenv.fake.users[USER_A] = PLANTED_NAME
    rider = await songenv.rider()
    own = songenv.fake.add_playlist(USER_A, PLANTED_PLAYLIST, [f"spotify:track:{POOL[0]}"])
    email = songenv.sql("SELECT email FROM users WHERE id = :u", u=rider.user_id).scalar_one()
    tokens = [t for t, g in songenv.fake.access.items() if g.user == USER_A]
    tokens += [t for t, g in songenv.fake.refresh.items() if g.user == USER_A]
    assert len(tokens) == 2

    # The pick, its pool create answered with nothing (found again by the listing), the wait.
    songenv.fake.inject("drop", method="POST", path="/v1/me/playlists")
    job_id, revision_id, digest = await songenv.waiting(rider)
    answer = await rider.approve(job_id, revision_id, digest)
    assert answer.status_code == 200, answer.text
    songenv.script = design_script()
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    listings = songenv.fake.requests("api.spotify.com", "/v1/me/playlists")
    assert any(s.method == "GET" for s in listings)  # the reconciler read the planted playlist

    pool_id = songenv.pool(rider.user_id).playlist_id
    [final] = songenv.class_playlists(rider.user_id)
    operations = songenv.operations(rider.user_id)
    tracks = [songenv.fake.catalogue[t] for t in POOL]
    made = [pool_id, final.playlist_id, own.id, str(final.mowl_playlist_id)]
    made += [ref_of(o.id) for o in operations]
    made += [str(o.external_id) for o in operations if o.kind.startswith("mowl_")]
    made += [str(rider.spotify_connection_id), str(rider.mowl_connection_id)]
    made += [t["id"] for t in tracks] + [t["album_id"] for t in tracks]
    made += [t["artist_id"] for t in tracks]
    made += [i["url"] for t in tracks for i in album_images(t["album_id"])]
    secrets = [*tokens, TOKEN_A, CLIENT_A, USER_A, PLANTED_NAME, PLANTED_PLAYLIST, email]
    shapes = ["spotify:track", "open.spotify.com", "api.spotify.com", "Bearer", "access_token"]
    shapes += ["refresh_token", "scdn.co", "classbeat ref"]
    everything = [*secrets, *made, *shapes]

    # 1. Model input, both phases.
    sent = "\n".join(everything_sent(p) for p in songenv.providers)
    assert len(songenv.providers) == 2  # one per run: the pick (both stages), the design
    assert leaked(everything, sent) == []
    for track in tracks:
        if track["id"] in CHOSEN:
            assert track["name"] in sent
    # 2. The job's events.
    events = songenv.sql(
        "SELECT kind, payload FROM job_events WHERE job_id = :j ORDER BY seq", j=job_id
    ).all()
    assert leaked(everything, json.dumps([[e.kind, e.payload] for e in events])) == []
    # 3. Every log record: no planted or made value, and no song's title or artist either.
    names = [t["name"] for t in tracks] + [t["artist"] for t in tracks]
    assert leaked([*everything, *names], logs.out.getvalue()) == []
    # 4. The API: the job, its revision, the class's revisions; the class page shows only the
    # owner's own final playlist.
    answers = [
        (await rider.browser.get(f"/api/jobs/{job_id}")).text,
        (await rider.browser.get(f"/api/jobs/{job_id}/revision")).text,
        (await rider.browser.get(f"/api/classes/{row.class_id}/revisions")).text,
    ]
    assert leaked([*secrets, pool_id, final.playlist_id, *shapes], "\n".join(answers)) == []
    page = (await rider.browser.get(f"/api/classes/{row.class_id}")).text
    # Its link is built by classbeat from the id, never taken from Spotify's data.
    link = f"https://open.spotify.com/playlist/{final.playlist_id}"
    assert link in page
    rest = page.replace(link, "")
    assert leaked([*secrets, pool_id, own.id, *shapes], rest) == []
