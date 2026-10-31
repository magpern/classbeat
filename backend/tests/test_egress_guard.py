"""The ADR 0006 §9 guard (``egress_scan``) over the whole source tree, and proof it can fail."""

from pathlib import Path

import pytest

from egress_scan import (
    EXEMPT,
    WORKER_ENTRYPOINTS,
    WORKER_PACKAGES,
    exempt_calls_used,
    imported_modules,
    loaded_exempt_modules,
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


def test_the_worker_process_never_loads_an_exempt_module() -> None:
    """Reachability, not only direct imports: a fresh interpreter imports every worker entry
    point; no exempt module (OAuth sign-in, mail) or other client library may be loaded."""
    assert loaded_exempt_modules(WORKER_ENTRYPOINTS) == []


@pytest.mark.parametrize(
    "path",
    [
        "classbeat.auth.setup",  # the auth package's web half
        "classbeat.auth.oauth_client",
        "classbeat.mail",
        "classbeat.auth.flowtools",  # reaches mail through a helper module
    ],
)
def test_the_reachability_check_fails_when_a_worker_path_reaches_an_exempt_module(
    path: str,
) -> None:
    assert loaded_exempt_modules((*WORKER_ENTRYPOINTS, path)) != []


def test_importing_the_auth_package_loads_no_web_module() -> None:
    assert loaded_exempt_modules(("classbeat.auth", "classbeat.auth.purge")) == []


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
    "from anthropic import AsyncAnthropic\nAsyncAnthropic(http_client=None)",
    "from anthropic import AsyncAnthropic\nAsyncAnthropic(http_client=self.client)",
    "import anthropic, httpx\nanthropic.AsyncAnthropic(http_client=httpx.AsyncClient())",
    "import xmlrpc.client",
    "import imaplib",
    "import poplib",
    "import ssl\nssl.get_server_certificate(('x', 443))",
    "loop.create_connection(factory, 'x', 443)",
    "async def f():\n    await loop.sock_connect(sock, ('x', 443))",
    "async def f():\n    await asyncio.get_running_loop().create_connection(f, 'x', 443)",
    "import httpx\nclass C(httpx.AsyncClient): pass",
    "from httpx import Client\nclass C(Client): pass",
    "import httpx\nclass T(httpx.AsyncBaseTransport): pass",
    "import urllib.request\nclass H(urllib.request.BaseHandler): pass",
    # ADR 0009 §10: allowlists are built and chosen in classbeat.egress only.
    "from classbeat.egress import Allowlist\nAllowlist(frozenset({'evil.example'}))",
    "from classbeat import egress\negress.Allowlist(hosts)",
    "import classbeat.egress as e\ne.AllowlistTransport(e.SPOTIFY_ALLOWLIST, inner)",
    "from classbeat.egress import build_allowlist_opener\nbuild_allowlist_opener()",
    "from classbeat.egress import DeadlineHTTPSHandler\nDeadlineHTTPSHandler()",
    "from classbeat.egress import spotify_network_transport\nspotify_network_transport()",
    "from classbeat.egress import model_network_transport\nmodel_network_transport()",
    "from classbeat.egress import allowlisted_async_client, WORKER_ALLOWLIST\n"
    "allowlisted_async_client(WORKER_ALLOWLIST)",
    "from classbeat.egress import allowlisted_async_client\nallowlisted_async_client(allowlist=a)",
    "from classbeat.egress import allowlisted_async_client\nallowlisted_async_client(**options)",
    # ADR 0012 §2: a caller names its provider; it never hands in a list, also not by position.
    "from classbeat.egress import allowlisted_async_client, MODEL_ALLOWLISTS\n"
    "allowlisted_async_client(MODEL_ALLOWLISTS['openai'])",
    "from classbeat.egress import allowlisted_async_client, MODEL_ALLOWLISTS\n"
    "allowlisted_async_client(provider='openai', allowlist=MODEL_ALLOWLISTS['deepseek'])",
    "from classbeat.egress import Allowlist\nAllowlist(frozenset({'api.openai.com', 'x.example'}))",
    "import openai\nopenai.AsyncOpenAI(api_key=k, base_url='https://api.deepseek.com')",
    "from openai import AsyncOpenAI\nAsyncOpenAI(http_client=None)",
    "from classbeat.egress import Allowlist\nclass Wider(Allowlist): pass",
    "from classbeat.egress import AllowlistTransport\nclass T(AllowlistTransport): pass",
    "import httpx\nhttpx.AsyncClient(transport=t, trust_env=False)",
]


@pytest.mark.parametrize("source", REFUSED)
def test_the_guard_refuses(source: str) -> None:
    assert scan_source(source, M) != []


ALLOWED = [
    "from classbeat.egress import allowlisted_async_client\nallowlisted_async_client()",
    "import httpx\nx: httpx.AsyncBaseTransport\nhttpx.Timeout(1.0)\nhttpx.MockTransport(f)",
    "import anthropic\nanthropic.AsyncAnthropic(api_key=k, http_client=c)",
    "from anthropic import AsyncAnthropic\nAsyncAnthropic(http_client=client, max_retries=2)",
    "from anthropic import AsyncAnthropic\nfrom classbeat.egress import allowlisted_async_client\n"
    "AsyncAnthropic(http_client=allowlisted_async_client())",
    "class Fine(Exception): pass",
    "import socket\nexcept_types = (socket.timeout,)",
    "from urllib.error import HTTPError, URLError",
    "from urllib.parse import urlsplit\nurlsplit('https://x')",
    "import urllib.request\nurllib.request.Request('https://x')",
    "from classbeat.egress import spotify_async_client\nspotify_async_client()",
    "from classbeat.egress import spotify_async_client\nspotify_async_client(total_seconds=5.0)",
    "from classbeat import egress\negress.spotify_async_client(total_seconds=t, inner=n)",
    "from classbeat.egress import allowlisted_async_client\n"
    "allowlisted_async_client(timeout=1.0, inner=t, refused=E)",
    # ADR 0012 §2: the OpenAI-SDK clients of P1 to P3 get their provider's one-host client.
    "from classbeat.egress import allowlisted_async_client\n"
    "allowlisted_async_client(provider='openai', timeout=1.0, inner=t, refused=E)",
    "from openai import AsyncOpenAI\nfrom classbeat.egress import allowlisted_async_client\n"
    "AsyncOpenAI(api_key=k, http_client=allowlisted_async_client(provider='deepseek'))",
    "from classbeat.egress import SPOTIFY_ALLOWLIST\nSPOTIFY_ALLOWLIST.check_url(u)",
]


@pytest.mark.parametrize("source", ALLOWED)
def test_the_guard_allows(source: str) -> None:
    assert scan_source(source, M) == []


def test_a_relative_import_of_an_allowlist_is_refused_too() -> None:
    source = "from ..egress import Allowlist\nAllowlist(frozenset({'evil.example'}))"
    assert scan_source(source, "classbeat.spotify.http") != []


def test_the_spotify_package_counts_as_worker_code() -> None:
    """ADR 0009 §6: refresh and the song-picking calls run in the worker, so the Spotify package
    may never import a web-only exempt module (OAuth sign-in, mail)."""
    assert "classbeat.spotify" in WORKER_PACKAGES


def test_an_exemption_is_per_module_and_per_call() -> None:
    call = "import httpx\nhttpx.AsyncClient()"
    assert scan_source(call, "classbeat.auth.oauth_client") == []
    assert scan_source(call, "classbeat.auth.routes") != []
    other = "import httpx\nhttpx.Client()"
    assert scan_source(other, "classbeat.auth.oauth_client") != []
