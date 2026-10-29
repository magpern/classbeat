"""No user code can run as ``classbeat_queue`` (review finding B2, #102; ADR 0005 section 3).

``classbeat_owner`` (a migration) can create a schema, and it owns ``jobs``, ``job_events`` and
``external_operations``, so it can attach a trigger, a rule or a column default to them. A
trigger function needs no EXECUTE when it fires, and inside a queue function it runs with
``current_user = classbeat_queue`` (BYPASSRLS). Each drift case below is planted as the owner, as
a migration would, and must be found by guard 9, refused by the startup check and on every new
connection, and stop ``20-queue.sql`` naming the object. The script cannot repair these (dropping
them could lose data), so each case is undone by hand and the script then installs cleanly.
"""

import re
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from dbtools import install_queue
from jobtools import engine_for, new_job, new_user
from queue_guard import check_queue
from role_guard import check_roles
from test_queue_guard import startup_and_connection_problems

REFUSED = "job queue does not match ADR 0005"
SCHEMA = "CREATE SCHEMA t_evil"
DROP_SCHEMA = "DROP SCHEMA IF EXISTS t_evil CASCADE"
TRIGGER_FN = (
    "CREATE FUNCTION t_evil.trg() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
    "IF NEW.state = 'running' THEN NEW.leased_by := 'pwn-' || current_user; END IF; "
    "RETURN NEW; END $$"
)
TS_FN = (
    "CREATE FUNCTION t_evil.ts() RETURNS timestamptz LANGUAGE sql AS "
    "$$ SELECT CASE WHEN current_user = 'classbeat_queue' "
    "THEN now() + interval '100 years' ELSE now() END $$"
)
RESTORE_DEFAULT = "ALTER TABLE public.job_events ALTER created_at SET DEFAULT now()"

# (plant as classbeat_owner, undo as classbeat_owner, what guard 9 and the checks must name,
#  what the script must name).
HOOK_DRIFT = [
    pytest.param(
        [
            SCHEMA,
            TRIGGER_FN,
            "CREATE TRIGGER zz_evil BEFORE UPDATE ON public.jobs "
            "FOR EACH ROW EXECUTE FUNCTION t_evil.trg()",
        ],
        [DROP_SCHEMA],
        "zz_evil",
        "trigger zz_evil on",
        id="trigger-from-another-schema",
    ),
    pytest.param(
        [
            SCHEMA,
            TS_FN,
            "GRANT EXECUTE ON FUNCTION t_evil.ts() TO PUBLIC",
            "ALTER TABLE public.job_events ALTER created_at SET DEFAULT t_evil.ts()",
        ],
        [RESTORE_DEFAULT, DROP_SCHEMA],
        "t_evil.ts()",
        "function t_evil.ts()",
        id="column-default-public-execute",
    ),
    pytest.param(
        [SCHEMA, TS_FN, "GRANT EXECUTE ON FUNCTION t_evil.ts() TO PUBLIC"],
        [DROP_SCHEMA],
        "t_evil.ts()",
        "function t_evil.ts()",
        id="public-execute-outside-public",
    ),
    pytest.param(
        [SCHEMA, "CREATE AGGREGATE t_evil.total(int) (SFUNC = int4pl, STYPE = int)"],
        [DROP_SCHEMA],
        "t_evil.total(integer)",
        "function t_evil.total(integer)",
        id="aggregate-of-built-ins",
    ),
    pytest.param(
        [
            "CREATE TRIGGER zz_builtin BEFORE UPDATE ON public.external_operations FOR EACH ROW "
            "EXECUTE FUNCTION pg_catalog.suppress_redundant_updates_trigger()",
        ],
        ["DROP TRIGGER zz_builtin ON public.external_operations"],
        "zz_builtin",
        "trigger zz_builtin on",
        id="trigger-of-a-built-in",
    ),
    pytest.param(
        [
            "CREATE CONSTRAINT TRIGGER zz_constraint AFTER INSERT ON public.job_events "
            "FOR EACH ROW EXECUTE FUNCTION pg_catalog.suppress_redundant_updates_trigger()",
        ],
        ["DROP TRIGGER zz_constraint ON public.job_events"],
        "zz_constraint",
        "trigger zz_constraint on",
        id="user-constraint-trigger",
    ),
    pytest.param(
        ["CREATE RULE zz_rule AS ON UPDATE TO public.jobs DO ALSO NOTIFY t_evil"],
        ["DROP RULE zz_rule ON public.jobs"],
        "zz_rule",
        "rule zz_rule on",
        id="rule",
    ),
    pytest.param(  # #108 item 2: a user-defined operator outside public, over a built-in
        [SCHEMA, "CREATE OPERATOR t_evil.=== (LEFTARG = int, RIGHTARG = int, FUNCTION = int4eq)"],
        [DROP_SCHEMA],
        "operator t_evil.===(integer, integer)",
        "operator t_evil.===(integer, integer)",
        id="operator-outside-public",
    ),
    # #108 item 1: built-ins that run SQL text, reachable from an expression a queue write
    # evaluates as classbeat_queue (read-only, but past RLS).
    pytest.param(
        [
            "ALTER TABLE public.job_events ALTER created_at SET DEFAULT now() + "
            "length(query_to_xml('SELECT count(*) FROM public.jobs', true, false, '')::text) "
            "* interval '0 seconds'"
        ],
        [RESTORE_DEFAULT],
        "built-in query_to_xml in the default of",
        "built-in query_to_xml in the default of public.job_events.created_at",
        id="default-runs-sql-text",
    ),
    pytest.param(
        [
            "ALTER TABLE public.jobs ADD CONSTRAINT zz_sql CHECK "
            "(ts_rewrite('a'::tsquery, 'SELECT ''a''::tsquery, ''b''::tsquery') IS NOT NULL) "
            "NOT VALID"
        ],
        ["ALTER TABLE public.jobs DROP CONSTRAINT zz_sql"],
        "built-in ts_rewrite in constraint zz_sql",
        "built-in ts_rewrite in constraint zz_sql on public.jobs",
        id="check-runs-sql-text",
    ),
    pytest.param(
        [
            SCHEMA,
            "CREATE DOMAIN t_evil.d AS text "
            "DEFAULT table_to_xml('public.jobs', true, false, '')::text",
        ],
        [DROP_SCHEMA],
        "built-in table_to_xml in the default of type t_evil.d",
        "built-in table_to_xml in the default of type t_evil.d",
        id="domain-default-reads-a-table",
    ),
    pytest.param(
        [
            "ALTER TABLE public.job_events ALTER created_at SET DEFAULT "
            "now() + length(set_config('search_path', 'public', true)) * interval '0 seconds'"
        ],
        [RESTORE_DEFAULT],
        "built-in set_config in the default of",
        "built-in set_config in the default of public.job_events.created_at",
        id="default-changes-settings",
    ),
]


