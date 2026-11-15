"""The SwedeBeat gateway client (ADR 0035): classbeat talks to the owner's gateway, never to
SwedeBeat.

Pure rules: ``url_policy`` (``CLASSBEAT_SWEDEBEAT_GATEWAY_URL``), ``key_policy`` (the
``swedebeat_gateway_key`` secret file and "available"), ``login_policy`` and ``match_policy`` (the
request bodies, the answers' field allowlists) and ``status_policy`` (status to category). Side
effects: ``answer`` (a capped, raw read of one answer) and ``client`` (``login``, ``session`` and
``match``, nothing else). The client goes out only through ``classbeat.egress_gateway``. This
package imports nothing at import time, so ``classbeat.config_features`` may use ``url_policy``.
"""
