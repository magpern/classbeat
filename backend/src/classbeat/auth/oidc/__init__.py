"""classbeat as an OpenID Connect provider for exactly one client, classbeat-player (ADR 0034
section 1).

Only this package (and migrations and tests) reads or writes ``oidc_requests``, ``oidc_codes`` and
``oidc_grants`` (guard 8). Pure rules: ``request_policy`` (the authorize request),
``auth_time_policy`` (``prompt=login`` and ``auth_time``), ``subject_calc`` (the pairwise
subject), ``keyset_policy`` (the signing keys and their rotation), ``idtoken_calc`` (the claims
and the discovery document) and ``limits``. Side effects: the stores, ``provider`` (the secret
files), ``signing`` and the routes.
"""
