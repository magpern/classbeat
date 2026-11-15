"""Guards of the SwedeBeat gateway (ADR 0035 sections 1, 7 and 9; ADR 0036 section 8). Static
checks over the
repository's own files, no network, no real key (the client key's own guards, section 2, are
``test_swedebeat_gateway_key_guard.py``'s):

- the e2e stack names the fake gateway's https alias, and web and worker alone get the URL;
- classbeat names no gateway path but ``/auth/login``, ``/auth/session``, ``/catalog/match`` and,
  since SR C2, ``GET /tracks/{reference}/download`` as that exact template (no playlist write,
  publish, merge-publish, other ``/tracks/`` path, ``preview`` or query, or catalogue search);
  never the match's ``playlist`` mode, and the gateway modules name no ``PATCH``, ``PUT`` or
  ``DELETE``. ``violations`` is the check; it is shown to fail on each forbidden shape;
- the test client builder is referenced nowhere in ``classbeat`` but its own module.
"""

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from classbeat.swedebeat_gateway.url_policy import gateway_origin

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src" / "classbeat"
NAME = "swedebeat_gateway_key"
E2E_URL = "https://gateway.classbeat-e2e.test"
DOWNLOAD_TEMPLATE = "/tracks/{reference}/download"  # ADR 0036 section 8: opened by SR C2
ALLOWED_PATHS = frozenset({"/auth/login", "/auth/session", "/catalog/match", DOWNLOAD_TEMPLATE})
# The gateway's other routes (docs/api.md): writes, the preview, the search and the categories.
# The write paths stay forbidden until C7 (ADR 0036 section 8); PATCH and the search for good.
FORBIDDEN = ("/playlists", "/publish", "merge-publish", "/catalog/search")
FORBIDDEN_METHODS = frozenset({"PATCH", "PUT", "DELETE"})
GATEWAY_MODULES = ("classbeat.swedebeat_gateway", "classbeat.egress_gateway")
# What can send a request to the gateway: the client module and the client builder's module.
SENDERS = ("classbeat.swedebeat_gateway.client", "classbeat.egress_gateway")

pytestmark = pytest.mark.skipif(
    not (ROOT / "compose.yaml").is_file(), reason="not a full checkout (no compose.yaml)"
)


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def compose(relative: str) -> dict[str, Any]:
    return yaml.safe_load(text(relative))


def mounts(service: dict[str, Any]) -> list[str]:
    return [s if isinstance(s, str) else s["source"] for s in service.get("secrets", [])]


# --- The URL: web and worker only ------------------------------------------------------------


def test_the_e2e_stack_names_the_fake_gateway_over_tls() -> None:
    config = compose("compose.e2e.yaml")
    for service in ("web", "worker"):
        env = config["services"][service]["environment"]
        assert env["CLASSBEAT_SWEDEBEAT_GATEWAY_URL"] == E2E_URL
    origin = gateway_origin(E2E_URL)
    assert (origin.scheme, origin.port) == ("https", 443)
    fake = config["services"]["fake-gateway"]
    assert fake["networks"]["default"]["aliases"] == [origin.host]
    assert "ports" not in fake  # reachable only inside the compose network
    assert NAME not in mounts(fake)


@pytest.mark.parametrize("file", ["compose.yaml", "compose.prod.yaml"])
def test_web_and_worker_get_the_url_from_the_one_variable(file: str) -> None:
    services = compose(file)["services"]
    for service in ("web", "worker"):
        env = services[service]["environment"]
        assert env["CLASSBEAT_SWEDEBEAT_GATEWAY_URL"] == "${SWEDEBEAT_GATEWAY_URL:-}"
    variable = "CLASSBEAT_SWEDEBEAT_GATEWAY_URL"
    holders = sorted(n for n, s in services.items() if variable in (s.get("environment") or {}))
    assert holders == ["web", "worker"]


def test_the_production_value_is_in_the_examples_never_in_code() -> None:
    prod_example = text("docs/runbooks/prod.env.example")
    assert "SWEDEBEAT_GATEWAY_URL=http://192.168.1.222:7355" in prod_example
    assert re.search(r"^SWEDEBEAT_GATEWAY_URL=$", text(".env.example"), re.M)
    for path in SRC.rglob("*.py"):
        assert "192.168.1.222" not in path.read_text(encoding="utf-8"), path


# --- No gateway path but the three ---------------------------------------------------------


