"""Guard 9 (ADR 0005 section 3, section 8): the queue role and its SECURITY DEFINER functions.

``check_queue`` returns a list of violations (empty when the database matches the ADR). It is a
second implementation of what web and worker check at startup (``classbeat.db.queue_check``),
read through the superuser so it can also see ``pg_authid``; only the shipped constants (version,
digests, search_path) are shared.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.db.queue_check import QUEUE_FUNCTIONS, QUEUE_VERSION
from classbeat.db.queue_digests import QUEUE_DIGESTS

QUEUE, APP = "classbeat_queue", "classbeat_app"
SEARCH_PATH = ["search_path=pg_catalog, public, pg_temp"]
ACL = {f"{QUEUE}=X/{QUEUE}", f"{APP}=X/{QUEUE}"}
TABLES = {
    "jobs": {"SELECT", "UPDATE"},
    "job_events": {"SELECT", "INSERT"},
    "external_operations": {"SELECT"},
}
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
