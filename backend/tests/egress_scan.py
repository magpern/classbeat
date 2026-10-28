"""Guard of ADR 0006 §9: outbound clients are built only in ``classbeat.egress``.

``scan_source(source, module)`` returns what ``module`` (a dotted name under ``classbeat``) may
not do outside ``classbeat.egress``:

- call anything that builds an HTTP client, an opener or a raw connection, or sends a request on
  its own: ``urllib.request.urlopen``, ``build_opener``, ``install_opener``, ``OpenerDirector``,
  the ``urllib`` handlers, ``httpx.Client``, ``AsyncClient``, the transports and the module-level
  ``httpx.get`` and friends, ``http.client`` connections, ``socket.socket`` and
  ``create_connection``, ``smtplib``, ``ftplib``, Authlib's httpx clients and the Anthropic and
  OpenAI SDK clients (those build their own httpx client);
- import a client library that bypasses both paths (``requests``, ``aiohttp``, ``urllib3``,
  ``httpcore``, ``http.client``, ``pycurl``, ``websockets``, ``ftplib``, ``telnetlib``), also
  through ``importlib.import_module`` or ``__import__`` with a constant name.

Names are resolved through imports and aliases (``import httpx as h``, ``from urllib import
request``, ``from httpx import AsyncClient as C``). Like the other guards this catches accidental
use and makes the rest visible in review; it cannot stop obfuscated code.

``EXEMPT`` lists the web-only call sites that predate the allowlist, each with its reason. They
run in the web process, which has no outbound allowlist yet (threat model T19). A worker or
agent module never imports them (``WORKER_PACKAGES``, checked by the test).
"""

import ast

EGRESS = "classbeat.egress"
# Packages that run in the worker: they may never import an exempt module.
WORKER_PACKAGES = ("classbeat.worker", "classbeat.agent", "classbeat.jobs")

_CALLS = frozenset(
    {
        "urllib.request.urlopen",
        "urllib.request.build_opener",
        "urllib.request.install_opener",
        "urllib.request.OpenerDirector",
        "urllib.request.HTTPHandler",
        "urllib.request.HTTPSHandler",
        "urllib.request.ProxyHandler",
        "urllib.request.FTPHandler",
        "urllib.request.FileHandler",
        "urllib.request.DataHandler",
        "urllib.request.URLopener",
        "urllib.request.FancyURLopener",
        "urllib.request.urlretrieve",
        "httpx.Client",
        "httpx.AsyncClient",
        "httpx.HTTPTransport",
        "httpx.AsyncHTTPTransport",
        "httpx.request",
        "httpx.stream",
        "httpx.get",
        "httpx.post",
        "httpx.put",
        "httpx.patch",
        "httpx.delete",
        "httpx.head",
        "httpx.options",
        "http.client.HTTPConnection",
        "http.client.HTTPSConnection",
        "socket.socket",
        "socket.create_connection",
        "socket.socketpair",
        "socket.fromfd",
        "smtplib.SMTP",
        "smtplib.SMTP_SSL",
        "smtplib.LMTP",
        "ftplib.FTP",
        "ftplib.FTP_TLS",
        "asyncio.open_connection",
        "asyncio.open_unix_connection",
        "authlib.integrations.httpx_client.AsyncOAuth2Client",
        "authlib.integrations.httpx_client.OAuth2Client",
        "authlib.integrations.httpx_client.AsyncOAuth1Client",
        "authlib.integrations.httpx_client.OAuth1Client",
        "anthropic.Anthropic",
        "anthropic.AsyncAnthropic",
        "anthropic.AnthropicBedrock",
        "anthropic.AsyncAnthropicBedrock",
        "anthropic.AnthropicVertex",
        "anthropic.AsyncAnthropicVertex",
        "anthropic.DefaultHttpxClient",
        "anthropic.DefaultAsyncHttpxClient",
        "openai.OpenAI",
        "openai.AsyncOpenAI",
    }
)
# The SDK clients are allowed when they are handed the allowlisted client (``http_client=``).
_SDK_CLIENTS = frozenset(
    {"anthropic.Anthropic", "anthropic.AsyncAnthropic", "openai.OpenAI", "openai.AsyncOpenAI"}
)
_MODULES = frozenset(
    {
        "requests",
        "aiohttp",
        "urllib3",
        "httpcore",
        "http.client",
        "pycurl",
        "websocket",
        "websockets",
        "ftplib",
        "telnetlib",
        "httpx2",
    }
)

