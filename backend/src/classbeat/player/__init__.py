"""classbeat-player: the signed events classbeat sends to the player (ADR 0034 section 2).

Pure rules: ``base_url_policy`` (``CLASSBEAT_PLAYER_BASE_URL``) and ``event_policy`` (the logout
token's claims, the retry schedule and the error categories). Side effects: ``schema`` and
``outbox`` (the ``player_events`` system table, reached only through ``outbox``) and
``delivery`` (the worker's leased, fenced delivery loop). This package imports nothing at import
time, so ``classbeat.config_features`` and ``classbeat.egress`` may use ``base_url_policy``.
"""
