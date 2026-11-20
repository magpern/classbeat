"""The spotdown analysis service client (ADR 0040, SD-P4, #1301): classbeat's side of spotdown's
v2 contract (``docs/contract-v2.md`` in magpern/spotdown; the shapes are its ``schema/*-v1.json``).

Pure rules: ``url_policy`` (``CLASSBEAT_SPOTDOWN_URL``, an internal origin only),
``sources_policy`` (``CLASSBEAT_SPOTDOWN_SOURCES``), ``key_policy`` (the ``spotdown_service_key``
secret file, its redacted holder and "available"), ``scope_calc`` (``cache_scope``, an HMAC of the
owner id, and ``Idempotency-Key``), ``request_model`` (request bodies, paths and queries from
checked values), ``answer_model`` and ``match_model`` (strict answer parsers) and
``failure_policy`` (the failure taxonomy of ADR 0040 section 8 as typed results).
Side effects: ``opener`` (the seam that opens a client of ``classbeat.egress_spotdown``),
``exchange`` (one capped request) and ``client`` (the calls of the contract, nothing else).
Nothing here calls spotdown unless the setting is set: unset, ``classbeat.egress_spotdown``
refuses before anything is built.
This package imports nothing at import time, so ``classbeat.config_features`` may use
``url_policy`` and ``sources_policy``.
"""
