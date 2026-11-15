"""The SwedeBeat studio's song stage (ADR 0036 sections 2 and 3), behind
``CLASSBEAT_SWEDEBEAT_STUDIO``. A studio class needs no MOWL connection.

- ``config_policy``, ``config``: the setting and what both processes ``configure`` at start;
- ``evidence_policy``, ``candidate_policy``, ``swap_policy``, ``length_policy``,
  ``song_list_model``: the rules, without I/O;
- ``match``: one gateway match of the candidates and the evidence retry;
- ``stages``, ``pick``, ``edit``: the pick, the swaps, undo and another;
- ``song_list``: the studio ``song_list`` revision; ``admit``, ``inputs``, ``kind``: the
  ``studio_songs`` job kind (registered in ``classbeat.jobs.KINDS`` when ``kind`` is imported);
- ``routes``: the user's actions on a list (undo, another, swap anyway) and its view.

This package's ``__init__`` imports nothing: ``classbeat.config_features`` reads
``config_policy`` while ``classbeat.config`` is still loading.
"""
