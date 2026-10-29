"""Shared fixtures.

Database tests need a real Postgres: set ``CLASSBEAT_TEST_DATABASE_URL`` (see "Developer
commands" in AGENTS.md). Locally they skip with a reason when it is unset; in CI (``CI=true``)
a missing URL fails the run, so the database tests can never be skipped silently.
"""

import asyncio
import logging
import os
import secrets
import socket
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

import netguard
from authtools import TEST_KEK, Env
from smtpfake import SmtpFake

pytest_plugins = [
    "mfatools",
    "oauthtools",
    "mowlfake",
    "isofake",
    "jobcore",
    "jobapi",
    "optools",
    "designtools",
    "revisetools",
]  # + jobs core, jobs API, external operations

if sys.platform == "win32":
    # psycopg's async mode does not run on the default Windows (proactor) event loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No CLASSBEAT_* setting leaks in from the machine running the tests."""
    for name in list(os.environ):
        if name.startswith("CLASSBEAT_") and name != "CLASSBEAT_TEST_DATABASE_URL":
            monkeypatch.delenv(name)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "allow_network: may reach non-loopback hosts (unused)")


@pytest.fixture(autouse=True)
def _loopback_only(request: pytest.FixtureRequest) -> Iterator[None]:
    """No test sends a packet beyond loopback (``netguard``) unless it is marked allow_network.

    Its own ``MonkeyPatch``: a test calling ``monkeypatch.undo()`` cannot lift the guard."""
    if request.node.get_closest_marker("allow_network") is not None:
        yield
        return
    with pytest.MonkeyPatch.context() as guard:
        netguard.install(guard)
        yield


@pytest.fixture(autouse=True)
def _no_worker_allowlist_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that installs the worker's allowlist (ADR 0006 section 9) leaves neither its
    process-wide opener nor its worker flag to the tests after it."""
    import urllib.request

    from classbeat import egress

    monkeypatch.setattr(urllib.request, "_opener", None)
    monkeypatch.setattr(egress, "_worker_process", False)


@pytest.fixture(autouse=True)
def kek_dir(
    _isolated_environment: None,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """A secrets directory holding only ``connection_kek`` (the test KEK), so web, worker and the
    CLI find a valid current KEK. Tests that need other key files write them here."""
    directory = tmp_path_factory.mktemp("secrets")
    (directory / "connection_kek").write_bytes(TEST_KEK.hex().encode() + b"\n")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(directory))
    return directory


def _unavailable(reason: str) -> None:
    if os.environ.get("CI") == "true":
        pytest.fail(reason + "; database tests must run in CI")
    pytest.skip(reason)


@pytest.fixture
def database_url() -> str:
    """Superuser URL of the throwaway test Postgres (``postgresql+psycopg://...``).

    For test setup and probes only. Code under test connects as ``app_database_url`` or
    ``owner_database_url``.
    """
    url = os.environ.get("CLASSBEAT_TEST_DATABASE_URL")
    if not url:
        _unavailable(
            "CLASSBEAT_TEST_DATABASE_URL is not set (start a throwaway Postgres, see AGENTS.md)"
        )
    assert url is not None
    return url


@dataclass(frozen=True)
class RoleUrls:
    owner: str
    app: str


def url_for(admin_url: str, role: str, password: str) -> str:
    return (
        make_url(admin_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )


@pytest.fixture(scope="session")
def role_urls() -> RoleUrls:
    """URLs of ``classbeat_owner`` and ``classbeat_app``, whose roles ``docker/postgres/init``
    created. The passwords are replaced by random ones for this run."""
    admin_url = os.environ.get("CLASSBEAT_TEST_DATABASE_URL")
    if not admin_url:
        _unavailable("CLASSBEAT_TEST_DATABASE_URL is not set")
    assert admin_url is not None
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    passwords = {"classbeat_owner": secrets.token_hex(16), "classbeat_app": secrets.token_hex(16)}
    try:
        with engine.connect() as connection:
            found = (
                connection.execute(
                    text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
                    {"names": list(passwords)},
                )
                .scalars()
                .all()
            )
            if sorted(found) != sorted(passwords):
                _unavailable(
                    "the roles of docker/postgres/init/10-roles.sql do not exist in the test "
                    "database (run it first, see AGENTS.md)"
                )
            for role, password in passwords.items():
                connection.execute(text(f"ALTER ROLE {role} PASSWORD '{password}'"))
    finally:
        engine.dispose()
    return RoleUrls(
        owner=url_for(admin_url, "classbeat_owner", passwords["classbeat_owner"]),
        app=url_for(admin_url, "classbeat_app", passwords["classbeat_app"]),
    )


@pytest.fixture
def app_database_url(role_urls: RoleUrls) -> str:
    """URL of the app role, as web and worker connect."""
    return role_urls.app


@pytest.fixture
def owner_database_url(role_urls: RoleUrls) -> str:
    """URL of the owner role, as migrations connect."""
    return role_urls.owner


@pytest.fixture
def unreachable_database_url() -> str:
    """A URL whose port is closed on this machine, for the 'database is down' cases."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"postgresql+psycopg://classbeat:not-a-real-password@127.0.0.1:{port}/classbeat"


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """For tests that call ``configure_logging``: put the root logger back afterwards."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


@pytest.fixture
def owned_tables(owner_database_url: str) -> Iterator[None]:
    """Test-only owned parent and child tables (as a migration by the owner would make them)."""
    from dbtools import (
        CHILD,
        OWNED_TABLES_DDL,
        PARENT,
        drop_tables,
        owner_connection,
        run_rls_helper,
    )

    drop_tables(owner_database_url, CHILD, PARENT)
    with owner_connection(owner_database_url) as connection:
        for statement in OWNED_TABLES_DDL:
            connection.execute(text(statement))
        run_rls_helper(connection, PARENT)
        run_rls_helper(connection, CHILD)
    try:
        yield
    finally:
        drop_tables(owner_database_url, CHILD, PARENT)


@pytest.fixture(scope="module")
def auth_schema(role_urls: RoleUrls) -> Iterator[None]:
    """The migrated schema (all revisions) for the tests of one module."""
    from dbtools import migrated

    with migrated(role_urls.owner):
        yield


@pytest.fixture
def clean_auth(auth_schema: None, database_url: str) -> None:
    """The migrated schema with every auth table empty."""
    from dbtools import truncate_auth

    truncate_auth(database_url)


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator["Env"]:
    """A running app on the app role over an empty, migrated database."""
    from authtools import running_env

    async with running_env(app_database_url) as running:
        yield running


@pytest.fixture
async def smtp() -> AsyncIterator["SmtpFake"]:
    """An in-process SMTP server that keeps every message (ADR 0003 section 11)."""
    from smtpfake import SmtpFake

    async with SmtpFake() as fake:
        yield fake


@pytest.fixture
async def menv(clean_auth: None, app_database_url: str, smtp: "SmtpFake") -> AsyncIterator["Env"]:
    """A running app whose mail goes to the SMTP fake."""
    from authtools import running_env

    async with running_env(
        app_database_url, smtp_host="127.0.0.1", smtp_port=smtp.port, smtp_tls="none"
    ) as running:
        yield running
