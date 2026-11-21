"""The in-process fake spotdown (contract v2, ADR 0040; SD-P4, #1301) over an httpx transport: no
socket, no network, deterministic. Public test values only.

The fake itself is ``spotdowncore`` (what it answers, ``fake.end``, ``fake.ends_by_source``,
``fake.documents``, ``fake.inject`` and the rest are described there); the e2e stack runs the same
code over HTTP (``e2e/fake-spotdown``, #1337), so the two cannot drift. This module is the pytest
side: the ``spotdown`` fixture, which puts the fake behind ``classbeat.spotdown.opener``, and the
names the tests import (re-exported from the core).
"""

from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from classbeat import egress_spotdown
from classbeat.spotdown import opener
from spotdowncore import (
    CONTRACTS,
    ENDS,
    MATCH_STATUSES,
    ORIGIN,
    SCOPE_A,
    SCOPE_B,
    SERVICE_KEY,
    FakeSpotdown,
    Seen,
    evidence_of,
    example,
    explicit_group,
    transport,
)

__all__ = [
    "CONTRACTS",
    "ENDS",
    "MATCH_STATUSES",
    "ORIGIN",
    "SCOPE_A",
    "SCOPE_B",
    "SERVICE_KEY",
    "FakeSpotdown",
    "Seen",
    "evidence_of",
    "example",
    "explicit_group",
    "spotdown",
    "transport",
]


@pytest.fixture
def spotdown(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSpotdown]:
    """The fake behind ``classbeat.spotdown.opener`` (the production builder, through
    ``spotdown_test_client``, over the fake transport), with spotdown installed for the test."""
    fake = FakeSpotdown()
    key = SecretStr(SERVICE_KEY)

    def open_client(total_seconds: float) -> httpx.AsyncClient:
        return egress_spotdown.spotdown_test_client(
            ORIGIN, key, transport(fake), total_seconds=total_seconds
        )

    def open_upload_client() -> httpx.AsyncClient:
        return egress_spotdown.spotdown_test_client(ORIGIN, key, transport(fake), upload=True)

    egress_spotdown.install_spotdown(ORIGIN, key)
    monkeypatch.setattr(opener, "open_client", open_client)
    monkeypatch.setattr(opener, "open_upload_client", open_upload_client)
    try:
        yield fake
    finally:
        egress_spotdown.install_spotdown(None, None)
