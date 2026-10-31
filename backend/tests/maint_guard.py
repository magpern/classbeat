"""Guard 11 (ADR 0011 section 5): what ``classbeat_maint`` is and may touch, as an allowlist.

``check_maint`` returns a list of violations (empty when the database matches the ADR). It reads
the catalog through the superuser, independently of ``docker/postgres/maint/30-maint.sql`` (a
change there needs the same change here):

- the role: ``LOGIN NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT``, no
  memberships either way, owns nothing, exactly its four role settings;
- every privilege it holds anywhere (database, schemas, tables, columns, sequences, functions,
  languages, large objects and parameters through ``pg_shdepend``) is exactly ``EXPECTED``;
- no column it can read (``has_column_privilege``, so a table-level grant counts for every
  column, including one added later) is a secret one (``SECRET_COLUMNS`` or a secret-looking
  name), and every column it can read is listed in ``READABLE``;
- ``SECRET_COLUMNS`` names only columns that exist (no stale entry hides a renamed one).
"""

import ast
import re
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.engine import Connection

MAINT = "classbeat_maint"
EXPECTED_SETTINGS = {
    "statement_timeout=30s",
    "lock_timeout=5s",
    "idle_in_transaction_session_timeout=60s",
    "default_transaction_read_only=on",
}

# Columns that hold a secret, a credential, a hash of one, or the user's content: maint never
# reads them, whatever the grants say (ADR 0011 section 5's list).
SECRET_COLUMNS: dict[str, frozenset[str]] = {
    table: frozenset(columns.split())
    for table, columns in {
        "users": "password_hash",
        "sessions": "token_hash ip user_agent",
        "known_devices": "device_hash",
        "pending_signups": "token_hash password_hash browser_hash",
        "auth_tokens": "token_hash",
        "oauth_flows": "flow_hash state_hash nonce pkce_verifier return_to",
        "invites": "code_hash",
        "mfa_totp": "secret_ciphertext nonce wrapped_dek dek_nonce",
        "mfa_recovery_codes": "code_hash",
        "rate_limits": "bucket_hash",
        "auth_events": "ip detail",
        "connections": "public secret secret_nonce wrapped_dek dek_nonce",
        "connection_keks": "check_value",
        "spotify_oauth_flows": "state_hash session_hash client_id verifier redirect_uri",
        "spotify_pool": "spotify_user_id playlist_id last_hash",
        "jobs": "input idempotency_key request_hash approval_hash leased_by",
        "job_events": "payload",
        "external_operations": "idempotency_key request_hash external_id result request_context",
        "classes": "title brief music_source",
        "class_revisions": "content content_hash music provenance",
        "class_messages": "body change",
        "saved_workouts": "name program_id playlist_id content_hash state_hash",
        "class_playlists": "playlist_id spotify_user_id name content_hash",
        "mowl_playlist_links": "source_id playlist_id",
        "redeem_codes": "code_hash",
        "admin_audit": "ip_hash",
    }.items()
}
# Names that are secret wherever they appear (a new table's token or hash is caught unlisted).
SECRET_NAME = re.compile(
    r"(^|_)(hash|secret|nonce|verifier|ciphertext|token|password|dek|pkce)(_|$)|_hash$"
)

