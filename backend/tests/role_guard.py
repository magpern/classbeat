"""Guard 2 of ADR 0002 section 6: roles, privileges and defaults, checked as an allowlist.

``check_roles`` returns a list of violations (empty when the database matches section 4). It
reads ``pg_roles``, ``pg_auth_members``, ``pg_shdepend`` and the ACLs through ``aclexplode``, so
a privilege of a kind nobody thought of still fails.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.db.queue_check import QUEUE_FUNCTIONS
from classbeat.db.role_check import EXPECTED_ROLE_SETTINGS
from classbeat.db.tables import NO_APP_ACCESS

APP, OWNER, QUEUE = "classbeat_app", "classbeat_owner", "classbeat_queue"
# ADR 0005 section 3: exactly these for the queue functions' owner, and USAGE on public.
QUEUE_TABLE_PRIVILEGES = {
    "jobs": {"SELECT", "UPDATE"},
    "job_events": {"SELECT", "INSERT"},
    "external_operations": {"SELECT"},
}
# The SECURITY DEFINER functions classbeat_app may execute (regprocedure, as rendered).
QUEUE_SIGNATURES = {
    "queue_claim(text,integer,text[])",
    "queue_reap(integer)",
    "queue_expire(integer)",
    "queue_unsettled(integer)",
    "queue_stats()",
}
# The M5 guard trigger functions (migration 0007, ADR 0011 section 1): SECURITY INVOKER, owned by
# classbeat_owner, executable by nobody else (firing a trigger needs no EXECUTE).
GUARD_TRIGGER_FUNCTIONS = frozenset({"credit_ledger_guard()", "admin_audit_guard()"})
TABLE_PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE"}
# Tables where the app role gets less than full DML: the KEK check values are recorded and read,
# never changed or forgotten by the app (migration 0003; ``keys wipe`` deletes as the owner);
# class messages are append-only and usage rows are never deleted (migration 0005; cascades run
# as the table owner).
RESTRICTED_APP_PRIVILEGES = {
    "connection_keks": {"SELECT", "INSERT"},
    "class_messages": {"SELECT", "INSERT"},
    "agent_runs": {"SELECT", "INSERT", "UPDATE"},
    # ADR 0011 section 1 (M5): read-only tiers and settings; entitlements and redemptions are
    # never updated by the app (a bug can't extend ``ends_at`` or un-revoke); the ledger and the
    # lock rows are narrowed further by policies and the ledger trigger. ``admin_audit`` is in
    # ``NO_APP_ACCESS``.
    "plans": {"SELECT"},
    "app_settings": {"SELECT"},
    "entitlements": {"SELECT", "INSERT"},
    "code_redemptions": {"SELECT", "INSERT"},
    "redeem_codes": {"SELECT"},
    "credit_ledger": {"SELECT", "INSERT", "UPDATE"},
    "quota_periods": {"SELECT", "INSERT", "UPDATE"},
    # ADR 0012 section 5 (M3c): eval results are append-only for the app (no UPDATE, DELETE or
    # TRUNCATE, also after 10-roles.sql runs again); ``model_routes`` keeps full DML (deleting a
    # route disables a model) and, like every table, no TRUNCATE.
    "model_eval_results": {"SELECT", "INSERT"},
}
# The only column-level privileges in public, exactly: (table, column) -> {(grantee, privilege)}.
# ``redeem_codes.redeemed_count`` is what a redeem increments (ADR 0011 section 2).
COLUMN_PRIVILEGES = {
    ("redeem_codes", "redeemed_count"): {(APP, "UPDATE")},
}
SEQUENCE_PRIVILEGES = {"USAGE", "SELECT"}
# Built-in languages that PUBLIC may use (Postgres' own default).
BUILTIN_LANGUAGES = {"plpgsql", "sql", "internal", "c"}
EXPECTED_TIMEOUTS = EXPECTED_ROLE_SETTINGS
GRANTEE = "CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END"
Acl = list[tuple[str, str, str, bool]]


def _acl(conn: Connection, sql: str) -> Acl:
    """Rows of (object, grantee, privilege, grantable) from ``aclexplode``."""
    return [
        (str(o), str(g), str(p), bool(x))
        for o, g, p, x in conn.execute(text(sql.replace("{G}", GRANTEE))).all()
    ]


def _privileges(rows: Acl, grantee: str, obj: str) -> set[str]:
    return {p for o, g, p, _ in rows if o == obj and g == grantee}


def _grantees(rows: Acl, obj: str | None = None) -> set[str]:
    return {g for o, g, _, _ in rows if obj is None or o == obj}


def check_roles(conn: Connection) -> list[str]:
    problems: list[str] = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    # Role attributes, memberships and ownership.
    roles = {
        r.rolname: r
        for r in conn.execute(
            text("SELECT * FROM pg_roles WHERE rolname IN (:app, :owner)"),
            {"app": APP, "owner": OWNER},
        ).all()
    }
    if set(roles) != {APP, OWNER}:
        return ["classbeat_app and classbeat_owner must both exist"]
    for name, role in roles.items():
        need(role.rolcanlogin, f"{name} must be able to log in")
        for attribute in (
            "rolsuper",
            "rolbypassrls",
            "rolcreatedb",
            "rolcreaterole",
            "rolreplication",
        ):
            need(not getattr(role, attribute), f"{name} must not have {attribute}")
        members = conn.execute(
            text("SELECT count(*) FROM pg_auth_members WHERE member = :oid"), {"oid": role.oid}
        ).scalar_one()
        need(members == 0, f"{name} must be a member of no role")
        privileged = conn.execute(
            text(
                "WITH RECURSIVE r AS (SELECT CAST(:oid AS oid) AS oid "
                "UNION SELECT am.roleid FROM pg_auth_members am JOIN r ON am.member = r.oid) "
                "SELECT count(*) FROM pg_roles x JOIN r ON r.oid = x.oid "
                "WHERE x.rolsuper OR x.rolbypassrls"
            ),
            {"oid": role.oid},
        ).scalar_one()
        need(privileged == 0, f"{name} must not reach a superuser or BYPASSRLS role")
    need(not roles[APP].rolinherit, "classbeat_app must be NOINHERIT")
    owned_by_app = conn.execute(
        text(
            "SELECT count(*) FROM pg_shdepend WHERE refobjid = :oid AND deptype = 'o' "
            "AND dbid IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))"
        ),
        {"oid": roles[APP].oid},
    ).scalar_one()
    need(owned_by_app == 0, "classbeat_app must own nothing")
    # Role settings: exactly the three timeouts, at role level. A per-database setting would take
    # precedence over them, and anything else (session_replication_role, search_path...) changes
    # how the app role's sessions behave.
    setting_rows = conn.execute(
        text("SELECT setdatabase, setconfig FROM pg_db_role_setting WHERE setrole = :oid"),
        {"oid": roles[APP].oid},
    ).all()
    need(
        [row.setdatabase for row in setting_rows] in ([], [0]),
        "classbeat_app may have role-level settings only, none per database",
    )
    found = dict(item.split("=", 1) for row in setting_rows for item in row.setconfig)
    need(
        found == EXPECTED_TIMEOUTS,
        f"classbeat_app role settings must be exactly {EXPECTED_TIMEOUTS}, are {found}",
    )

    # The app role can connect to its own database and no other.
    other_databases = (
        conn.execute(
            text(
                "SELECT datname FROM pg_database WHERE datallowconn "
                "AND datname <> current_database() "
                "AND has_database_privilege(:app, oid, 'CONNECT')"
            ),
            {"app": APP},
        )
        .scalars()
        .all()
    )
    need(not other_databases, f"classbeat_app must not connect to {other_databases}")

    # The database.
    db = conn.execute(text("SELECT current_database()")).scalar_one()
    databases = _acl(
        conn,
        "SELECT d.datname, {G}, a.privilege_type, a.is_grantable FROM pg_database d, "
        "aclexplode(coalesce(d.datacl, acldefault(CAST('d' AS \"char\"), d.datdba))) a "
        "WHERE d.datname = current_database()",
    )
    need(
        _privileges(databases, APP, db) == {"CONNECT"},
        "classbeat_app: CONNECT only on the database",
    )
    need(not _privileges(databases, "PUBLIC", db), "PUBLIC must have no privilege on the database")
    need("CONNECT" in _privileges(databases, OWNER, db), "classbeat_owner needs CONNECT")
    need(_grantees(databases) <= {APP, OWNER}, "unexpected grantee on the database")
    database_owner = conn.execute(
        text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
    ).scalar_one()
    need(database_owner == OWNER, "classbeat_owner must own the database")

    # Schemas.
    schemas = _acl(
        conn,
        "SELECT n.nspname, {G}, a.privilege_type, a.is_grantable FROM pg_namespace n, "
        "aclexplode(coalesce(n.nspacl, acldefault(CAST('n' AS \"char\"), n.nspowner))) a "
        "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'",
    )
    need(_privileges(schemas, APP, "public") == {"USAGE"}, "classbeat_app: USAGE only on public")
    need(not _privileges(schemas, "PUBLIC", "public"), "PUBLIC must have no privilege on public")
    need(
        {"USAGE", "CREATE"} <= _privileges(schemas, OWNER, "public"),
        "classbeat_owner needs USAGE and CREATE on public",
    )
    queue_exists = bool(
        conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :q"), {"q": QUEUE}).first()
    )
    need(
        _privileges(schemas, QUEUE, "public") == ({"USAGE"} if queue_exists else set()),
        "classbeat_queue: USAGE only on public",
    )
    need(_grantees(schemas) <= {APP, OWNER, QUEUE}, "unexpected grantee on a schema")
    need(
        not [1 for o, g, _, _ in schemas if o != "public" and g in (APP, QUEUE, "PUBLIC")],
        "no privilege for PUBLIC, classbeat_app or classbeat_queue on another schema",
    )
    schema_owner = conn.execute(
        text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'")
    ).scalar_one()
    need(schema_owner == OWNER, "classbeat_owner must own schema public")

    # Tables and sequences (and views etc., which are not allowed but must not be exposed).
    relations = _acl(
        conn,
        "SELECT c.relname || ':' || c.relkind::text, {G}, a.privilege_type, a.is_grantable "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, "
        "aclexplode(coalesce(c.relacl, acldefault("
        "CASE WHEN c.relkind = 'S' THEN CAST('s' AS \"char\") "
        "ELSE CAST('r' AS \"char\") END, c.relowner))) a "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')",
    )
    for obj in sorted({o for o, *_ in relations}):
        expected = SEQUENCE_PRIVILEGES if obj.endswith(":S") else TABLE_PRIVILEGES
        expected = RESTRICTED_APP_PRIVILEGES.get(obj.split(":")[0], expected)
        if obj.split(":")[0] in NO_APP_ACCESS:
            expected = set()
        need(
            _privileges(relations, APP, obj) == expected,
            f"{obj}: classbeat_app privileges must be exactly {sorted(expected)}",
        )
        need(not _privileges(relations, "PUBLIC", obj), f"{obj}: PUBLIC must have no privilege")
        name, kind = obj.split(":")
        queue_expected = QUEUE_TABLE_PRIVILEGES.get(name, set()) if kind == "r" else set()
        need(
            _privileges(relations, QUEUE, obj) == (queue_expected if queue_exists else set()),
            f"{obj}: classbeat_queue privileges must be exactly {sorted(queue_expected)}",
        )
        need(_grantees(relations, obj) <= {APP, OWNER, QUEUE}, f"{obj}: unexpected grantee")
    need(
        not [1 for *_, grantable in relations if grantable], "no grantable privilege on a relation"
    )
    foreign_owned = conn.execute(
        text(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S') "
            "AND pg_get_userbyid(c.relowner) <> :owner"
        ),
        {"owner": OWNER},
    ).scalar_one()
    need(foreign_owned == 0, "every relation in public must be owned by classbeat_owner")
    column_rows = conn.execute(
        text(
            "SELECT c.relname, a.attname, "
            + GRANTEE.replace("a.grantee", "x.grantee")
            + ", x.privilege_type, x.is_grantable "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(a.attacl) x "
            "WHERE n.nspname = 'public' AND a.attacl IS NOT NULL"
        )
    ).all()
    found_columns: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for table, column, grantee, privilege, grantable in column_rows:
        found_columns.setdefault((str(table), str(column)), set()).add(
            (str(grantee), str(privilege))
        )
        need(not grantable, f"{table}.{column}: no grantable column privilege")
    expected_columns = {
        key: value
        for key, value in COLUMN_PRIVILEGES.items()
        if key[0] in {o.split(":")[0] for o, *_ in relations}
    }
    need(
        found_columns == expected_columns,
        f"column-level privileges in public must be exactly {expected_columns}, "
        f"are {found_columns}",
    )

    # Functions (in public, and user-made ones anywhere): PUBLIC may call none; classbeat_app
    # only the queue's SECURITY DEFINER functions owned by classbeat_queue (ADR 0005 section 3;
    # guard 9 checks them in full). 16384 is FirstNormalObjectId (review finding B2, #102).
    functions = _acl(
        conn,
        "SELECT p.oid::regprocedure::text || ':' || pg_get_userbyid(p.proowner) || ':' "
        "|| p.prosecdef::text, {G}, a.privilege_type, a.is_grantable "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, "
        "aclexplode(coalesce(p.proacl, acldefault(CAST('f' AS \"char\"), p.proowner))) a "
        "WHERE n.nspname = 'public' OR p.oid >= 16384",
    )
    need(
        "PUBLIC" not in _grantees(functions),
        "functions in public or made by a user must not be executable by PUBLIC",
    )
    queue_callable = {f"{sig}:{QUEUE}:true" for sig in QUEUE_SIGNATURES}
    for obj in sorted({o for o, *_ in functions}):
        allowed = {"EXECUTE"} if obj in queue_callable else set()
        need(
            _privileges(functions, APP, obj) == allowed
            or (obj in queue_callable and not _privileges(functions, APP, obj)),
            f"{obj}: classbeat_app may execute only the queue functions",
        )
        need(_grantees(functions, obj) <= {APP, OWNER, QUEUE}, f"{obj}: unexpected grantee")
        if obj.split(":")[0] in GUARD_TRIGGER_FUNCTIONS:
            need(
                _grantees(functions, obj) == {OWNER},
                f"{obj}: only classbeat_owner may execute a guard trigger function",
            )
    present = {o.split(":")[0] for o, *_ in functions}
    for signature in sorted(GUARD_TRIGGER_FUNCTIONS & present):
        need(
            f"{signature}:{OWNER}:false" in {o for o, *_ in functions},
            f"{signature}: must be owned by classbeat_owner and SECURITY INVOKER",
        )
    need(len(QUEUE_SIGNATURES) == len(QUEUE_FUNCTIONS), "QUEUE_SIGNATURES must list each function")
    languages = _acl(
        conn,
        "SELECT l.lanname, {G}, a.privilege_type, a.is_grantable FROM pg_language l, "
        "aclexplode(coalesce(l.lanacl, acldefault(CAST('l' AS \"char\"), l.lanowner))) a",
    )
    need(
        not [1 for o, g, *_ in languages if g in ("PUBLIC", APP) and o not in BUILTIN_LANGUAGES],
        "PUBLIC and classbeat_app may use only the built-in languages",
    )
    parameters = conn.execute(
        text(
            "SELECT count(*) FROM pg_parameter_acl p, aclexplode(p.paracl) a "
            "WHERE a.grantee = 0 OR a.grantee = CAST(:app AS regrole)"
        ),
        {"app": APP},
    ).scalar_one()
    need(parameters == 0, "no parameter privileges for PUBLIC or classbeat_app")

    # Default privileges: what the owner's future tables, sequences and functions get.
    defaults = [
        (str(r), str(k), str(s), str(g), str(p))
        for r, k, s, g, p in conn.execute(
            text(
                (
                    "SELECT pg_get_userbyid(d.defaclrole), d.defaclobjtype, "
                    "d.defaclnamespace::regnamespace::text, "
                    "{G}, a.privilege_type FROM pg_default_acl d, aclexplode(d.defaclacl) a"
                ).replace("{G}", GRANTEE)
            )
        ).all()
    ]
    expected_defaults = {("r", "public"): TABLE_PRIVILEGES, ("S", "public"): SEQUENCE_PRIVILEGES}
    for (kind, schema), privileges in expected_defaults.items():
        got = {p for r, k, s, g, p in defaults if (r, k, s, g) == (OWNER, kind, schema, APP)}
        need(
            got == privileges,
            f"default privileges on {kind} in {schema}: app must get {sorted(privileges)}",
        )
    function_defaults = [g for r, k, s, g, _ in defaults if (r, k, s) == (OWNER, "f", "-")]
    need(bool(function_defaults), "default privileges for functions must be set")
    need("PUBLIC" not in function_defaults, "new functions must not be executable by PUBLIC")
    allowed = {(OWNER, "r", "public", APP), (OWNER, "S", "public", APP)}
    unexpected = {(r, k, s, g) for r, k, s, g, _ in defaults if g in ("PUBLIC", APP)} - allowed
    need(not unexpected, f"unexpected default privileges: {sorted(unexpected)}")
    return problems
