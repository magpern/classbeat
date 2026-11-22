"""The renderer's identity in a clip's cache key (ADR 0042 section 6.4): the encoder settings the
render child writes with, the ``libsndfile`` and ``soxr`` versions this process loads, and
``render.RENDERER_VERSION``. Web and worker compute the same key from it (one image, one set of
libraries); a worker whose libraries differ from the web's computes another key and stores nothing
under the web's (``render_kind`` refuses a key it does not compute itself).

``ENCODER`` mirrors ``renderchild``'s ``soundfile.SoundFile`` arguments; ``tests/test_preview_key``
reads them from the child's source, so a change there that is not made here fails.
"""

import functools
from typing import Any, Final

from classbeat.preview.key_calc import Identity
from classbeat.studio import render, render_policy

ENCODER: Final[dict[str, Any]] = {
    "format": "MP3",
    "subtype": "MPEG_LAYER_III",
    "bitrate_mode": "CONSTANT",
    "compression_level": 0.0,
    "samplerate": render_policy.RATE,
    "channels": 2,
}


@functools.cache
def _versions() -> tuple[str, str]:
    import soundfile
    import soxr

    libsoxr = str(getattr(soxr, "__libsoxr_version__", ""))
    return str(soundfile.__libsndfile_version__), f"{soxr.__version__}/{libsoxr}"


def current() -> Identity:
    libsndfile, soxr = _versions()
    return Identity(ENCODER, libsndfile, soxr, render.RENDERER_VERSION)
