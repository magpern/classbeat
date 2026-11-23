"""The music step of ``design_draft``, ``revise_draft`` and ``refit_draft`` (split out of
``classbeat.design.kind``): the user's Spotify playlist in MOWL (``music.load_music``) or their
SwedeBeat mix (``swedebeat_music.swedebeat_step``), then, on a playlist, spotdown's sections for
every track, then the class's beat-one corrections for that music (ADR 0007 amendment).

``prefer`` (a revise or refit: the fingerprint of the map its base was designed on) makes a mix's
cached row with that map the hit (``mix_cache.cached``, #1304), so a
later job reads the analysis the class was designed on; on a playlist it is the music a carried
record must still be (``songs.spotdown_carry``).

**A playlist is always timed on spotdown** (ADR 0040 amendment "spotdown is the only analysis
path", item 3; #1407): ``carry`` (a revise or refit: the base's ``music.spotdown``, which every
Spotify base has, ``design.spotdown_base``) reads the base's tracks again
(``songs.spotdown_carry``); without one (``design_draft``) stages A, B and D run on every track
(``songs.draft_spotdown``), as they do for a revise (``fresh_on_change``) of such a design whose
playlist changed since (its own record no longer fits; a song list's base is ``music_changed``).
No path gives back MOWL's sections. The second value is where the
step's spotdown record lives, for the revision (``revision_spotdown_model.sections_of``): the
carry, the design's own record, or ``None`` on a mix (its own spotdown path, ``mix_spotdown``).
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
    *,
    fresh_on_change: bool = False,
) -> tuple[MusicStep, SpotdownSections | None]:
    """The music step of the class's source and its spotdown origin (module docstring; the caller
    checked a mix's flag with ``mixadmit.still_on``). ``fresh_on_change`` (a revise): a playlist
    design's base whose music changed since gets the design's stages again, not
    ``music_changed``."""
    if isinstance(music, SwedebeatMusicChoice):
        step = await swedebeat_step(
            run, runtime.keyring, music.mix_id, connection_id=connection_id, prefer=prefer
        )
    else:
        step = await load_music(
            run, runtime.keyring, music.spotify_playlist_id, connection_id=connection_id
        )
    try:
        timing: SpotdownSections | None = None
        if not isinstance(music, SwedebeatMusicChoice):
            # Imported here: ``classbeat.songs`` imports this package.
            from classbeat.songs.draft_spotdown import drafted_music
            from classbeat.songs.spotdown_carry import carried_music

            changed = carry is not None and carry.record is not None and step.fingerprint != prefer
            if carry is None or (changed and fresh_on_change):  # stages A, B and D (#1407)
                step, timing = await drafted_music(run, step)
            else:
                step, timing = await carried_music(run, step, carry, prefer), carry
        # the class's beat-one corrections for this music (ADR 0007 amendment)
        return await corrections.attach(run, step), timing
    except BaseException:
        step.close()
        raise