# Exactly what maint holds: table privileges (``*`` = table level) and column privileges.
_COLUMNS = {
    "users": "id email email_normalized email_verified_at role disabled_at created_at",
    "sessions": "id user_id created_at last_seen_at expires_at",
    "invites": "id created_by used_by created_at expires_at used_at",
    "plans": "id name rank classes_per_month revisions_per_class concurrent_jobs "
    "monthly_cost_micros route_key features created_at updated_at updated_by",
    "redeem_codes": "id label plan_id features grant_days max_redemptions redeemed_count "
    "expires_at disabled_at created_by created_at",
    "entitlements": "id owner_id plan_id features source starts_at ends_at code_id granted_by "
    "note revoked_at revoked_by created_at",
    "code_redemptions": "id owner_id code_id entitlement_id created_at",
    "credit_ledger": "id owner_id job_id period plan_id state reason settled_at refunded_by "
    "created_at",
    "app_settings": "key value created_at updated_at updated_by",
    "admin_audit": "id created_at actor_id actor_email action target_user_id target_kind "
    "target_id params outcome reason request_id",
    "connections": "id owner_id provider status last_error connected_at updated_at last_used_at",
    "agent_runs": "id owner_id job_id stage attempt provider model model_answered effort "
    "prompt_version turns tool_calls input_tokens output_tokens cache_read_tokens "
    "cache_write_tokens cost_micros outcome started_at finished_at job_kind price_version",
    "jobs": "id owner_id kind state stage error_category credit_state expires_at approved_at "
    "created_at updated_at started_at finished_at",
    "external_operations": "id owner_id job_id kind state error_category created_at updated_at "
    "resolved_at pending_at call_ended_at",
    "model_eval_results": "id run_id recorded_at suite_version manifest_digest provider model "
    "effort stage_group prompt_versions classbeat_version results_sha256 verdict passed "
    "total_cost_micros authorization_note seq",
    "model_routes": "route_key stage_group provider model effort eval_result_id gate_passed "
    "set_at set_by",
}
READABLE: dict[str, frozenset[str]] = {t: frozenset(c.split()) for t, c in _COLUMNS.items()}
_UPDATES = {
    "users": "disabled_at",
    "invites": "expires_at",
    "plans": "classes_per_month revisions_per_class concurrent_jobs monthly_cost_micros "
    "updated_at updated_by",
    "redeem_codes": "disabled_at",
    "entitlements": "revoked_at revoked_by",
    "credit_ledger": "state reason settled_at refunded_by",
}
# (table, privilege) -> the columns (column grants) or {"*"} (a table-level grant).
EXPECTED: dict[tuple[str, str], frozenset[str]] = {
    **{(t, "SELECT"): cols for t, cols in READABLE.items()},
    **{(t, "UPDATE"): frozenset(c.split()) for t, c in _UPDATES.items()},
    ("users", "DELETE"): frozenset({"*"}),
    ("sessions", "DELETE"): frozenset({"*"}),
    ("invites", "INSERT"): frozenset({"*"}),
    ("redeem_codes", "INSERT"): frozenset({"*"}),
    ("entitlements", "INSERT"): frozenset({"*"}),
    ("app_settings", "INSERT"): frozenset({"*"}),
    ("app_settings", "UPDATE"): frozenset({"*"}),
    ("admin_audit", "INSERT"): frozenset({"*"}),
    # AD5 #223: the routes are written whole; the results are never writable (no entry).
    ("model_routes", "INSERT"): frozenset({"*"}),
    ("model_routes", "UPDATE"): frozenset({"*"}),
    ("model_routes", "DELETE"): frozenset({"*"}),
}
# Everything else maint may hold, as pg_shdepend-free ACL entries: CONNECT and USAGE.
EXPECTED_OTHER = {"database:CONNECT", "schema:public:USAGE"}


def is_secret(table: str, column: str) -> bool:
    return column in SECRET_COLUMNS.get(table, frozenset()) or bool(SECRET_NAME.search(column))


