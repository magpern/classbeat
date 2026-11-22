"""The SwedeBeat audition's clip service (ADR 0042 sections 6.3 to 6.5, CP-R2 #1394).

- ``session_policy``, ``clip_window_calc`` and ``key_calc``: the pure rules (the session's
  lifetime and delivery caps, the clip's window in whole milliseconds, the cache key);
- ``identity``: the renderer's identity in the key (encoder settings, library versions);
- ``storage``: the private preview volume (per-owner paths, atomic writes, removal);
- ``plan_source``: a class's current studio draft as the reference-keyed render plan;
- ``render_kind``: the ``preview_render`` job; ``sources``: its per-owner source-track cache;
- ``purge``: idle, size-cap and orphan purges, run by the hourly purge.

The web side (sessions and the clip endpoint) is ``classbeat.classes.preview_*``.
"""