def _module(path: Path) -> str:
    parts = list(path.relative_to(SRC.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _strings(tree: ast.AST) -> list[str]:
    """Every string constant that is not a docstring (f-string parts included)."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _sources() -> list[tuple[str, ast.Module]]:
    paths = sorted(SRC.rglob("*.py"))
    return [(_module(p), ast.parse(p.read_text(encoding="utf-8"))) for p in paths]


def _calls_the_gateway(module: str, tree: ast.Module) -> bool:
    """A gateway module, or one that can send through it: it imports the client or the builder."""
    if module.startswith(GATEWAY_MODULES):
        return True
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        if any(n.startswith(SENDERS) for n in names):
            return True
    return False


def violations(sources: list[tuple[str, ast.Module]]) -> list[tuple[str, str]]:
    """Every forbidden gateway string in ``sources`` (module, value): another route, another
    ``/tracks/`` path than the download's template, a query, the ``playlist`` mode or a write
    method in a gateway module, and any path a gateway module or caller names outside
    ``ALLOWED_PATHS``."""
    found: list[tuple[str, str]] = []
    for module, tree in sources:
        sender = _calls_the_gateway(module, tree)
        for value in _strings(tree):
            if any(fragment in value for fragment in FORBIDDEN):
                found.append((module, value))
            if "/catalog/" in value and value != "/catalog/match":
                found.append((module, value))
            if "/tracks/" in value and value != DOWNLOAD_TEMPLATE:
                found.append((module, value))
            if sender and "preview" in value.lower():  # the download's short preview
                found.append((module, value))
            if module.startswith(GATEWAY_MODULES):
                if "playlist" in value.lower():  # never that match mode, never a write
                    found.append((module, value))
                if value.strip().upper() in FORBIDDEN_METHODS:
                    found.append((module, value))
            if sender and value.startswith("/") and value != "/" and value not in ALLOWED_PATHS:
                found.append((module, value))
    return found


def test_classbeat_names_no_forbidden_gateway_string() -> None:
    assert violations(_sources()) == []


def test_the_gateway_modules_and_their_callers_name_exactly_the_four_paths() -> None:
    gateway = [(m, t) for m, t in _sources() if _calls_the_gateway(m, t)]
    assert {m for m, _ in gateway} >= {
        "classbeat.swedebeat_gateway.client",
        "classbeat.egress_gateway",
        "classbeat.__main__",
        "classbeat.worker.__main__",
    }
    paths = {
        value
        for _, tree in gateway
        for value in _strings(tree)
        if value.startswith("/") and value != "/"
    }
    assert paths == ALLOWED_PATHS


CLIENT = "classbeat.swedebeat_gateway.client"


@pytest.mark.parametrize(
    ("module", "source"),
    [
        (CLIENT, 'PATH = "/playlists"'),
        (CLIENT, 'PATH = "/playlists/{id}/publish"'),
        (CLIENT, 'PATH = "/playlists/merge-publish"'),
        (CLIENT, 'PATH = "/catalog/search"'),
        (CLIENT, 'PATH = "/catalog/categories"'),
        (CLIENT, 'PATH = "/tracks/{reference}/download?preview=true"'),
        (CLIENT, 'PARAMS = {"preview": "true"}'),
        (CLIENT, 'PATH = "/tracks/{reference}"'),
        (CLIENT, 'PATH = f"/tracks/{r}/download"'),  # the template only, never built otherwise
        (CLIENT, 'METHOD = "PATCH"'),
        (CLIENT, 'METHOD = "delete"'),
        (CLIENT, 'BODY = {"playlist": "x"}'),
        (CLIENT, 'PATH = "/auth/logout"'),
        ("classbeat.studio", 'from classbeat.swedebeat_gateway import client\nP = "/health"'),
        ("classbeat.anything", 'URL = "http://gw/playlists/1"'),
        ("classbeat.anything", 'P = "/tracks/x/download?preview=true"'),  # not a sender
        ("classbeat.anything", 'P = "/tracks/{reference}"'),
    ],
)
def test_the_guard_fails_on_a_forbidden_path_or_method(module: str, source: str) -> None:
    assert violations([(module, ast.parse(source))]) != []


@pytest.mark.parametrize(
    "source", ['PATH = "/tracks/{reference}/download"', 'P = "/catalog/match"', 'M = "GET"']
)
def test_the_guard_passes_the_allowed_paths(source: str) -> None:
    assert violations([(CLIENT, ast.parse(source))]) == []


def test_the_test_client_is_referenced_only_by_its_own_module() -> None:
    for module, tree in _sources():
        if module == "classbeat.egress_gateway":
            continue
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert "swedebeat_gateway_test_client" not in names, module
