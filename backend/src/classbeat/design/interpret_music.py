"""The music names an ``interpret_request`` shows the interpreter (ADR 0029 section 4).

Read in the worker with the job's own MOWL connection: the user's Spotify playlists as MOWL lists
them (``mowl.api.spotify_playlists``) and, only when the job was admitted with Swedebeat on for the
user (``swedebeat_mixes``) and the worker's switch is on, their Swedebeat mixes. A checkpoint
precedes each read; nothing is written. An entry whose id has not the shape of an id is left out,
a name is cleaned (``interpret_input.music_list`` cleans and cuts it again before it is sent), and
at most ``MAX_NAMES`` are kept, playlists first, in MOWL's order: the interpreter's answer is a
position in that list. Ids stay here and in the resolved music; a model never sees one.
"""

import re
from typing import Any, Final, cast
from uuid import UUID

from mowl import api as mowl_api

from classbeat.agent.interpreted import MAX_NAMES
from classbeat.agent.schemas import clean_text
from classbeat.connections import call_opened, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.design.interpret_music_policy import MusicOption
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations.swedebeat import parse_mixes

SPOTIFY_ID: Final = re.compile(r"[0-9A-Za-z]{22}")
NAME_CHARS: Final = 200


def playlist_options(answer: object) -> list[MusicOption]:
    """The playlists of MOWL's ``v1/Spotify/Playlists`` answer (a plain list)."""
    out: list[MusicOption] = []
    entries: list[Any] = answer if isinstance(answer, list) else []  # pyright: ignore[reportUnknownVariableType]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fields = cast(dict[str, Any], entry)
        ref, name = fields.get("ID"), fields.get("Name")
        if isinstance(ref, str) and SPOTIFY_ID.fullmatch(ref):
            label = clean_text(name if isinstance(name, str) else "")[:NAME_CHARS]
            out.append(MusicOption("playlist", label, ref))
    return out


async def music_options(
    run: JobRun, keyring: KeyRing, connection_id: UUID, *, mixes: bool
) -> tuple[MusicOption, ...]:
    """The options, ``not_connected`` unless the job's connection is the context's (before any
    call). A MOWL failure fails the attempt with its category (``classbeat.jobs.errors``)."""
    if run.ctx.connection("mowl") != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    await run.checkpoint()
    listed = await call_opened(run.factory, run.ctx, opened, mowl_api.spotify_playlists)
    options = playlist_options(listed)
    if mixes and len(options) < MAX_NAMES:
        await run.checkpoint()
        answer = await call_opened(run.factory, run.ctx, opened, mowl_api.swedebeat_mixes)
        options += [MusicOption("mix", m.name, m.mix_id) for m in parse_mixes(answer)]
    return tuple(options[:MAX_NAMES])
