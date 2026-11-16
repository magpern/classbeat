"""The final Spotify playlist of a studio class (ADR 0036 section 6, ADR 0008 sections 2 and 4,
SR C7): a fresh, private playlist created and filled with the approved draft's tracks **in the
mix's order**, through ``classbeat.playlists.final`` (the create, its ``class_playlists`` row for
the approved studio ``song_list`` revision, the absolute fill, read back). Its keys are the
approval's (``out:...:spotify_playlist:<n>:spotify_create`` and ``...:spotify_fill``), so a later
job for the same approval finds the earlier rows and makes no second playlist. It needs no
SwedeBeat write, no render and no MOWL.
"""

from classbeat.jobs import JobFailure
from classbeat.playlists import fill_final_playlist, open_spotify
from classbeat.playlists.final import create_final_playlist
from classbeat.swedebeat_studio.outputs_attempt import mark
from classbeat.swedebeat_studio.outputs_job import Producer
from classbeat.swedebeat_studio.outputs_rows import OutputRow, step_keys


def track_ids(p: Producer) -> list[str]:
    """The approved list's Spotify ids in the mix's order."""
    by_reference = {
        s.swedebeat.reference: s.spotify_id
        for s in p.draft.songs.content.songs
        if s.swedebeat is not None and not s.gap
    }
    if any(r not in by_reference for r in p.references):
        raise JobFailure("internal")
    return [by_reference[r] for r in p.references]


async def spotify_playlist(p: Producer, row: OutputRow) -> None:
    connection_id = p.given.spotify_connection_id
    name = row.detail.get("name")
    if connection_id is None or not isinstance(name, str):
        raise JobFailure("internal")
    opened = await open_spotify(p.run, p.keyring, connection_id)
    keys = step_keys(row)
    made = await create_final_playlist(
        p.run,
        p.keyring,
        opened,
        class_id=p.class_id,
        revision_id=p.draft.music.song_list_id,
        content_hash=p.draft.songs.content_hash,
        name=name,
        key=keys["spotify_create"],
    )
    await mark(p, row, "running", playlist_id=made.playlist_id)
    await fill_final_playlist(
        p.run,
        p.keyring,
        opened,
        class_id=p.class_id,
        class_playlist_id=made.id,
        track_ids=track_ids(p),
        key=keys["spotify_fill"],
    )
    await mark(p, row, "done")
