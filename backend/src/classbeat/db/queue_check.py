"""The queue check of ADR 0005 section 3: web and worker refuse to run on a drifted job queue.

The five ``SECURITY DEFINER`` functions of ``docker/postgres/queue/20-queue.sql`` and their owner
``classbeat_queue`` are the only cross-user paths into the owned job tables. The check runs at
startup (``verify_database_role``) and on every new pooled connection, like the role check, and
refuses on any difference (no override): each function exists once, owned by
``classbeat_queue``, ``SECURITY DEFINER`` with exactly the fixed ``search_path``, the version
comment, the shipped ``md5(pg_get_functiondef(oid))`` and the ACL
``{classbeat_queue=X/classbeat_queue, classbeat_app=X/classbeat_queue}``; the role has exactly
its attributes, no memberships either way, no settings, and exactly its privileges; and no other
function, procedure, aggregate or operator exists in ``public`` or was added to ``pg_catalog``
(the functions' search_path), so nothing can shadow a built-in a function body calls.

Every grant or ownership naming a role is recorded in ``pg_shdepend``, so comparing that list
with the expected one catches a privilege of any kind, in any database of the cluster.
"""

import weakref
from collections.abc import Sequence
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from classbeat.db.queue_digests import QUEUE_DIGESTS

QUEUE_ROLE = "classbeat_queue"
QUEUE_VERSION = "classbeat-queue v2"
SEARCH_PATH = "search_path=pg_catalog, pg_temp"
# name -> identity arguments, as pg_get_function_identity_arguments renders them.
QUEUE_FUNCTIONS: dict[str, str] = {
    "queue_claim": "p_worker text, p_lease_seconds integer, p_kinds text[]",
    "queue_reap": "p_limit integer",
    "queue_expire": "p_limit integer",
    "queue_unsettled": "p_limit integer",
    "queue_stats": "",
}
EXPECTED_ACL = "classbeat_app=X/classbeat_queue,classbeat_queue=X/classbeat_queue"
# rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit
EXPECTED_ATTRIBUTES = [False, False, False, False, False, True, False]
# What classbeat_queue may hold: ACL entries ("a") and ownership ("o"), as pg_shdepend lists them.
EXPECTED_DEPENDENCIES = frozenset(
    {f"pg_proc:public.{name}({args}):0:o" for name, args in QUEUE_FUNCTIONS.items()}
    | {
        "pg_class:public.jobs:0:a",
        "pg_class:public.job_events:0:a",
        "pg_class:public.external_operations:0:a",
        "pg_namespace:public:0:a",
    }
)
EXPECTED_PRIVILEGES = frozenset(
    {
        "schema:public:USAGE",
        "table:public.jobs:SELECT",
        "table:public.jobs:UPDATE",
        "table:public.job_events:SELECT",
        "table:public.job_events:INSERT",
        "table:public.external_operations:SELECT",
    }
)

QUEUE_QUERY = """
WITH q AS (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'classbeat_queue')
SELECT
    (SELECT ARRAY[r.rolcanlogin, r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication,
                  r.rolbypassrls, r.rolinherit]
       FROM pg_catalog.pg_roles r WHERE r.oid = (SELECT oid FROM q)),
    (SELECT count(*) FROM pg_catalog.pg_auth_members m
      WHERE (SELECT oid FROM q) IN (m.member, m.roleid)),
    (SELECT count(*) FROM pg_catalog.pg_db_role_setting s WHERE s.setrole = (SELECT oid FROM q))
      + (SELECT count(*) FROM pg_catalog.pg_default_acl d
          WHERE d.defaclrole = (SELECT oid FROM q)),
    ARRAY(
        SELECT s.classid::regclass::text || ':' ||
               CASE WHEN s.dbid NOT IN (0, (SELECT oid FROM pg_catalog.pg_database
                                             WHERE datname = current_database()))
                      THEN 'another database ' || s.objid::text
                    WHEN s.classid = 'pg_catalog.pg_class'::regclass
                      THEN (SELECT n.nspname || '.' || c.relname FROM pg_catalog.pg_class c
                              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                             WHERE c.oid = s.objid)
                    WHEN s.classid = 'pg_catalog.pg_namespace'::regclass
                      THEN (SELECT n.nspname FROM pg_catalog.pg_namespace n WHERE n.oid = s.objid)
                    WHEN s.classid = 'pg_catalog.pg_proc'::regclass
                      THEN (SELECT n.nspname || '.' || p.proname || '('
                                   || pg_catalog.pg_get_function_identity_arguments(p.oid) || ')'
                              FROM pg_catalog.pg_proc p
                              JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                             WHERE p.oid = s.objid)
                    ELSE s.objid::text END
               || ':' || s.objsubid::text || ':' || s.deptype::text
          FROM pg_catalog.pg_shdepend s
         WHERE s.refclassid = 'pg_catalog.pg_authid'::regclass AND s.refobjid = (SELECT oid FROM q)
         ORDER BY 1),
    ARRAY(
        SELECT 'schema:' || n.nspname || ':' || a.privilege_type
               || CASE WHEN a.is_grantable THEN ' with grant option' ELSE '' END
          FROM pg_catalog.pg_namespace n, aclexplode(n.nspacl) a
         WHERE a.grantee = (SELECT oid FROM q)
        UNION ALL
        SELECT 'table:' || n.nspname || '.' || c.relname || ':' || a.privilege_type
               || CASE WHEN a.is_grantable THEN ' with grant option' ELSE '' END
          FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace,
               aclexplode(c.relacl) a
         WHERE a.grantee = (SELECT oid FROM q)
        ORDER BY 1),
    ARRAY(
        SELECT p.proname || '|' || pg_catalog.pg_get_function_identity_arguments(p.oid)
               || '|' || p.prosecdef::text || '|' || pg_catalog.pg_get_userbyid(p.proowner)
               || '|' || coalesce(array_to_string(p.proconfig, ';'), '')
               || '|' || coalesce(pg_catalog.obj_description(p.oid, 'pg_proc'), '')
               || '|' || md5(pg_catalog.pg_get_functiondef(p.oid))
               || '|' || coalesce(array_to_string(
                             ARRAY(SELECT x::text FROM unnest(p.proacl) x ORDER BY 1), ','), '')
          FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname IN
               ('queue_claim', 'queue_reap', 'queue_expire', 'queue_unsettled', 'queue_stats')
         ORDER BY 1),
    ARRAY(
        SELECT 'function ' || p.oid::regprocedure::text
          FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE (n.nspname = 'public' AND p.proname NOT IN
                  ('queue_claim', 'queue_reap', 'queue_expire', 'queue_unsettled', 'queue_stats'))
            OR (n.nspname = 'pg_catalog' AND p.oid >= 16384)
        UNION ALL
        SELECT 'operator ' || o.oid::regoperator::text
          FROM pg_catalog.pg_operator o JOIN pg_catalog.pg_namespace n ON n.oid = o.oprnamespace
         WHERE n.nspname = 'public' OR (n.nspname = 'pg_catalog' AND o.oid >= 16384)
        ORDER BY 1)
"""
# 16384 is FirstNormalObjectId: below it, pg_catalog holds only what initdb created.
COLUMN_COUNT = 7


