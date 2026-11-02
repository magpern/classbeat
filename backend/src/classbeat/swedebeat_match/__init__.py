"""Matching a song selection against Swedebeat's catalogue (ADR 0016 section 5, B1).

- ``classbeat.swedebeat_match.kind``: the ``swedebeat_match`` job kind (registered in
  ``classbeat.jobs.KINDS`` on import, for both the worker and the web app);
- ``classbeat.swedebeat_match.catalogue``: the library's query, the field-allowlist parser of an
  answer and the verdict, without a network call;
- ``classbeat.swedebeat_match.search``: one search over the worker's dedicated catalogue client
  (``classbeat.egress.swedebeat_cloud_async_client``) and its status categories;
- ``classbeat.swedebeat_match.revision``: the ``swedebeat_match`` revision, its hash, ``build``,
  ``store`` and the re-checking ``load``;
- ``classbeat.swedebeat_match.replace``: the catalogue replace inside ``revise_songs`` (the
  status-only fact, the three-round limit and the re-match, ADR 0016 section 6);
- ``classbeat.swedebeat_match.config``: the settings both processes ``configure`` at start.

A job kind's own package (job kinds live outside ``classbeat.jobs``): it writes only through the
job's fence, reads the user's rows in the jobs API only through the session it is handed, and opens
the Swedebeat token only through ``classbeat.connections``.
"""

from classbeat.swedebeat_match.config import MatchConfig, configure
from classbeat.swedebeat_match.kind import KIND, SWEDEBEAT_MATCH, SwedebeatMatchInput

__all__ = ["KIND", "SWEDEBEAT_MATCH", "MatchConfig", "SwedebeatMatchInput", "configure"]
