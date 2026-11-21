"""The spotdown analysis service client (ADR 0040, SD-P4, #1301): classbeat's side of spotdown's
v2 contract (``docs/contract-v2.md`` in magpern/spotdown; the shapes are its ``schema/*-v1.json``).

Pure rules: ``url_policy`` (``CLASSBEAT_SPOTDOWN_URL``, an internal origin only),
``sources_policy`` (``CLASSBEAT_SPOTDOWN_SOURCES``), ``key_policy`` (the ``spotdown_service_key``
secret file, its redacted holder and "available"), ``scope_calc`` (``cache_scope``, an HMAC of the
owner id, the shared scope of a kind, and ``Idempotency-Key``), ``scope_policy`` (which kind is
shared: only ``studio``, an allowlist; #1338), ``request_model`` (request bodies, paths and
queries from checked values), ``answer_model`` and ``match_model`` (strict answer parsers) and
``failure_policy`` (the failure taxonomy of ADR 0040 section 8 as typed results).
The analysis rules (SD-P4 part 2): ``timing_class_policy`` with ``timing_inputs_model`` and
``anchor_calc`` (the strict ``exact`` rule, section 3), ``feature_trust_policy``,
``analysis_features_calc`` and ``analysis_hints_policy`` (what may be validated with, section 5),
``spotdown_map_policy`` (an analysis as the studio track map or MOWL-shaped Spotify sections),
``track_fit_policy`` with ``track_facts_calc`` (the bounded suitability refinement, section 2) and
``provenance_model`` with ``provenance_read_calc`` (the per-track record, section 6, and the reuse
rule, section 7). Shadow mode (SD-P4 part 3) uses ``timing_class_policy`` (path (a) only),
``spotdown_map_policy.track_map`` and ``provenance_model`` through ``shadow_record_model``, with
``shadow_compare_calc`` (the metrics), ``shadow_policy`` (budget, pace, outcomes), ``shadow_config``
and ``scope_key``; the side effects are ``classbeat.design.track_shadow`` and ``scope_outbox`` (the
account deletion's ``DELETE /v2/scopes``). Studio tracks via spotdown (SD-P5, #1302):
``studio_map_model`` (the map and provenance, always through ``timing_class_policy`` and
``spotdown_map_policy``), ``studio_policy`` (the failure reactions and the budget),
and ``studio_tracks_config`` (what the studio and the mixes share: usable or not, no switch,
#1353), ``outage_policy`` (an ``Unreachable``'s category); the side effects are
``classbeat.design.track_spotdown``, ``mix_spotdown`` and ``spotdown_ending``.
The Spotify song pick's match stage (SD-P6 stage A, #1303): ``song_match_config`` with
``owner_list_policy`` (the allowlist), ``song_match_policy`` (each match's use, the reactions,
the outage rule and the wait budget), ``song_swap_calc`` (spares and replacement rounds) and
``song_match_record_model`` (the list's provenance record); the side effects are
``classbeat.songs.pick_match``, ``pick_match_batch`` and ``pick_match_budget``.
Its analysis and refinement stage (SD-P6 stage B): ``song_analysis_policy`` (the reactions and a
slot's needs), ``song_refine_calc`` (the spare that replaces an unsuitable song) and
``song_analysis_record_model`` (the record and the timing marks); the side effects are
``classbeat.songs.pick_analyse`` and ``pick_analyse_batch``.
Side effects: ``opener`` (the seam that opens a client of ``classbeat.egress_spotdown``),
``exchange`` (one capped request) and ``client`` (the calls of the contract, nothing else).
Nothing here calls spotdown unless the setting is set: unset, ``classbeat.egress_spotdown``
refuses before anything is built.
This package imports nothing at import time, so ``classbeat.config_features`` may use
``url_policy`` and ``sources_policy``.
"""
