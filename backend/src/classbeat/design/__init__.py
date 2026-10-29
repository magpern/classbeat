"""The ``design_draft`` job kind (ADR 0006 section 3, M3a D1): a class drafted on a playlist the
user already has in MOWL.

- ``classbeat.design.kind``: the input model, the pipeline and its registration in
  ``classbeat.jobs.KINDS`` (on import, for both the worker and the web app);
- ``classbeat.design.music``: the music step (link or attach, then the read-only analysis);
- ``classbeat.design.revision``: the stored ``draft`` revision;
- ``classbeat.design.routes``: ``GET /api/music/mowl-playlists`` (web only; not imported here, so
  the worker never loads it).

A job kind's own package (job kinds live outside ``classbeat.jobs``): it writes only through the
job's fence and opens provider connections only through ``classbeat.connections``.
"""

from classbeat.design.kind import (
    DESIGN_DRAFT,
    KIND,
    DesignDraftInput,
    MusicChoice,
    Runtime,
    configure,
    runtime_from_settings,
)

__all__ = [
    "DESIGN_DRAFT",
    "KIND",
    "DesignDraftInput",
    "MusicChoice",
    "Runtime",
    "configure",
    "runtime_from_settings",
]
