"""Guard 9 (ADR 0005 section 3, section 8): the queue role and its SECURITY DEFINER functions.

``check_queue`` returns a list of violations (empty when the database matches the ADR). It is a
second implementation of what web and worker check at startup (``classbeat.db.queue_check``),
read through the superuser so it can also see ``pg_authid``; only the shipped constants (version,
digests, search_path) are shared.
"""

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.db.queue_check import QUEUE_FUNCTIONS, QUEUE_VERSION
from classbeat.db.queue_digests import GUARD_TRIGGER_DIGESTS, QUEUE_DIGESTS

QUEUE, APP, OWNER = "classbeat_queue", "classbeat_app", "classbeat_owner"
# The M5 guard triggers of migration 0007 (ADR 0011 section 1): function = trigger name -> (table,
# the events of a row-level BEFORE trigger). pg_trigger.tgtype bits: ROW 1, BEFORE 2, INSERT 4,
# DELETE 8, UPDATE 16, TRUNCATE 32, INSTEAD 64.
GUARD_TRIGGERS = {
    "credit_ledger_guard": ("credit_ledger", 16),
    "admin_audit_guard": ("admin_audit", 8 | 16),
}
SEARCH_PATH = ["search_path=pg_catalog, pg_temp"]
ACL = {f"{QUEUE}=X/{QUEUE}", f"{APP}=X/{QUEUE}"}
TABLES = {
    "jobs": {"SELECT", "UPDATE"},
    "job_events": {"SELECT", "INSERT"},
    "external_operations": {"SELECT"},
}
# Schemas whose functions and operators below FirstNormalObjectId (16384) are initdb's built-ins.
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")
# Every catalog with an ACL column (name, ACL column, how to name the object).
ACL_CATALOGS = (
    ("pg_database", "datacl", "datname"),
    ("pg_namespace", "nspacl", "nspname"),
    ("pg_class", "relacl", "relnamespace::regnamespace::text || '.' || relname"),
    ("pg_proc", "proacl", "oid::regprocedure::text"),
    ("pg_language", "lanacl", "lanname"),
    ("pg_type", "typacl", "oid::regtype::text"),
    ("pg_largeobject_metadata", "lomacl", "oid::text"),
    ("pg_foreign_data_wrapper", "fdwacl", "fdwname"),
    ("pg_foreign_server", "srvacl", "srvname"),
    ("pg_tablespace", "spcacl", "spcname"),
    ("pg_parameter_acl", "paracl", "parname"),
)


# Built-ins that run SQL text, read whole tables or change settings (#108 item 1), by name in
# pg_catalog (every overload); written apart from classbeat.db.queue_check's list on purpose.
SQL_TEXT_BUILTINS = frozenset(
    {f"{prefix}_to_{suffix}" for prefix in ("query", "table", "cursor", "schema", "database")
     for suffix in ("xml", "xmlschema", "xml_and_xmlschema")}
    - {"cursor_to_xml_and_xmlschema"}
    | {"ts_stat", "ts_rewrite", "set_config"}
)  # fmt: skip
# Every catalog column holding an expression tree a write or a type can evaluate.
EXPRESSION_SOURCES = (
    "SELECT 'the default of ' || adrelid::regclass::text || '.' || "
    "(SELECT attname FROM pg_attribute WHERE attrelid = adrelid AND attnum = adnum), "
    "adbin::text FROM pg_attrdef",
    "SELECT 'constraint ' || conname, conbin::text FROM pg_constraint WHERE conbin IS NOT NULL",
    "SELECT 'index ' || indexrelid::regclass::text, indexprs::text FROM pg_index "
    "WHERE indexprs IS NOT NULL",
    "SELECT 'index ' || indexrelid::regclass::text, indpred::text FROM pg_index "
    "WHERE indpred IS NOT NULL",
    "SELECT 'the default of type ' || oid::regtype::text, typdefaultbin::text FROM pg_type "
    "WHERE typdefaultbin IS NOT NULL",
    "SELECT 'the partition key of ' || partrelid::regclass::text, partexprs::text "
    "FROM pg_partitioned_table WHERE partexprs IS NOT NULL",
)
FUNCID = re.compile(r":funcid ([0-9]+)")