def queue_problems(row: Sequence[Any] | None) -> list[str]:
    """Problems for one ``QUEUE_QUERY`` row; empty when the queue matches ADR 0005 section 3."""
    if row is None or len(row) != COLUMN_COUNT:
        raise ValueError("the queue query must return seven columns")
    attributes, memberships, settings, dependencies, privileges, functions, foreign = row
    if attributes is None:
        return [f"the role {QUEUE_ROLE} does not exist (run docker/postgres/queue/20-queue.sql)"]
    found: list[str] = []
    if list(attributes) != EXPECTED_ATTRIBUTES:
        found.append(
            f"{QUEUE_ROLE} must be NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            "BYPASSRLS NOINHERIT"
        )
    if memberships:
        found.append(f"{QUEUE_ROLE} must be a member of no role and have no members")
    if settings:
        found.append(f"{QUEUE_ROLE} must have no role settings and no default privileges")
    if set(dependencies) != EXPECTED_DEPENDENCIES or len(dependencies) != len(set(dependencies)):
        found.append(f"{QUEUE_ROLE} owns or holds privileges other than the expected ones")
    if set(privileges) != EXPECTED_PRIVILEGES or len(privileges) != len(set(privileges)):
        found.append(f"{QUEUE_ROLE} must have exactly its table privileges and USAGE on public")
    if foreign:
        found.append(
            "functions or operators other than the queue's exist in public or pg_catalog: "
            + ", ".join(foreign)
        )
    by_name: dict[str, list[list[str]]] = {}
    for item in functions:
        parts = item.split("|")
        by_name.setdefault(parts[0], []).append(parts)
    for name, args in QUEUE_FUNCTIONS.items():
        rows = by_name.get(name, [])
        if len(rows) != 1:
            found.append(f"public.{name} must exist exactly once, found {len(rows)}")
            continue
        expected = [
            name,
            args,
            "true",
            QUEUE_ROLE,
            SEARCH_PATH,
            QUEUE_VERSION,
            QUEUE_DIGESTS.get(name, ""),
            EXPECTED_ACL,
        ]
        if rows[0] != expected:
            found.append(
                f"public.{name} differs from the shipped definition (owner, SECURITY DEFINER, "
                "search_path, version, md5 or ACL)"
            )
    return found


_verified: "weakref.WeakKeyDictionary[Engine, bool]" = weakref.WeakKeyDictionary()


def track_queue_check(engine: AsyncEngine) -> None:
    """Mark ``engine`` as one whose connections must pass the queue check."""
    _verified.setdefault(engine.sync_engine, False)


def queue_check_enabled(engine: AsyncEngine) -> bool:
    return engine.sync_engine in _verified


def mark_queue_verified(engine: Engine) -> None:
    if engine in _verified:
        _verified[engine] = True


def queue_verified(engine: AsyncEngine) -> bool:
    """True once a connection of ``engine`` passed the queue check. The worker claims nothing
    before that (ADR 0005 section 3): a database that was down at startup was not checked yet."""
    return _verified.get(engine.sync_engine, False)
