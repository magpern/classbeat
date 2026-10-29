"""The agent smoke set (ADR 0006 section 8): 15 design and 3 revise cases (ADR 0007 section 3),
each a brief, a synthetic music fixture and deterministic checks on the draft the design or revise
stage submits (a revise case also holds the base draft and the requests).

- ``cases``: the case files (``smoke/*.json``) and their models;
- ``music``: the synthetic playlists (invented titles and BPMs, no Spotify data), built into the
  library's music map with ``mowl.spotify.build_map``;
- ``checks``: the checks on a submitted draft; the ones ``mowl.quality.report`` covers use it;
- ``runner``: one case through ``classbeat.agent.run_stage`` with a given provider;
- ``live``, ``__main__``: ``python -m classbeat.evals smoke --live``, the owner's command.

In CI every case runs against ``FakeProvider`` scripts (``tests/test_evals_smoke.py``): no model,
no network. The live command is the only code here that builds a real provider, and it is never
run by CI or by an agent: it needs ``--live``, the Anthropic key as a secret and the ``anthropic``
provider (see ``live``). Worker-side code: the web process never imports this package.
"""
