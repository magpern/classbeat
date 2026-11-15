"""Matching a song selection against Swedebeat's catalogue (ADR 0016 section 5, B1).

- ``classbeat.swedebeat_match.kind``: the ``swedebeat_match`` job kind (registered in
  ``classbeat.jobs.KINDS`` on import, for both the worker and the web app);
- ``classbeat.swedebeat_match.gateway_match``: the one gateway match of a job (ADR 0035 sections 5
  and 6), the 401 confirmation and the status mapping, over ``classbeat.swedebeat_gateway``;
- ``classbeat.swedebeat_match.verdict_policy`` and ``confirm_policy``: what is sent, classbeat's
  verdict (``SAME_SONG``) and a failed call's category, without I/O;
- ``classbeat.swedebeat_match.catalogue`` and ``search``: the direct ``cloud.swedebeat.se`` search
  of ADR 0016, no longer called by any job (SG-4 deletes them with the cloud client);
- ``classbeat.swedebeat_match.revision``: the ``swedebeat_match`` revision, its hash, ``build``,
  ``store`` and the re-checking ``load``;
- ``classbeat.swedebeat_match.replace``: the catalogue replace inside ``revise_songs`` (the
  status-only fact, the three-round limit and the re-match, ADR 0016 section 6);
- ``classbeat.swedebeat_match.config``: the settings both processes ``configure`` at start.

A job kind's own package (job kinds live outside ``classbeat.jobs``): it writes only through the
job's fence, reads the user's rows in the jobs API only through the session it is handed, and opens
the SwedeBeat bearer only through ``classbeat.connections.swedebeat_account.open_bearer``.
"""

from classbeat.swedebeat_match.config import MatchConfig, configure
from classbeat.swedebeat_match.kind import KIND, SWEDEBEAT_MATCH, SwedebeatMatchInput

__all__ = ["KIND", "SWEDEBEAT_MATCH", "MatchConfig", "SwedebeatMatchInput", "configure"]