# module -> calls it may make, with the reason.
EXEMPT: dict[str, dict[str, str]] = {
    "classbeat.auth.oauth_client": {
        "httpx.AsyncClient": "web only: the JWKS fetch of OAuth sign-in (ADR 0003 section 5)",
        "authlib.integrations.httpx_client.AsyncOAuth2Client": "web only: OAuth sign-in",
    },
    "classbeat.mail": {
        "smtplib.SMTP": "web only: account mail (ADR 0003 section 5)",
        "smtplib.SMTP_SSL": "web only: account mail",
    },
}


def _blocked_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in _MODULES)


def _resolve_from(node: ast.ImportFrom, module: str) -> str:
    if not node.level:
        return node.module or ""
    base = module.split(".")[: len(module.split(".")) - node.level]
    return ".".join([*base, node.module] if node.module else base)


def _aliases(tree: ast.AST, module: str) -> dict[str, str]:
    """Local name -> the dotted name it stands for, from every import in the file."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom):
            source = _resolve_from(node, module)
            for alias in node.names:
                names[alias.asname or alias.name] = f"{source}.{alias.name}"
    return names


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    head = aliases.get(node.id)
    if head is None:
        return None
    return ".".join([head, *reversed(parts)])


_AUTHLIB = "authlib.integrations.httpx_client."


def _canonical(name: str) -> str:
    # ``from urllib import request`` gives ``urllib.request.urlopen`` already; ``import
    # urllib.request`` binds ``urllib``, so ``urllib.request.urlopen`` too. Authlib re-exports.
    return name.replace("authlib.integrations.httpx_client.oauth2_client.", _AUTHLIB)


def _imports(tree: ast.AST, module: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            source = _resolve_from(node, module)
            found.append((node.lineno, source))
            found += [(node.lineno, f"{source}.{a.name}") for a in node.names]
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.append((node.lineno, first.value))
    return found


def scan_source(source: str, module: str) -> list[tuple[int, str]]:
    """``(line, why)`` for every outbound client ``module`` builds outside the allowlist."""
    if module == EGRESS:
        return []
    tree = ast.parse(source)
    aliases = _aliases(tree, module)
    exempt = EXEMPT.get(module, {})
    found: list[tuple[int, str]] = []
    for line, name in _imports(tree, module):
        if _blocked_module(name):
            found.append((line, f"imports {name}: outbound clients live in {EGRESS}"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func, aliases)
        if name is None:
            continue
        name = _canonical(name)
        if name not in _CALLS or name in exempt:
            continue
        if name in _SDK_CLIENTS and any(k.arg == "http_client" for k in node.keywords):
            continue
        found.append((node.lineno, f"calls {name}: outbound clients live in {EGRESS}"))
    return sorted(set(found))


def exempt_calls_used(source: str, module: str) -> set[str]:
    """The exempt calls ``module`` still makes (so a stale exemption is noticed)."""
    tree = ast.parse(source)
    aliases = _aliases(tree, module)
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func, aliases)
            if name is not None:
                used.add(_canonical(name))
    return used & set(EXEMPT.get(module, {}))


def imported_modules(source: str, module: str) -> set[str]:
    """Every dotted name ``module`` imports (and each ``from`` source)."""
    return {name for _, name in _imports(ast.parse(source), module)}
