"""Guards of the SwedeBeat gateway (ADR 0035 sections 1, 2, 7 and 9). Static checks over the
repository's own files, no network, no real key:

- the client key comes from a secret file only, sits on web and worker only in every compose file,
  is never an environment variable or a workflow secret, and is redacted from every log;
- the e2e stack mounts the public TEST key and names the fake gateway's https alias;
- classbeat names no gateway path but ``/auth/login``, ``/auth/session`` and ``/catalog/match``
  (no playlist write, publish, merge-publish, track download or catalogue search) and never the
  match's ``playlist`` mode;
- the test client builder is referenced nowhere in ``classbeat`` but its own module.
"""

import ast
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from classbeat.log import SECRET_KEYS, JsonFormatter, redact
from classbeat.swedebeat_gateway.key_policy import check_key
from classbeat.swedebeat_gateway.url_policy import gateway_origin

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src" / "classbeat"
NAME = "swedebeat_gateway_key"
E2E_KEY_FILE = "e2e/fake-gateway/client-key.txt"
E2E_URL = "https://gateway.classbeat-e2e.test"
ALLOWED_PATHS = frozenset({"/auth/login", "/auth/session", "/catalog/match"})
# The gateway's other routes (docs/api.md): writes, downloads, the search and the categories.
FORBIDDEN = ("/playlists", "/publish", "merge-publish", "/tracks/", "/catalog/search")
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


# --- The key: a file, on web and worker only ----------------------------------------------


@pytest.mark.parametrize("file", ["compose.yaml", "compose.prod.yaml"])
def test_the_key_is_a_secret_file_mounted_on_web_and_worker_only(file: str) -> None:
    config = compose(file)
    holders = sorted(n for n, s in config["services"].items() if NAME in mounts(s))
    assert holders == ["web", "worker"]
    assert config["secrets"][NAME] == {"file": f"./secrets/{NAME}"}


def test_the_e2e_stack_mounts_the_public_test_key_on_no_other_service() -> None:
    config = compose("compose.e2e.yaml")
    assert config["secrets"][NAME] == {"file": f"./{E2E_KEY_FILE}"}
    # compose.yaml's web and worker mount it; nothing the e2e file adds may (the second and third
    # webs, the fakes), and the e2e web and worker add no second mount of it.
    assert [n for n, s in config["services"].items() if NAME in mounts(s)] == []


def test_the_e2e_key_is_a_public_test_value_that_passes_the_rule() -> None:
    key = text(E2E_KEY_FILE).strip()
    assert "e2e" in key  # marked; never the owner's key
    assert "test" in key
    assert check_key(SecretStr(key)) is not None


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


@pytest.mark.parametrize("file", ["compose.yaml", "compose.prod.yaml", "compose.e2e.yaml"])
def test_no_service_has_the_key_or_its_file_in_its_environment(file: str) -> None:
    e2e_key = text(E2E_KEY_FILE).strip()
    for name, service in compose(file)["services"].items():
        env = service.get("environment") or {}
        items = env.items() if isinstance(env, dict) else (e.split("=", 1) for e in env)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        for variable, value in items:  # pyright: ignore[reportUnknownVariableType]
            assert "GATEWAY_KEY" not in str(variable).upper(), (file, name, variable)
            assert e2e_key not in str(value), (file, name, variable)


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


def test_no_workflow_or_image_holds_the_key() -> None:
    for path in (ROOT / ".github" / "workflows").glob("*.y*ml"):
        body = path.read_text(encoding="utf-8")
        assert "SB_CLIENT_API_KEY" not in body, path.name  # the owner's record, read by nothing
    dockerfile = text("Dockerfile")
    assert NAME not in dockerfile
    assert "SB_CLIENT_API_KEY" not in dockerfile
    assert "GATEWAY" not in dockerfile.upper()


def test_the_env_examples_never_hold_the_key() -> None:
    for example in (".env.example", "docs/runbooks/prod.env.example"):
        body = text(example)
        assert not re.search(r"^[^#]*GATEWAY_KEY", body, re.M), example


def test_the_init_script_creates_an_empty_placeholder() -> None:
    script = text("scripts/worktree.sh")
    assert NAME in script.split("SECRETS=(", 1)[1].split(")", 1)[0]
    assert f'"$name" == {NAME}' in script


# --- The key and the bearer are redacted --------------------------------------------------


def test_the_redaction_names_are_on_the_list() -> None:
    assert {"bearer", "x-api-key", NAME} <= SECRET_KEYS


def test_the_key_and_the_bearer_never_reach_a_log_line() -> None:
    key, bearer = "fake-gateway-client-key-Zq4mR8", "gAAAABfakeBearerValue0123456789"
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.gateway.redaction")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning(
            "Authorization: Bearer %s x-api-key: %s {'X-API-Key': '%s'} {\"bearer\": \"%s\"} "
            "swedebeat_gateway_key=%s",
            bearer,
            key,
            key,
            bearer,
            key,
            extra={"bearer": bearer, "headers": {"X-API-Key": key}, NAME: key},
        )
    finally:
        logger.removeHandler(handler)
    line = out.getvalue()
    assert key not in line
    assert bearer not in line
    assert json.loads(line)["bearer"] == "[redacted]"
    assert key not in redact(f"X-API-Key={key}")


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


def test_classbeat_names_no_other_gateway_route() -> None:
    found: list[tuple[str, str]] = []
    for module, tree in _sources():
        for value in _strings(tree):
            if any(fragment in value for fragment in FORBIDDEN):
                found.append((module, value))
            if "/catalog/" in value and value != "/catalog/match":
                found.append((module, value))
    assert found == []


def test_the_gateway_modules_and_their_callers_name_only_the_three_paths() -> None:
    gateway = [(m, t) for m, t in _sources() if _calls_the_gateway(m, t)]
    assert {m for m, _ in gateway} >= {
        "classbeat.swedebeat_gateway.client",
        "classbeat.egress_gateway",
        "classbeat.__main__",
        "classbeat.worker.__main__",
    }
    paths: set[str] = set()
    for module, tree in gateway:
        for value in _strings(tree):
            if module.startswith(GATEWAY_MODULES):
                assert "playlist" not in value.lower(), (module, value)  # never that match mode
            if value.startswith("/") and value != "/":
                paths.add(value)
    assert paths == ALLOWED_PATHS


def test_the_test_client_is_referenced_only_by_its_own_module() -> None:
    for module, tree in _sources():
        if module == "classbeat.egress_gateway":
            continue
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert "swedebeat_gateway_test_client" not in names, module


def test_the_gateway_modules_carry_no_swedebeat_host_or_secret_name() -> None:
    for module, tree in _sources():
        if not module.startswith(GATEWAY_MODULES):
            continue
        for value in _strings(tree):
            assert "swedebeat.se" not in value, module
            assert "swedebeat_app" not in value, module
