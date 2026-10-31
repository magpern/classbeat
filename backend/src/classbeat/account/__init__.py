"""The user's own data: export and deletion (ADR 0014; M6, #337).

- ``classbeat.account.export``: everything the app holds about the signed-in user, built from
  owner-scoped rows only (``classbeat.account.registry`` lists each table and what it leaves out).
- ``classbeat.account.erase``: the deletion, after a typed confirmation: open jobs are cancelled,
  their credits settled, then the ``users`` row goes and the foreign keys do the rest.
- ``classbeat.account.routes``: ``GET /api/account/export`` and ``POST /api/account/delete``.

The package may open a ``user_session`` (``tests/session_scan.py``): both are the user's own
reads and writes in a web request, never the worker's.
"""