@pytest.fixture
def admin(auth_schema: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def run_as_owner(owner_database_url: str, statements: list[str]) -> None:
    with (
        engine_for(owner_database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        for statement in statements:
            connection.execute(text(statement))


@pytest.mark.parametrize(("plant", "undo", "subject", "script_names"), HOOK_DRIFT)
async def test_user_code_reachable_from_the_queue_is_found_and_refused(
    admin: Connection,
    database_url: str,
    owner_database_url: str,
    app_database_url: str,
    plant: list[str],
    undo: list[str],
    subject: str,
    script_names: str,
) -> None:
    assert check_queue(admin) == []
    run_as_owner(owner_database_url, plant)
    try:
        found = check_queue(admin)
        assert any(subject in problem for problem in found), found
        startup, connection = await startup_and_connection_problems(app_database_url)
        for message in (startup, connection):
            assert REFUSED in message, message
            assert subject in message, message
        with pytest.raises(Exception, match=re.escape(script_names)):
            install_queue(database_url)
        if "PUBLIC" in " ".join(plant):  # guard 2 now sees functions outside public too
            assert any("PUBLIC" in problem for problem in check_roles(admin))
    finally:
        run_as_owner(owner_database_url, undo)
    install_queue(database_url)
    assert check_queue(admin) == []
    assert check_roles(admin) == []
    assert await startup_and_connection_problems(app_database_url) == ("", "")


async def test_the_planted_trigger_would_run_as_the_queue_role_and_the_worker_refuses_first(
    clean_auth: None,
    admin: Connection,
    database_url: str,
    owner_database_url: str,
    app_database_url: str,
) -> None:
    """The repro of B2: the plant is real (an unchecked connection shows it ran as
    classbeat_queue), and the checked engine web and worker use refuses before any claim."""
    owner = new_user(admin)
    job_id = new_job(admin, owner)
    plant = HOOK_DRIFT[0].values[0]
    assert isinstance(plant, list)
    run_as_owner(owner_database_url, [str(s) for s in plant])
    try:
        startup, connection = await startup_and_connection_problems(app_database_url)
        assert "zz_evil" in startup
        assert "zz_evil" in connection
        with engine_for(app_database_url) as app, app.begin() as tx:
            [row] = tx.execute(
                text("SELECT * FROM public.queue_claim('w1', 60, ARRAY['test_kind'])")
            ).all()
            assert row.job_id == job_id
        leased_by = admin.execute(
            text("SELECT leased_by FROM public.jobs WHERE id = :id"), {"id": job_id}
        ).scalar_one()
        assert leased_by == "pwn-classbeat_queue"
    finally:
        run_as_owner(owner_database_url, [DROP_SCHEMA])
    install_queue(database_url)
    assert check_queue(admin) == []


async def test_foreign_key_triggers_views_and_empty_schemas_are_not_refused(
    admin: Connection, database_url: str, owner_database_url: str, app_database_url: str
) -> None:
    """What is legitimate stays clean: the internal triggers of foreign keys (the job tables
    have them), a view's _RETURN rule and a schema without functions."""
    internal = admin.execute(
        text(
            "SELECT count(*) FROM pg_trigger WHERE tgisinternal "
            "AND tgrelid IN ('public.jobs'::regclass, 'public.job_events'::regclass, "
            "'public.external_operations'::regclass)"
        )
    ).scalar_one()
    assert internal > 0
    run_as_owner(
        owner_database_url,
        ["CREATE SCHEMA t_plain", "CREATE VIEW t_plain.v AS SELECT 1 AS one"],
    )
    try:
        assert check_queue(admin) == []
        assert await startup_and_connection_problems(app_database_url) == ("", "")
        install_queue(database_url)
    finally:
        run_as_owner(owner_database_url, ["DROP SCHEMA t_plain CASCADE"])
    assert check_queue(admin) == []
