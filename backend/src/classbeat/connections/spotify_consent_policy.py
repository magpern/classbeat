"""The standing pool consent's wording and version (ADR 0009 section 9). Pure: no I/O.

``spotify_pool.consent_version`` records which wording a user agreed to. A consent stands only for
``CONSENT_VERSION`` (``consent_current``): every reader that lets a Spotify write go ahead (the
admission checks, ``pool_consent_in``, and the pool write's decide step, ``playlists.pool``) and the
status the pages read (``spotify_status``) apply it, so an older consent reads as no consent and the
user is asked again before any write.

- Version 1: classbeat asked Spotify for a private playlist (roadmap Amendment 27).
- Version 2 (roadmap Amendment 30, #1287): the playlist is created public; a version-1 consent
  stood.
- Version 3 (#1540): MOWL does not analyse the songs. classbeat links the playlist in MOWL so the
  class can be timed to it, and spotdown, a separate music-analysis service, gets the candidates'
  title, artists and length and analyses the same recordings from a source other than Spotify
  (ADR 0040). That recipient and purpose were never in versions 1 and 2, so neither stands.
"""

from typing import Final

CONSENT_VERSION: Final = 3
CONSENT_TEXT: Final = (
    "classbeat manages one working playlist in your Spotify account, named classbeat working "
    "copy, to hold the song candidates of a pick. It is public, so MOWL can find it: it can show "
    "on your Spotify profile and in search, and anyone with its link can open it. It is "
    "overwritten on each pick. classbeat links it in MOWL so your class can be timed to it. The "
    "songs are analysed by spotdown, the music-analysis service classbeat uses: classbeat sends "
    "it the title, artists and length of each candidate, and spotdown analyses the same recording "
    "from a source other than Spotify."
)


def consent_current(version: int | None) -> bool:
    """Whether a stored consent of ``version`` (None: none stored) covers the current wording."""
    return version is not None and version >= CONSENT_VERSION
