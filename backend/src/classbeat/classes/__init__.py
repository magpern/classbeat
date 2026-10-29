"""Classes and their revisions, as the signed-in user sees them (ADR 0007 sections 2, 3, 11, 12).

- ``classbeat.classes.service``: the reads, the pick of the current revision and the class delete,
  one ``user_session`` each (a user's own work in a web request, never the worker's);
- ``classbeat.classes.seams``: the two things operations issue X2 (#143) provides;
- ``classbeat.classes.schemas`` and ``classbeat.classes.routes``: the JSON API (web only; not
  imported here, so the worker never loads it).

This package may open a ``user_session`` (``tests/session_scan.py``). Every statement filters on
``owner_id = ctx.user_id`` as well as running under RLS: another user's id is not found, exactly
like an unknown one.
"""
