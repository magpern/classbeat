"""Phase 1 of a class on the user's own Spotify playlist (roadmap Amendment 26, ADR 0008 amendment
of 2026-11-16): a ``design_with_songs`` job whose input names ``source``.

The owner's decision: a class always gets a **new** private playlist in class order, also when the
user chose a playlist they already have. So this phase replaces a pick's pool search, pool write
and choice with the playlist itself, and hands over to the pick's own wait and phase 2:

1. the MOWL connection must be the job's and the Spotify connection must open
   (``open_spotify``: ``not_connected`` otherwise), before any call;
2. ``pool_analyse``: the design music step on **the user's playlist** (``load_music``, as
   ``design_draft`` reads it: the stored MOWL link or the attach, then read-only analysis calls);
   **no Spotify write and no pool**: the user's playlist is only read, never modified;
3. the ``song_list`` revision from that analysis in the playlist's order
   (``playlist_list_policy.chosen_list``; ``invalid_input`` when no track is usable or there are
   more than 40), for the connected Spotify account and both connection generations;
4. ``pick.await_approval``: the same final fenced transaction (Spotify held, the MOWL link still
   current, the revision and its hash) and the same ``waiting_for_approval``.

No model runs here (the song stages' cost is 0). After Approve, phase 2 (``classbeat.songs.phase2``)
creates the final playlist by id (private, not collaborative), fills it in the approved order,
verifies it, attaches it to MOWL and designs on it: the user's playlist is never the class's
music any more.
"""

import logging
from typing import Any, Final

from classbeat.crypto.keks import KeyRing
from classbeat.design.kind import SpotifyMusicChoice
from classbeat.design.music import load_music
from classbeat.jobs import AwaitApproval, JobFailure, JobRun
from classbeat.playlists import open_spotify
from classbeat.songs import playlist_list_policy, song_list
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.pick import await_approval, generations

logger = logging.getLogger(__name__)

ORIGIN: Final = "design_with_songs"
MUSIC_FROM: Final = "spotify_playlist"


def _provenance(analysis_map: dict[str, Any]) -> dict[str, Any]:
    """What made the list: no model run, no cost, the user's playlist."""
    return {
        "origin": ORIGIN,
        "music_from": MUSIC_FROM,
        "agent_run_ids": [],
        "cost_micros": 0,
        "map_version": analysis_map.get("version"),
    }


async def run_source_phase_one(
    run: JobRun,
    given: DesignWithSongsInput,
    source: SpotifyMusicChoice,
    *,
    keyring: KeyRing,
) -> AwaitApproval:
    if run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    spotify = await open_spotify(run, keyring, given.spotify_connection_id)
    account = spotify.account
    if account is None:  # open_spotify refuses it already
        raise JobFailure("not_connected")
    await run.stage("pool_analyse")
    step = await load_music(
        run, keyring, source.spotify_playlist_id, connection_id=given.mowl_connection_id
    )
    try:
        analysis_map = step.data.analysis["map"]
        chosen = playlist_list_policy.chosen_list(
            analysis_map, target_sec=given.brief.length_min * 60
        )
        if chosen is None:
            raise JobFailure("invalid_input")
        built = song_list.build(
            chosen,
            account=account,
            provenance=_provenance(analysis_map),
            generations=generations(spotify, step),
        )
    except BaseException:
        step.close()
        raise
    logger.info(
        "song list from the user's playlist",
        extra={
            "job_id": str(run.lease.job_id),
            "songs": len(built.content.songs),
            "omitted_no_bpm": built.content.omitted_no_bpm,
        },
    )
    return await_approval(spotify, step, built)
