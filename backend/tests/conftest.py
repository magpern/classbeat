"""Shared fixtures.

Database tests need a real Postgres: set ``CLASSBEAT_TEST_DATABASE_URL`` (see "Developer
commands" in AGENTS.md). Locally they skip with a reason when it is unset; in CI (``CI=true``)
a missing URL fails the run, so the database tests can never be skipped silently.
"""

import asyncio
import logging
import os
import socket
import sys
from collections.abc import Iterator

import pytest

if sys.platform == "win32":
    # psycopg's async mode does not run on the default Windows (proactor) event loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No CLASSBEAT_* setting leaks in from the machine running the tests."""
    for name in list(os.environ):
        if name.startswith("CLASSBEAT_") and name != "CLASSBEAT_TEST_DATABASE_URL":
            monkeypatch.delenv(name)


@pytest.fixture
def database_url() -> str:
    """URL of the throwaway test Postgres (``postgresql+psycopg://...``)."""
    url = os.environ.get("CLASSBEAT_TEST_DATABASE_URL")
    if url:
        return url
    reason = "CLASSBEAT_TEST_DATABASE_URL is not set (start a throwaway Postgres, see AGENTS.md)"
    if os.environ.get("CI") == "true":
        pytest.fail(reason + "; database tests must run in CI")
    pytest.skip(reason)


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