def check_maint(conn: Connection) -> list[str]:
    problems: list[str] = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    role = conn.execute(
        text(
            "SELECT oid, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, "
            "rolreplication, rolinherit FROM pg_roles WHERE rolname = :r"
        ),
        {"r": MAINT},
    ).first()
    if role is None:
        return [f"the role {MAINT} does not exist (run docker/postgres/maint/30-maint.sql)"]
    need(
        tuple(role[1:]) == (True, False, True, False, False, False, False),
        f"{MAINT} must be LOGIN NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION "
        "NOINHERIT",
    )
    members = conn.execute(
        text("SELECT count(*) FROM pg_auth_members WHERE :o IN (member, roleid)"), {"o": role.oid}
    ).scalar_one()
    need(members == 0, f"{MAINT} must be a member of no role and have no members")
    settings = conn.execute(
        text("SELECT setdatabase, setconfig FROM pg_db_role_setting WHERE setrole = :o"),
        {"o": role.oid},
    ).all()
    need([s.setdatabase for s in settings] in ([], [0]), f"{MAINT}: no per-database settings")
    found_settings = {item for s in settings for item in s.setconfig}
    need(
        found_settings == EXPECTED_SETTINGS,
        f"{MAINT} role settings must be exactly {sorted(EXPECTED_SETTINGS)}",
    )
    owned = conn.execute(
        text("SELECT count(*) FROM pg_shdepend WHERE refobjid = :o AND deptype = 'o'"),
        {"o": role.oid},
    ).scalar_one()
    need(owned == 0, f"{MAINT} must own nothing")

    # Table-level privileges (relacl) and column-level ones (attacl), in schema public.
    held: dict[tuple[str, str], set[str]] = defaultdict(set)
    for table, privilege, grantable in conn.execute(
        text(
            "SELECT c.relname, a.privilege_type, a.is_grantable FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a "
            "WHERE n.nspname = 'public' AND a.grantee = CAST(:r AS regrole)"
        ),
        {"r": MAINT},
    ):
        held[(table, privilege)].add("*")
        need(not grantable, f"{table}: no grantable privilege for {MAINT}")
    for table, column, privilege, grantable in conn.execute(
        text(
            "SELECT c.relname, t.attname, a.privilege_type, a.is_grantable FROM pg_attribute t "
            "JOIN pg_class c ON c.oid = t.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace, "
            "aclexplode(t.attacl) a "
            "WHERE n.nspname = 'public' AND a.grantee = CAST(:r AS regrole)"
        ),
        {"r": MAINT},
    ):
        held[(table, privilege)].add(column)
        need(not grantable, f"{table}.{column}: no grantable privilege for {MAINT}")
    for key in sorted(set(held) | set(EXPECTED)):
        need(
            held.get(key, set()) == set(EXPECTED.get(key, frozenset())),
            f"{key[0]}: {MAINT} {key[1]} must be exactly "
            f"{sorted(EXPECTED.get(key, frozenset()))}, is {sorted(held.get(key, set()))}",
        )

    # Every dependency on the role outside those tables: only CONNECT and USAGE on public.
    other: set[str] = set()
    for kind, name, privilege in conn.execute(
        text(
            "SELECT 'database', d.datname, a.privilege_type FROM pg_database d, "
            "aclexplode(d.datacl) a WHERE a.grantee = CAST(:r AS regrole) "
            "UNION ALL SELECT 'schema', n.nspname, a.privilege_type FROM pg_namespace n, "
            "aclexplode(n.nspacl) a WHERE a.grantee = CAST(:r AS regrole) "
            "UNION ALL SELECT 'function', p.oid::regprocedure::text, a.privilege_type "
            "FROM pg_proc p, aclexplode(p.proacl) a WHERE a.grantee = CAST(:r AS regrole) "
            "UNION ALL SELECT 'relation', n.nspname || '.' || c.relname, a.privilege_type "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, "
            "aclexplode(c.relacl) a WHERE a.grantee = CAST(:r AS regrole) "
            "AND (n.nspname <> 'public' OR c.relkind NOT IN ('r', 'p'))"
        ),
        {"r": MAINT},
    ):
        if kind == "database":
            other.add(f"database:{privilege}" if name == _db(conn) else f"database {name}")
        else:
            other.add(f"{kind}:{name}:{privilege}")
    need(other == EXPECTED_OTHER, f"{MAINT} must hold exactly {sorted(EXPECTED_OTHER)} besides "
         f"its table privileges, holds {sorted(other)}")  # fmt: skip
    # Any other ACL that names maint (types, languages, large objects, parameters, foreign
    # servers, other databases' objects), through the shared dependency catalog.
    foreign = conn.execute(
        text(
            "SELECT count(*) FROM pg_shdepend WHERE refobjid = :o AND deptype = 'a' AND ("
            "dbid NOT IN (0, (SELECT oid FROM pg_database WHERE datname = current_database())) "
            "OR classid NOT IN (CAST('pg_class' AS regclass), CAST('pg_namespace' AS regclass), "
            "CAST('pg_database' AS regclass), CAST('pg_proc' AS regclass)))"
        ),
        {"o": role.oid},
    ).scalar_one()
    need(foreign == 0, f"{MAINT} holds a privilege outside tables, public and the database")

    # What maint can actually read, column by column, against the registries.
    columns: dict[str, set[str]] = defaultdict(set)
    for table, column, readable in conn.execute(
        text(
            "SELECT c.relname, a.attname, "
            "has_column_privilege(CAST(:r AS regrole), c.oid, a.attnum, 'SELECT') "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND a.attnum > 0 "
            "AND NOT a.attisdropped"
        ),
        {"r": MAINT},
    ):
        columns[table].add(column)
        if not readable:
            continue
        need(not is_secret(table, column), f"{table}.{column}: {MAINT} can read a secret column")
        need(
            column in READABLE.get(table, frozenset()),
            f"{table}.{column}: {MAINT} can read a column no registry decision covers",
        )
    for table, secret in sorted(SECRET_COLUMNS.items()):
        for column in sorted(secret - columns.get(table, set())):
            if table in columns:
                problems.append(f"{table}.{column}: SECRET_COLUMNS names a column that is gone")
    return problems


def _db(conn: Connection) -> str:
    return str(conn.execute(text("SELECT current_database()")).scalar_one())


# ADR 0011 section 5: the read-only switch does not stop NOTIFY, pg_notify or the advisory-lock
# functions (they succeed in a read-only transaction), so code under ``classbeat.admin`` must
# never name them. Checked in the source: every string that is not a docstring, and every name
# and attribute (``func.pg_notify``, ``sqlalchemy.func.pg_advisory_xact_lock``).
FORBIDDEN_SQL = re.compile(r"\bnotify\b|\bpg_notify\b|\bpg_(try_)?advisory\w*|\blisten\b", re.I)


def forbidden_calls(source: str) -> list[tuple[int, str]]:
    """``(line, what)`` for each NOTIFY, LISTEN, pg_notify or advisory-lock use in ``source``."""
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        value = (
            node.value
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            else node.attr
            if isinstance(node, ast.Attribute)
            else node.id
            if isinstance(node, ast.Name)
            else node.name
            if isinstance(node, ast.alias)
            else None
        )
        if isinstance(value, str):
            match = FORBIDDEN_SQL.search(value)
            if match:
                found.append((getattr(node, "lineno", 0), match.group(0)))
    return found