def expression_calls(conn: Connection) -> list[str]:
    """``built-in <name> in <where>`` for each stored expression calling one of
    ``SQL_TEXT_BUILTINS``."""
    refused = dict(
        conn.execute(
            text(
                "SELECT p.oid, p.proname FROM pg_proc p WHERE p.proname = ANY(:names) "
                "AND p.pronamespace = 'pg_catalog'::regnamespace"
            ),
            {"names": sorted(SQL_TEXT_BUILTINS)},
        ).all()
    )
    found: set[str] = set()
    for source in EXPRESSION_SOURCES:
        for where, tree in conn.execute(text(source)).all():
            for oid in FUNCID.findall(tree):
                if int(oid) in refused:
                    found.add(f"built-in {refused[int(oid)]} in {where}")
    return sorted(found)


def check_queue(conn: Connection) -> list[str]:
    problems: list[str] = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    role = conn.execute(text("SELECT * FROM pg_authid WHERE rolname = :q"), {"q": QUEUE}).first()
    if role is None:
        return [f"{QUEUE} does not exist"]
    need(not role.rolcanlogin, f"{QUEUE} must be NOLOGIN")
    need(role.rolpassword is None, f"{QUEUE} must have no password")
    need(role.rolbypassrls, f"{QUEUE} must have BYPASSRLS (it owns the cross-user functions)")
    need(not role.rolinherit, f"{QUEUE} must be NOINHERIT")
    for attribute in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication"):
        need(not getattr(role, attribute), f"{QUEUE} must not have {attribute}")
    members = conn.execute(
        text("SELECT count(*) FROM pg_auth_members WHERE :oid IN (member, roleid)"),
        {"oid": role.oid},
    ).scalar_one()
    need(members == 0, f"{QUEUE} must be a member of no role and have no members")
    settings = conn.execute(
        text("SELECT count(*) FROM pg_db_role_setting WHERE setrole = :oid"), {"oid": role.oid}
    ).scalar_one()
    need(settings == 0, f"{QUEUE} must have no role settings")
    default_acls = conn.execute(
        text(
            "SELECT count(*) FROM pg_default_acl d WHERE d.defaclrole = :oid OR EXISTS "
            "(SELECT 1 FROM aclexplode(d.defaclacl) a WHERE a.grantee = :oid)"
        ),
        {"oid": role.oid},
    ).scalar_one()
    need(default_acls == 0, f"{QUEUE} must appear in no default privileges")

    # Privileges held, over every ACL kind of this database and the shared catalogs.
    held: set[tuple[str, str, str]] = set()
    for catalog, column, name in ACL_CATALOGS:
        for obj, privilege, grantable in conn.execute(
            text(
                f"SELECT {name}, a.privilege_type, a.is_grantable "  # noqa: S608
                f"FROM pg_catalog.{catalog} c, aclexplode(c.{column}) a "
                "WHERE a.grantee = :oid AND NOT (:catalog = 'pg_proc' AND c.oid IN "
                "(SELECT oid FROM pg_proc WHERE proowner = :oid))"
            ),
            {"oid": role.oid, "catalog": catalog},
        ).all():
            need(not grantable, f"{QUEUE}: grantable {privilege} on {catalog} {obj}")
            held.add((catalog, str(obj), str(privilege)))
    columns = conn.execute(
        text("SELECT count(*) FROM pg_attribute t, aclexplode(t.attacl) a WHERE a.grantee = :oid"),
        {"oid": role.oid},
    ).scalar_one()
    need(columns == 0, f"{QUEUE} must hold no column privileges")
    expected = {("pg_namespace", "public", "USAGE")} | {
        ("pg_class", f"public.{table}", privilege)
        for table, privileges in TABLES.items()
        for privilege in privileges
    }
    need(held == expected, f"{QUEUE} privileges must be exactly {sorted(expected)}, are {held}")
    other_databases = conn.execute(
        text(
            "SELECT count(*) FROM pg_shdepend WHERE refobjid = :oid AND dbid NOT IN "
            "(0, (SELECT oid FROM pg_database WHERE datname = current_database()))"
        ),
        {"oid": role.oid},
    ).scalar_one()
    need(other_databases == 0, f"{QUEUE} must own or hold nothing in another database")
    policies = conn.execute(
        text("SELECT count(*) FROM pg_policy WHERE :oid = ANY(polroles)"), {"oid": role.oid}
    ).scalar_one()
    need(policies == 0, f"{QUEUE} must be named in no policy")

    # Nothing that could shadow a built-in called by a function body, and nothing a function body
    # fires that could run user code as classbeat_queue (review finding B2, #102): no user-defined
    # function, procedure, aggregate or operator in any schema besides the five, no trigger but
    # the internal ones of foreign keys, no rule but the _RETURN rules of views. Named as the
    # startup check and the script name them (#108 item 4).
    hooks = (
        conn.execute(
            text(
                "SELECT 'function ' || n.nspname || '.' || p.proname "
                "|| '(' || oidvectortypes(p.proargtypes) || ')' FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE NOT (n.nspname = 'public' AND p.proname = ANY(:names)) "
                "AND (p.oid >= 16384 OR NOT n.nspname = ANY(:system)) "
                "UNION ALL SELECT 'operator ' || n.nspname || '.' || o.oprname || '(' "
                "|| CASE WHEN o.oprleft = 0 THEN 'NONE' ELSE format_type(o.oprleft, NULL) END "
                "|| ', ' "
                "|| CASE WHEN o.oprright = 0 THEN 'NONE' ELSE format_type(o.oprright, NULL) END "
                "|| ')' FROM pg_operator o "
                "JOIN pg_namespace n ON n.oid = o.oprnamespace "
                "WHERE o.oid >= 16384 OR NOT n.nspname = ANY(:system) "
                "UNION ALL SELECT 'trigger ' || t.tgname || ' on ' || n.nspname || '.' "
                "|| c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal "
                "UNION ALL SELECT 'rule ' || r.rulename || ' on ' || n.nspname || '.' "
                "|| c.relname FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE r.rulename <> '_RETURN' AND r.oid >= 16384"
            ),
            {"names": list(QUEUE_FUNCTIONS), "system": list(SYSTEM_SCHEMAS)},
        )
        .scalars()
        .all()
    )
    # The one amendment (ADR 0011 section 1): the two M5 guard triggers, admitted only exactly.
    pinned = pinned_guard_triggers(conn)
    hooks = [hook for hook in hooks if hook not in pinned]
    # ... and both must exist exactly as pinned (review of #233), not only be allowed.
    expected_pinned = {
        hook
        for name, (table, _) in GUARD_TRIGGERS.items()
        for hook in (f"function public.{name}()", f"trigger {name} on public.{table}")
    }
    need(
        pinned == expected_pinned,
        f"the M5 guard triggers are missing or differ: {sorted(expected_pinned - pinned)}",
    )
    # Temp schemas (pg_temp_N) hold no built-ins, so any function there is >= 16384 and found.
    need(
        not hooks,
        f"user-defined functions, operators, triggers or rules besides the queue's: {hooks}",
    )
    # Built-ins that run SQL text (or change settings) called from any stored expression (#108
    # item 1): read every expression tree and look for the functions' OIDs in Python.
    calls = expression_calls(conn)
    need(not calls, f"expressions call a built-in that runs SQL text: {calls}")

    # Ownership: exactly the five functions.
    owned = conn.execute(
        text(
            "SELECT classid::regclass::text, objid FROM pg_shdepend "
            "WHERE refobjid = :oid AND deptype = 'o'"
        ),
        {"oid": role.oid},
    ).all()
    functions = conn.execute(
        text(
            "SELECT p.oid, n.nspname, p.proname, "
            "pg_get_function_identity_arguments(p.oid) AS args, "
            "p.prosecdef, p.prokind, l.lanname, p.proowner, p.proconfig, p.proacl::text[] AS acl, "
            "obj_description(p.oid, 'pg_proc') AS comment, md5(pg_get_functiondef(p.oid)) AS md5 "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "JOIN pg_language l ON l.oid = p.prolang WHERE p.proname = ANY(:names)"
        ),
        {"names": list(QUEUE_FUNCTIONS)},
    ).all()
    need(
        sorted(str(c) for c, _ in owned) == ["pg_proc"] * len(QUEUE_FUNCTIONS)
        and {o for _, o in owned} == {f.oid for f in functions if f.proowner == role.oid},
        f"{QUEUE} must own exactly the queue functions",
    )
    for name, args in QUEUE_FUNCTIONS.items():
        found = [f for f in functions if f.proname == name]
        if len(found) != 1:
            problems.append(f"{name}: must exist exactly once (in any schema), found {len(found)}")
            continue
        f = found[0]
        prefix = f"public.{name}"
        need(f.nspname == "public" and f.args == args, f"{prefix}: wrong schema or arguments")
        need(f.prosecdef, f"{prefix}: must be SECURITY DEFINER")
        need(f.prokind == "f" and f.lanname == "plpgsql", f"{prefix}: must be a plpgsql function")
        need(f.proowner == role.oid, f"{prefix}: must be owned by {QUEUE}")
        need(list(f.proconfig or []) == SEARCH_PATH, f"{prefix}: search_path must be fixed")
        need(f.comment == QUEUE_VERSION, f"{prefix}: comment must be {QUEUE_VERSION!r}")
        need(f.md5 == QUEUE_DIGESTS[name], f"{prefix}: definition md5 differs from the shipped one")
        need(set(f.acl or []) == ACL and len(f.acl or []) == 2, f"{prefix}: ACL must be {ACL}")
    return problems


