"""The music step of ``design_draft``, ``revise_draft`` and ``refit_draft`` (split out of
``classbeat.design.kind``): the user's Spotify playlist in MOWL (``music.load_music``) or their
SwedeBeat mix (``swedebeat_music.swedebeat_step``), then the class's beat-one corrections for that
music (ADR 0007 amendment).

``prefer`` (a revise or refit: the fingerprint of the map its base was designed on) makes a mix's
cached row with that map the hit, whichever analyser made it (``mix_cache.cached``, #1304), so a
later job reads the analysis the class was designed on; a playlist ignores it.

``carry`` (a revise or refit of a draft that had tracks on spotdown's sections, ADR 0040 SD-P6
stage E: ``design.spotdown_base``) reads those tracks again on a playlist
(``songs.spotdown_carry``), before the corrections; ``None`` is the step as loaded (a mix, or
``design_draft``).
"""

from typing import TYPE_CHECKING
from uuid import UUID

from classbeat.design import corrections
from classbeat.design.music import MusicStep, load_music
from classbeat.design.music_choice import SpotifyMusicChoice, SwedebeatMusicChoice
from classbeat.design.revision_spotdown_model import SpotdownSections
from classbeat.design.swedebeat_music import swedebeat_step
from classbeat.jobs import JobRun

if TYPE_CHECKING:
    from classbeat.design.kind import Runtime


async def music_step(
    run: JobRun,
    runtime: "Runtime",
    music: SpotifyMusicChoice | SwedebeatMusicChoice,
    connection_id: UUID,
    prefer: str | None = None,
    carry: SpotdownSections | None = None,
) -> MusicStep:
    """The music step of the class's source (the caller checked a mix's flag with
    ``mixadmit.still_on``)."""
    if isinstance(music, SwedebeatMusicChoice):
        step = await swedebeat_step(
            run, runtime.keyring, music.mix_id, connection_id=connection_id, prefer=prefer
        )
    else:
        step = await load_music(
            run, runtime.keyring, music.spotify_playlist_id, connection_id=connection_id
        )
    try:
        if carry is not None and not isinstance(music, SwedebeatMusicChoice):
            # Imported here: ``classbeat.songs`` imports this package.
            from classbeat.songs.spotdown_carry import carried_music

            step = await carried_music(run, step, carry)
        # the class's beat-one corrections for this music (ADR 0007 amendment)
        return await corrections.attach(run, step)
    except BaseException:
        step.close()
        raise
