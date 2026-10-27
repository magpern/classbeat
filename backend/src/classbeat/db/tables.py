"""Tables that are not per-user (ADR 0002 section 3)."""

from types import MappingProxyType

# table name -> one-line reason it has no ``owner_id`` and no row-level security.
# Every other table in ``public`` is owned; the guard test in ``tests/test_rls_guard.py``
# holds both sides to the contract. The first entries arrive with M1 (users, identities,
# sessions) and M2 (shared music analysis).
SYSTEM_TABLES: MappingProxyType[str, str] = MappingProxyType({})