def pinned_guard_triggers(conn: Connection) -> set[str]:
    """The hook names (as ``check_queue`` names them) of the M5 guard triggers that match their
    pinned definition exactly; a guard that differs in anything is left to be reported."""
    allowed: set[str] = set()
    for name, (table, events) in GUARD_TRIGGERS.items():
        functions = conn.execute(
            text(
                "SELECT p.oid, p.prokind, p.prosecdef, l.lanname, pg_get_userbyid(p.proowner), "
                "CAST(p.proacl AS text[]), p.proconfig, oidvectortypes(p.proargtypes), "
                "format_type(p.prorettype, NULL), "
                "CASE WHEN p.prokind = 'f' THEN md5(pg_get_functiondef(p.oid)) END "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "JOIN pg_language l ON l.oid = p.prolang "
                "WHERE n.nspname = 'public' AND p.proname = :name"
            ),
            {"name": name},
        ).all()
        if len(functions) != 1:
            continue
        oid, *facts = functions[0]
        expected = [
            "f", False, "plpgsql", OWNER, [f"{OWNER}=X/{OWNER}"], SEARCH_PATH, "", "trigger",
            GUARD_TRIGGER_DIGESTS[name],
        ]  # fmt: skip
        if [*facts[:4], list(facts[4] or []), list(facts[5] or []), *facts[6:]] != expected:
            continue
        allowed.add(f"function public.{name}()")
        triggers = conn.execute(
            text(
                "SELECT t.tgname, n.nspname, c.relname, t.tgfoid, t.tgtype, t.tgenabled, "
                "t.tgnargs, CAST(t.tgattr AS text), t.tgqual IS NULL, t.tgconstraint, "
                "t.tgoldtable IS NULL AND t.tgnewtable IS NULL "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE NOT t.tgisinternal AND (t.tgname = :name OR t.tgfoid = :oid)"
            ),
            {"name": name, "oid": oid},
        ).all()
        if [tuple(t) for t in triggers] == [
            (name, "public", table, oid, 1 | 2 | events, "O", 0, "", True, 0, True)
        ]:
            allowed.add(f"trigger {name} on public.{table}")
    return allowed
