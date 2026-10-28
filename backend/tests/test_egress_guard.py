"""The ADR 0006 §9 guard (``egress_scan``) over the whole source tree, and proof it can fail."""

from pathlib import Path

import pytest

from egress_scan import (
    EXEMPT,
    WORKER_PACKAGES,
    exempt_calls_used,
    imported_modules,
    scan_source,
)

SRC = Path(__file__).resolve().parent.parent / "src"


def _module(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _sources() -> list[tuple[str, str]]:
    return [
        (_module(p), p.read_text(encoding="utf-8"))
        for p in sorted((SRC / "classbeat").rglob("*.py"))
    ]


def test_no_module_builds_an_outbound_client_outside_the_allowlist() -> None:
    violations = [
        (module, line, why)
        for module, source in _sources()
        for line, why in scan_source(source, module)
    ]
    assert violations == []


def test_every_exemption_is_still_used() -> None:
    sources = dict(_sources())
    for module, calls in EXEMPT.items():
        assert module in sources, f"{module} no longer exists; drop its exemption"
        assert exempt_calls_used(sources[module], module) == set(calls), module


def test_worker_code_never_imports_an_exempt_module() -> None:
    for module, source in _sources():
        if not module.startswith(WORKER_PACKAGES):
            continue
        imported = imported_modules(source, module)
        for exempt in EXEMPT:
            assert not any(n == exempt or n.startswith(exempt + ".") for n in imported), (
                module,
                exempt,
            )


def test_the_allowlist_module_itself_is_scanned_as_allowed() -> None:
    source = (SRC / "classbeat" / "egress.py").read_text(encoding="utf-8")
    assert scan_source(source, "classbeat.egress") == []
    assert scan_source(source, "classbeat.worker.loop") != []  # it does build clients


M = "classbeat.agent.providers.anthropic"

REFUSED = [
    "import urllib.request\nurllib.request.urlopen('https://x')",
    "from urllib import request\nrequest.urlopen('https://x')",
    "from urllib.request import urlopen as u\nu('https://x')",
    "import urllib.request as r\nr.build_opener()",
    "import urllib.request\nurllib.request.install_opener(None)",
    "from urllib.request import OpenerDirector\nOpenerDirector()",
    "from urllib.request import HTTPSHandler\nHTTPSHandler()",
    "import httpx\nhttpx.AsyncClient()",
    "import httpx as h\nh.Client()",
    "from httpx import AsyncClient as C\nC(trust_env=False)",
    "import httpx\nhttpx.AsyncHTTPTransport()",
    "import httpx\nhttpx.get('https://x')",
    "import socket\nsocket.create_connection(('x', 443))",
    "import socket\nsocket.socket()",
    "import smtplib\nsmtplib.SMTP('x')",
    "import asyncio\nasyncio.open_connection('x', 443)",
    "from authlib.integrations.httpx_client import AsyncOAuth2Client\nAsyncOAuth2Client()",
    "import anthropic\nanthropic.AsyncAnthropic(api_key='k')",
    "from anthropic import AsyncAnthropic\nAsyncAnthropic()",
    "from anthropic import DefaultAsyncHttpxClient\nDefaultAsyncHttpxClient()",
    "import openai\nopenai.AsyncOpenAI()",
    "import requests",
    "import aiohttp",
    "from urllib3 import PoolManager",
    "import http.client",
    "from http import client",
    "from http.client import HTTPSConnection",
    "import httpcore",
    "import importlib\nimportlib.import_module('requests')",
    "__import__('aiohttp')",
]


@pytest.mark.parametrize("source", REFUSED)
def test_the_guard_refuses(source: str) -> None:
    assert scan_source(source, M) != []


ALLOWED = [
    "from classbeat.egress import allowlisted_async_client\nallowlisted_async_client()",
    "import httpx\nx: httpx.AsyncBaseTransport\nhttpx.Timeout(1.0)\nhttpx.MockTransport(f)",
    "import anthropic\nanthropic.AsyncAnthropic(api_key=k, http_client=c)",
    "from anthropic import AsyncAnthropic\nAsyncAnthropic(http_client=client, max_retries=2)",
    "import socket\nexcept_types = (socket.timeout,)",
    "from urllib.error import HTTPError, URLError",
    "from urllib.parse import urlsplit\nurlsplit('https://x')",
    "import urllib.request\nurllib.request.Request('https://x')",
]


@pytest.mark.parametrize("source", ALLOWED)
def test_the_guard_allows(source: str) -> None:
    assert scan_source(source, M) == []


def test_an_exemption_is_per_module_and_per_call() -> None:
    call = "import httpx\nhttpx.AsyncClient()"
    assert scan_source(call, "classbeat.auth.oauth_client") == []
    assert scan_source(call, "classbeat.auth.routes") != []
    other = "import httpx\nhttpx.Client()"
    assert scan_source(other, "classbeat.auth.oauth_client") != []
